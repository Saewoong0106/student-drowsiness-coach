"""Tk screen. Workers never touch widgets or share SQLite connections."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .records import RecordService, display_time, summary_text

SESSION_STATES = {"completed": "종료", "running": "진행 중 / 임시 기록", "interrupted": "중단"}
COACHING_STATES = {"pending": "대기", "running": "생성 중", "succeeded": "완료", "failed": "실패"}
MODES = {"규칙 기반 안내": "rule", "기본 KoGPT2": "kogpt2", "LoRA 모델": "lora"}
EVENT_TYPES = {"eye_closure_suspected": "눈 감김 의심", "head_down": "고개 숙임", "unobservable": "미측정", "break": "휴식"}


class RecordsApp:
    def __init__(self, root, db_path, adapter_path):
        self.root = root
        self.service = RecordService(db_path)
        self.adapter_path = Path(adapter_path)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coaching")
        self.future = None
        self.job_session = None
        self.selected_id = None
        self.rows = []
        root.title("Study Coach · 공부 기록과 코칭")
        root.geometry("1120x780")
        root.minsize(860, 640)
        style = ttk.Style(root)
        style.configure("Title.TLabel", font=("맑은 고딕", 19, "bold"))
        style.configure("Treeview", rowheight=29)
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="공부 기록과 다음 학습 계획", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="PC에 저장된 기록을 확인하고, 종료된 세션의 코칭을 생성하세요.").pack(anchor="w", pady=(6, 14))
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="기록 검색").pack(side="left")
        self.search = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.search, width=28)
        entry.pack(side="left", padx=8)
        self.filter = tk.StringVar(value="전체")
        ttk.Combobox(toolbar, textvariable=self.filter, values=["전체", *SESSION_STATES.values()], state="readonly", width=21).pack(side="left")
        ttk.Button(toolbar, text="새로고침", command=self.refresh).pack(side="right")
        self.search.trace_add("write", lambda *_: self.filter_rows())
        self.filter.trace_add("write", lambda *_: self.filter_rows())
        table = ttk.Frame(outer)
        table.pack(fill="x", pady=12)
        self.sessions = ttk.Treeview(table, columns=("date", "goal", "duration", "status"), show="headings", height=6, selectmode="browse")
        for key, label, width in [("date", "시작 시각", 200), ("goal", "학습 목표", 330), ("duration", "공부 시간", 100), ("status", "세션 상태", 180)]:
            self.sessions.heading(key, text=label)
            self.sessions.column(key, width=width)
        scroll = ttk.Scrollbar(table, command=self.sessions.yview)
        self.sessions.configure(yscrollcommand=scroll.set)
        self.sessions.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")
        self.sessions.bind("<<TreeviewSelect>>", self.select_session)
        self.count = tk.StringVar()
        ttk.Label(outer, textvariable=self.count).pack(anchor="w")
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=10)
        self.mode = tk.StringVar(value="규칙 기반 안내")
        ttk.Label(actions, text="코칭 방식").pack(side="left")
        ttk.Combobox(actions, textvariable=self.mode, values=list(MODES), state="readonly", width=18).pack(side="left", padx=8)
        self.generate_button = ttk.Button(actions, text="코칭 생성", command=self.generate, state="disabled")
        self.generate_button.pack(side="left")
        self.export_button = ttk.Button(actions, text="JSON / CSV 내보내기", command=self.export, state="disabled")
        self.export_button.pack(side="right")
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.status = tk.StringVar(value="")
        status_label = ttk.Label(footer, textvariable=self.status, wraplength=800)
        status_label.pack(anchor="w")
        footer.bind("<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width)))
        ttk.Label(footer, text="공부 시작: study_session.py  ·  이 화면은 카메라를 열지 않습니다.").pack(anchor="w", pady=(6, 0))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        self.summary = self.text_tab(tabs, "기록 요약")
        self.coaching = self.text_tab(tabs, "코칭 결과")
        event_frame = ttk.Frame(tabs)
        tabs.add(event_frame, text="이벤트")
        self.events = ttk.Treeview(event_frame, columns=("type", "start", "confirmed", "end", "reason"), show="headings", selectmode="browse")
        for key, title in [("type", "종류"), ("start", "시작(초)"), ("confirmed", "확정(초)"), ("end", "종료(초)"), ("reason", "중단 사유")]:
            self.events.heading(key, text=title)
            self.events.column(key, width=140, minwidth=75)
        event_scroll = ttk.Scrollbar(event_frame, command=self.events.yview)
        self.events.configure(yscrollcommand=event_scroll.set)
        self.events.pack(side="left", fill="both", expand=True)
        event_scroll.pack(side="right", fill="y")
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F5>", lambda _: self.refresh())
        self.refresh()

    def text_tab(self, tabs, title):
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=title)
        widget = ScrolledText(frame, wrap="word", font=("맑은 고딕", 11), padx=16, pady=14, height=10, state="disabled")
        widget.pack(fill="both", expand=True)
        return widget

    @staticmethod
    def set_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def refresh(self):
        try:
            self.rows = self.service.list_sessions()
            self.filter_rows()
            self.status.set("기록을 불러왔습니다." if self.rows else "아직 기록이 없습니다. 공부 세션을 저장한 뒤 새로고침하세요.")
        except Exception as exc:
            self.rows = []
            self.filter_rows()
            self.status.set(f"기록 조회 실패: {exc}")

    def filter_rows(self):
        previous = self.selected_id
        query = self.search.get().strip().casefold()
        for item in self.sessions.get_children():
            self.sessions.delete(item)
        for row in self.rows:
            state = SESSION_STATES.get(row["status"], row["status"])
            if self.filter.get() not in ("전체", state):
                continue
            if query not in f"{row['session_id']} {row.get('goal', '')} {row.get('started_at', '')}".casefold():
                continue
            self.sessions.insert("", "end", iid=row["session_id"], values=(display_time(row.get("started_at")), row.get("goal") or "일반 학습", f"{row['study_seconds'] / 60:.1f}분", state))
        visible = self.sessions.get_children()
        self.count.set(f"표시 {len(visible)}개 / 전체 {len(self.rows)}개")
        if visible:
            target = previous if previous in visible else visible[0]
            self.sessions.selection_set(target)
            self.sessions.focus(target)
            self.select_session()
        else:
            self.clear_detail()

    def clear_detail(self):
        self.selected_id = None
        self.set_text(self.summary, "표시할 기록이 없습니다.")
        self.set_text(self.coaching, "세션을 선택하면 저장된 코칭을 확인할 수 있습니다.")
        for item in self.events.get_children():
            self.events.delete(item)
        self.generate_button.configure(state="disabled")
        self.export_button.configure(state="disabled")

    def select_session(self, _event=None):
        selection = self.sessions.selection()
        if not selection:
            self.clear_detail()
            return
        session_id = selection[0]
        try:
            summary, events, result = self.service.read(session_id)
            self.selected_id = session_id
            text = summary_text(summary)
            if summary["status"] != "completed":
                text = "진행 중이거나 중단된 임시 기록입니다. 코칭 생성은 정상 종료 후 가능합니다.\n\n" + text
            self.set_text(self.summary, text)
            self.set_text(self.coaching, self.coaching_text(result))
            for item in self.events.get_children():
                self.events.delete(item)
            for event in events:
                self.events.insert("", "end", values=(EVENT_TYPES.get(event.get("type"), event.get("type", "눈 감김 의심")), event["condition_started_s"], event["confirmed_s"], event.get("ended_s") if event.get("ended_s") is not None else "진행 중", event.get("interrupted_reason") or "—"))
            self.generate_button.configure(state="normal" if summary["status"] == "completed" and self.future is None else "disabled")
            self.export_button.configure(state="normal")
        except Exception as exc:
            self.clear_detail()
            self.status.set(f"상세 조회 실패: {exc}")

    @staticmethod
    def coaching_text(result):
        if result is None:
            return "아직 코칭을 생성하지 않았습니다.\n\n규칙 기반 안내는 모델 다운로드 없이 실행됩니다.\nKoGPT2 / LoRA는 별도 패키지와 모델이 필요합니다."
        state = COACHING_STATES.get(result["status"], result["status"])
        if result.get("is_fallback"):
            state += " · 규칙 기반 대체 안내"
        text = f"코칭 상태: {state}\n모델: {result['model_id']}\n\n{result.get('display_text') or '아직 표시할 결과가 없습니다.'}"
        if result["status"] in ("pending", "running"):
            text += "\n\n다른 창에서 생성 중이거나 이전 실행이 중단됐을 수 있습니다. 새로고침으로 확인하고, 작업 중인 창이 없다면 다시 생성하세요."
        if result.get("validation_errors"):
            text += "\n\n확인 사항: " + ", ".join(result["validation_errors"])
        if result.get("raw_text") and result["raw_text"] != result.get("display_text"):
            text += "\n\n검증 전 모델 원출력 (대체 안내와 구분):\n" + result["raw_text"]
        return text

    def generate(self):
        if self.future is not None or self.selected_id is None:
            return
        try:
            _, _, existing = self.service.read(self.selected_id)
            if existing is not None and not messagebox.askyesno("코칭 다시 생성", "기존 코칭 결과를 새 결과로 바꿀까요?", parent=self.root):
                return
            self.job_session = self.selected_id
            self.future = self.executor.submit(self.service.generate, self.job_session, mode=MODES[self.mode.get()], adapter_path=self.adapter_path)
            self.generate_button.configure(state="disabled")
            self.set_text(self.coaching, "코칭 상태: 대기 / 생성 중\n\n다른 세션을 조회해도 결과는 요청한 세션에 저장됩니다.")
            self.status.set("코칭을 생성 중입니다. 모델 첫 로딩에는 시간이 걸릴 수 있습니다.")
            self.root.after(150, self.poll)
        except Exception as exc:
            self.status.set(f"코칭 요청 실패: {exc}")

    def poll(self):
        if not self.future.done():
            self.root.after(150, self.poll)
            return
        try:
            result = self.future.result()
            self.status.set(f"세션 {self.job_session}: {COACHING_STATES[result.status]}" + (" · 규칙 기반 대체 안내 저장" if result.is_fallback else " · 저장 완료"))
        except Exception as exc:
            self.status.set(f"코칭 생성·저장 실패: {exc}")
        self.future = None
        self.job_session = None
        # Always redraw the CURRENT selection, never the job's original selection.
        self.select_session()

    def export(self):
        if self.selected_id is None:
            return
        destination = filedialog.askdirectory(title="기록 내보내기 폴더", parent=self.root)
        if not destination:
            return
        try:
            targets = [Path(destination) / f"{self.selected_id}.json", Path(destination) / f"{self.selected_id}-events.csv"]
            if any(p.exists() for p in targets) and not messagebox.askyesno("파일 덮어쓰기", "같은 세션의 내보내기 파일을 덮어쓸까요?", parent=self.root):
                return
            paths = self.service.export(self.selected_id, destination)
            self.status.set("내보내기 완료: " + ", ".join(str(p) for p in paths))
        except Exception as exc:
            self.status.set(f"내보내기 실패: {exc}")

    def close(self):
        if self.future is not None:
            messagebox.showinfo("코칭 생성 중", "결과 저장이 끝난 후 창을 닫아 주세요.", parent=self.root)
            return
        self.executor.shutdown(wait=False)
        self.root.destroy()
