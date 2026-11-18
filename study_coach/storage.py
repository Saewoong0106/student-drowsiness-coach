"""Local numerical records only; images and landmarks are never stored."""

import csv
import json
from pathlib import Path
import sqlite3


class SessionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS study_sessions (
                session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                status TEXT NOT NULL, summary_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS eye_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES study_sessions(session_id),
                event_json TEXT NOT NULL
            );
        """)

    def save(self, session):
        summary = session.snapshot()
        with self.connection:
            self.connection.execute("""INSERT INTO study_sessions VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET status=excluded.status, summary_json=excluded.summary_json""",
                (session.session_id, session.started_at, session.status, json.dumps(summary, ensure_ascii=False)))
            for event in session.event_rows():
                self.connection.execute("""INSERT INTO eye_events VALUES (?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET event_json=excluded.event_json""",
                    (event["event_id"], session.session_id, json.dumps(event, ensure_ascii=False)))

    def list_sessions(self, limit=20):
        rows = self.connection.execute("SELECT summary_json FROM study_sessions ORDER BY started_at DESC LIMIT ?", (limit,))
        return [json.loads(row[0]) for row in rows]

    def read(self, session_id):
        row = self.connection.execute("SELECT summary_json FROM study_sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        events = [json.loads(row[0]) for row in self.connection.execute(
            "SELECT event_json FROM eye_events WHERE session_id=?", (session_id,))]
        events.sort(key=lambda event: event["condition_started_s"])
        return json.loads(row[0]), events

    def export(self, session_id, destination):
        summary, events = self.read(session_id)
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        summary_path = destination / f"{session_id}.json"
        event_path = destination / f"{session_id}-events.csv"
        summary_path.write_text(json.dumps({"summary": summary, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
        fields = ["event_id", "session_id", "type", "condition_started_s", "confirmed_s", "ended_s", "interrupted_reason"]
        with event_path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(events)
        return summary_path, event_path

    def close(self):
        self.connection.close()
