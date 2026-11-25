"""Dataset schema, validation, group-safe splitting, prompt loss masking, and model evaluation."""

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

from .coaching import (
    VALID_SCENARIOS,
    build_causal_lm_text,
    classify_scenario,
    coerce_summary,
    format_seconds_ko,
    generate_diverse_coaching_text,
    generate_rule_coaching_text,
    validate_coaching_text,
)
from .contracts import SCHEMA_VERSION, SessionSummary
from .storage import SessionStore

IGNORE_INDEX = -100
SYNTHETIC_SOURCES = (
    "synthetic_seed",
    "synthetic_auto",
    "synthetic_short_demo",
    "synthetic_short_demo_eval",
    "rule_bootstrap",
)
REVIEWED_SOURCES = ("team_authored", "human_reviewed", "human_independent")
SHORT_DEMO_PROFILES = ("none", "eye_only", "head_only", "both")
SHORT_DEMO_MIN_SECONDS = 30.0
SHORT_DEMO_MAX_SECONDS = 180.0


@dataclass(frozen=True)
class CoachingRecord:
    """Single coaching dataset record matching docs/interfaces.md."""

    id: str
    scenario: str
    group_id: str
    source: str
    reviewed: bool
    input: dict
    target: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id must be a non-empty string")
        if self.scenario not in VALID_SCENARIOS:
            raise ValueError(f"Invalid scenario: {self.scenario}")
        if not isinstance(self.group_id, str) or not self.group_id.strip():
            raise ValueError("group_id must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.reviewed, bool):
            raise ValueError("reviewed must be a boolean")
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be a non-empty string")
        summary = SessionSummary.from_dict(self.input)
        object.__setattr__(self, "input", summary.to_dict())

    @property
    def is_synthetic(self) -> bool:
        return self.source in SYNTHETIC_SOURCES or self.source.startswith("synthetic")

    def summary(self) -> SessionSummary:
        return SessionSummary.from_dict(self.input)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CoachingRecord":
        return cls(
            id=data["id"],
            scenario=data["scenario"],
            group_id=data["group_id"],
            source=data["source"],
            reviewed=data["reviewed"],
            input=data["input"],
            target=data["target"],
        )


def validate_coaching_record(
    record_or_dict: CoachingRecord | dict,
    *,
    require_reviewed: bool = False,
    check_scenario_match: bool = True,
) -> list[str]:
    """Validate a single coaching dataset record and return a list of error codes."""
    errors: list[str] = []
    data = record_or_dict.to_dict() if isinstance(record_or_dict, CoachingRecord) else dict(record_or_dict)

    required_keys = ("id", "scenario", "group_id", "source", "reviewed", "input", "target")
    for key in required_keys:
        if key not in data:
            errors.append(f"missing_field:{key}")
    if errors:
        return errors

    if not isinstance(data["id"], str) or not data["id"].strip():
        errors.append("invalid_id")
    if data["scenario"] not in VALID_SCENARIOS:
        errors.append(f"invalid_scenario:{data['scenario']}")
    if not isinstance(data["group_id"], str) or not data["group_id"].strip():
        errors.append("invalid_group_id")
    if not isinstance(data["source"], str) or not data["source"].strip():
        errors.append("invalid_source")
    if not isinstance(data["reviewed"], bool):
        errors.append("invalid_reviewed_flag")

    source_str = str(data.get("source", ""))
    is_synth = source_str in SYNTHETIC_SOURCES or source_str.startswith("synthetic")
    if is_synth and data.get("reviewed") is True:
        errors.append("synthetic_record_marked_reviewed")
    if require_reviewed and (not data.get("reviewed") or is_synth):
        errors.append("unreviewed_or_synthetic_record")

    try:
        summary = SessionSummary.from_dict(data["input"])
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid_input_summary:{exc}")
        return errors

    if check_scenario_match and data["scenario"] in VALID_SCENARIOS:
        inferred = classify_scenario(summary)
        if inferred != data["scenario"]:
            errors.append(f"scenario_mismatch:expected_{data['scenario']}_got_{inferred}")

    target_errors = validate_coaching_text(summary, str(data.get("target", "")))
    for err in target_errors:
        errors.append(f"target_{err}")

    return errors


def validate_coaching_dataset(
    records: Iterable[CoachingRecord | dict],
    *,
    require_reviewed: bool = False,
    check_scenario_match: bool = True,
) -> dict:
    """Validate a collection of coaching records and summarize counts, sources, and errors."""
    seen_ids: set[str] = set()
    scenario_counts: dict[str, int] = defaultdict(int)
    source_counts: dict[str, int] = defaultdict(int)
    groups_by_scenario: dict[str, set[str]] = defaultdict(set)
    record_errors: dict[str, list[str]] = {}
    total = 0
    reviewed_count = 0
    synthetic_count = 0

    for idx, item in enumerate(records):
        total += 1
        data = item.to_dict() if isinstance(item, CoachingRecord) else dict(item)
        rec_id = str(data.get("id") or f"row_{idx}")
        errs = validate_coaching_record(
            data,
            require_reviewed=require_reviewed,
            check_scenario_match=check_scenario_match,
        )
        if rec_id in seen_ids:
            errs.append("duplicate_id")
        seen_ids.add(rec_id)

        scenario = str(data.get("scenario", "unknown"))
        group_id = str(data.get("group_id", ""))
        source = str(data.get("source", "unknown"))
        scenario_counts[scenario] += 1
        source_counts[source] += 1
        if group_id:
            groups_by_scenario[scenario].add(group_id)
        if data.get("reviewed") is True:
            reviewed_count += 1
        if source in SYNTHETIC_SOURCES or source.startswith("synthetic"):
            synthetic_count += 1
        if errs:
            record_errors[rec_id] = errs

    return {
        "valid": len(record_errors) == 0 and total > 0,
        "total_records": total,
        "valid_records": total - len(record_errors),
        "invalid_records": len(record_errors),
        "reviewed_records": reviewed_count,
        "synthetic_records": synthetic_count,
        "scenario_counts": dict(scenario_counts),
        "group_counts_by_scenario": {k: len(v) for k, v in groups_by_scenario.items()},
        "source_counts": dict(source_counts),
        "errors": record_errors,
    }


def load_coaching_jsonl(path: str | Path) -> list[CoachingRecord]:
    """Load CoachingRecord items from a UTF-8 JSONL file."""
    file_path = Path(path)
    records: list[CoachingRecord] = []
    for line_no, raw_line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
            records.append(CoachingRecord.from_dict(payload))
        except Exception as exc:
            raise ValueError(f"{file_path}:{line_no}: {exc}") from exc
    return records


def save_coaching_jsonl(records: Iterable[CoachingRecord | dict], path: str | Path) -> Path:
    """Write CoachingRecord items to a UTF-8 JSONL file."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in records:
        payload = item.to_dict() if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item).to_dict()
        lines.append(json.dumps(payload, ensure_ascii=False))
    file_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return file_path


def verify_no_group_leakage(splits: dict[str, list[CoachingRecord | dict]]) -> list[str]:
    """Verify that no group_id or record id appears in more than one split."""
    errors: list[str] = []
    split_groups: dict[str, set[str]] = {}
    split_ids: dict[str, set[str]] = {}
    for split_name, items in splits.items():
        groups = set()
        ids = set()
        for item in items:
            data = item.to_dict() if isinstance(item, CoachingRecord) else item
            groups.add(str(data["group_id"]))
            ids.add(str(data["id"]))
        split_groups[split_name] = groups
        split_ids[split_name] = ids

    names = list(splits.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            overlap_groups = sorted(split_groups[a] & split_groups[b])
            if overlap_groups:
                errors.append(f"group_leakage:{a}:{b}:{','.join(overlap_groups)}")
            overlap_ids = sorted(split_ids[a] & split_ids[b])
            if overlap_ids:
                errors.append(f"id_leakage:{a}:{b}:{','.join(overlap_ids)}")
    return errors


def split_coaching_records(
    records: Iterable[CoachingRecord | dict],
    *,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
) -> dict[str, list[CoachingRecord]]:
    """Split coaching records by group_id per scenario so derived variants never cross splits."""
    if abs((train_ratio + val_ratio + test_ratio) - 1.0) > 1e-6:
        raise ValueError("train_ratio + val_ratio + test_ratio must equal 1.0")

    typed_records = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in records
    ]

    # Ensure each group_id is assigned to a single canonical bucket even if scenarios vary
    groups: dict[str, list[CoachingRecord]] = defaultdict(list)
    for rec in typed_records:
        groups[rec.group_id].append(rec)

    scenario_to_groups: dict[str, list[str]] = defaultdict(list)
    for gid, items in groups.items():
        primary_scenario = items[0].scenario
        bucket_key = primary_scenario
        if gid.startswith("grp-short_demo-"):
            parts = gid.split("-")
            if len(parts) >= 4 and parts[2] in SHORT_DEMO_PROFILES:
                bucket_key = f"{primary_scenario}:short_demo:{parts[2]}"
        scenario_to_groups[bucket_key].append(gid)

    splits: dict[str, list[CoachingRecord]] = {"train": [], "val": [], "test": []}

    for scenario in sorted(scenario_to_groups.keys()):
        gids = sorted(
            scenario_to_groups[scenario],
            key=lambda g: hashlib.sha256(g.encode("utf-8")).hexdigest(),
        )
        n_groups = len(gids)
        if n_groups == 1:
            train_gids, val_gids, test_gids = gids, [], []
        elif n_groups == 2:
            train_gids, val_gids, test_gids = [gids[0]], [], [gids[1]]
        else:
            n_test = max(1, int(round(n_groups * test_ratio))) if test_ratio > 0 else 0
            n_val = max(1, int(round(n_groups * val_ratio))) if val_ratio > 0 else 0
            if n_test + n_val >= n_groups:
                n_test = 1 if test_ratio > 0 else 0
                n_val = 1 if val_ratio > 0 and (n_groups - n_test) > 1 else 0
            n_train = n_groups - n_val - n_test
            train_gids = gids[:n_train]
            val_gids = gids[n_train : n_train + n_val]
            test_gids = gids[n_train + n_val :]

        for gid in train_gids:
            splits["train"].extend(groups[gid])
        for gid in val_gids:
            splits["val"].extend(groups[gid])
        for gid in test_gids:
            splits["test"].extend(groups[gid])

    leakage = verify_no_group_leakage(splits)
    if leakage:
        raise RuntimeError(f"Group leakage detected during split: {leakage}")
    return splits


def _scenario_template_summary(scenario: str, group_idx: int, variant_idx: int) -> SessionSummary:
    """Create a deterministic SessionSummary for a given (scenario, group_idx, variant_idx)."""
    session_id = f"seed-{scenario}-g{group_idx:02d}-v{variant_idx:02d}"
    goals = (
        "자료구조 복습",
        "운영체제 정리",
        "임베디드 실습 예습",
        "알고리즘 문제 풀이",
        "컴퓨터구조 파이프라인 정리",
        "네트워크 프로토콜 복습",
        "데이터베이스 SQL 실습",
        "디지털논리회로 설계",
        "신호및시스템 예제 풀이",
        "소프트웨어공학 설계서 작성",
        "인공지능 논문 읽기",
        "선형대수학 문제 풀이",
    )
    goal = goals[(group_idx * 3 + variant_idx) % len(goals)]
    offset_s = variant_idx * 60

    if scenario == "normal":
        study_s = 2400 + group_idx * 120 + offset_s
        obs_s = study_s - 60
        unobs_s = 60
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=unobs_s,
            eye_closure_count=1 if group_idx % 2 == 0 else 0,
            eye_closure_seconds=3.0 if group_idx % 2 == 0 else 0.0,
            head_down_count=1,
            windows=[
                {
                    "start_s": 0,
                    "end_s": study_s,
                    "observable_seconds": obs_s,
                    "eye_closure_count": 1 if group_idx % 2 == 0 else 0,
                }
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "short_session":
        study_s = 480 + group_idx * 60 + variant_idx * 30
        obs_s = study_s - 30
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=30,
            eye_closure_count=0,
            eye_closure_seconds=0.0,
            head_down_count=0,
            windows=[{"start_s": 0, "end_s": study_s, "observable_seconds": obs_s, "eye_closure_count": 0}],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "long_continuous":
        study_s = 4200 + group_idx * 300 + offset_s
        obs_s = study_s - 120
        mid = 2400
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=120,
            eye_closure_count=1,
            eye_closure_seconds=3.0,
            head_down_count=1,
            windows=[
                {"start_s": 0, "end_s": mid, "observable_seconds": mid - 60, "eye_closure_count": 1},
                {"start_s": mid, "end_s": study_s, "observable_seconds": study_s - mid - 60, "eye_closure_count": 0},
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "repeated_drowsiness":
        study_s = 2400 + group_idx * 180 + offset_s
        obs_s = study_s - 120
        eye_cnt = 4 + (group_idx % 3)
        eye_sec = float(eye_cnt * 5)
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=60,
            observable_seconds=obs_s,
            unobservable_seconds=120,
            eye_closure_count=eye_cnt,
            eye_closure_seconds=eye_sec,
            head_down_count=2,
            windows=[
                {"start_s": 0, "end_s": study_s, "observable_seconds": obs_s, "eye_closure_count": eye_cnt}
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "late_increase":
        study_s = 3600 + group_idx * 120 + offset_s
        obs_s = study_s - 120
        first_cnt = 1
        second_cnt = 3 + (group_idx % 2)
        total_cnt = first_cnt + second_cnt
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=120,
            eye_closure_count=total_cnt,
            eye_closure_seconds=float(total_cnt * 4),
            head_down_count=1,
            windows=[
                {"start_s": 0, "end_s": 2400, "observable_seconds": 2340, "eye_closure_count": first_cnt},
                {
                    "start_s": 2400,
                    "end_s": study_s,
                    "observable_seconds": study_s - 2400 - 60,
                    "eye_closure_count": second_cnt,
                },
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "head_down_focused":
        study_s = 2700 + group_idx * 120 + offset_s
        obs_s = study_s - 90
        head_cnt = 4 + (group_idx % 3)
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=90,
            eye_closure_count=1,
            eye_closure_seconds=2.5,
            head_down_count=head_cnt,
            windows=[
                {"start_s": 0, "end_s": study_s, "observable_seconds": obs_s, "eye_closure_count": 1}
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "with_break":
        study_s = 2700 + group_idx * 120 + offset_s
        obs_s = study_s - 60
        break_s = 300 + (group_idx % 3) * 60
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=break_s,
            observable_seconds=obs_s,
            unobservable_seconds=60,
            eye_closure_count=1,
            eye_closure_seconds=3.0,
            head_down_count=1,
            windows=[
                {"start_s": 0, "end_s": study_s, "observable_seconds": obs_s, "eye_closure_count": 1}
            ],
            schema_version=SCHEMA_VERSION,
        )

    if scenario == "low_observability":
        study_s = 1800 + group_idx * 120 + offset_s
        obs_s = 0.0 if group_idx == 1 else 600.0
        unobs_s = study_s - obs_s
        return SessionSummary(
            session_id=session_id,
            goal=goal,
            study_seconds=study_s,
            break_seconds=0,
            observable_seconds=obs_s,
            unobservable_seconds=unobs_s,
            eye_closure_count=0,
            eye_closure_seconds=0.0,
            head_down_count=0,
            windows=[
                {"start_s": 0, "end_s": study_s, "observable_seconds": obs_s, "eye_closure_count": 0}
            ],
            schema_version=SCHEMA_VERSION,
        )

    raise ValueError(f"Unsupported scenario: {scenario}")


def build_seed_coaching_records(
    *,
    groups_per_scenario: int = 3,
    variants_per_group: int = 2,
    diverse_templates: bool = True,
) -> list[CoachingRecord]:
    """Generate synthetic seed records across all 8 scenarios (marked source='synthetic_seed', reviewed=False)."""
    if groups_per_scenario < 1 or variants_per_group < 1:
        raise ValueError("groups_per_scenario and variants_per_group must be >= 1")

    records: list[CoachingRecord] = []
    for scenario in VALID_SCENARIOS:
        for group_idx in range(1, groups_per_scenario + 1):
            group_id = f"grp-{scenario}-{group_idx:02d}"
            for variant_idx in range(1, variants_per_group + 1):
                summary = _scenario_template_summary(scenario, group_idx, variant_idx)
                if diverse_templates:
                    style_idx = (group_idx - 1) * variants_per_group + (variant_idx - 1)
                    target = generate_diverse_coaching_text(summary, variant_index=style_idx)
                else:
                    target = generate_rule_coaching_text(summary)
                rec = CoachingRecord(
                    id=f"{scenario}-g{group_idx:02d}-v{variant_idx:02d}",
                    scenario=scenario,
                    group_id=group_id,
                    source="synthetic_seed",
                    reviewed=False,
                    input=summary.to_dict(),
                    target=target,
                )
                records.append(rec)
    return records


def classify_short_demo_profile(summary_or_dict: SessionSummary | dict) -> str:
    """Classify a session's event occurrence pattern into none / eye_only / head_only / both."""
    summary = coerce_summary(summary_or_dict)
    has_eye = summary.eye_closure_count > 0
    has_head = summary.head_down_count > 0
    if has_eye and has_head:
        return "both"
    if has_eye:
        return "eye_only"
    if has_head:
        return "head_only"
    return "none"


def _short_demo_template_summary(
    profile: str,
    group_idx: int,
    variant_idx: int,
    *,
    session_prefix: str = "demo",
) -> SessionSummary:
    """Build a deterministic 30s~180s SessionSummary covering all 4 event-combination profiles."""
    if profile not in SHORT_DEMO_PROFILES:
        raise ValueError(f"Unsupported short demo profile: {profile}")

    goals = (
        "임베디드 실습 점검",
        "자료구조 핵심 복습",
        "운영체제 개념 확인",
        "컴퓨터구조 요약",
        "알고리즘 워밍업",
        "디지털논리회로 복습",
        "네트워크 계층 정리",
        "데이터베이스 질의 연습",
    )
    goal = goals[(SHORT_DEMO_PROFILES.index(profile) * 3 + group_idx * 2 + variant_idx) % len(goals)]
    session_id = f"{session_prefix}-{profile}-g{group_idx:02d}-v{variant_idx:02d}"

    base_durations = {
        "none": (35.0, 75.0, 120.0, 155.0),
        "eye_only": (40.0, 80.0, 118.0, 150.0),
        "head_only": (38.0, 72.0, 112.0, 148.0),
        "both": (45.0, 86.0, 126.0, 154.0),
    }[profile]
    base_s = base_durations[(group_idx - 1) % len(base_durations)] + ((group_idx - 1) // len(base_durations)) * 7.0
    raw_study_s = base_s + (variant_idx - 1) * 11.0
    study_s = float(min(SHORT_DEMO_MAX_SECONDS, max(SHORT_DEMO_MIN_SECONDS, round(raw_study_s))))

    break_choices = (0.0, 5.0, 10.0, 15.0)
    break_s = break_choices[(group_idx + variant_idx - 2) % len(break_choices)]
    unobs_choices = (0.0, 2.0, 4.0, 6.0)
    unobs_s = min(float(int(study_s * 0.15)), unobs_choices[(group_idx * 2 + variant_idx) % len(unobs_choices)])
    obs_s = study_s - unobs_s

    if profile == "none":
        eye_cnt = 0
        eye_sec = 0.0
        head_cnt = 0
    elif profile == "eye_only":
        eye_specs = (
            (1, 2.5),
            (1, 4.0),
            (2, 6.0),
            (2, 11.0),
            (2, 14.0),
            (3, 10.0),
            (3, 16.0),
            (4, 15.0),
            (4, 21.0),
        )
        eye_cnt, raw_eye_sec = eye_specs[((group_idx - 1) * 3 + (variant_idx - 1)) % len(eye_specs)]
        eye_sec = min(float(raw_eye_sec), max(2.0, obs_s - 6.0))
        head_cnt = 0
    elif profile == "head_only":
        eye_cnt = 0
        eye_sec = 0.0
        head_choices = (1, 2, 3, 1, 2, 4, 2, 3, 4)
        head_cnt = head_choices[((group_idx - 1) * 3 + (variant_idx - 1)) % len(head_choices)]
    else:
        both_specs = (
            (1, 3.0, 1),
            (1, 4.0, 2),
            (2, 7.0, 1),
            (2, 10.0, 2),
            (2, 13.0, 3),
            (3, 11.0, 1),
            (3, 15.0, 2),
            (3, 18.0, 3),
            (4, 16.0, 2),
        )
        eye_cnt, raw_eye_sec, head_cnt = both_specs[((group_idx - 1) * 3 + (variant_idx - 1)) % len(both_specs)]
        eye_sec = min(float(raw_eye_sec), max(2.0, obs_s - 6.0))

    return SessionSummary(
        session_id=session_id,
        goal=goal,
        study_seconds=study_s,
        break_seconds=break_s,
        observable_seconds=obs_s,
        unobservable_seconds=unobs_s,
        eye_closure_count=eye_cnt,
        eye_closure_seconds=eye_sec,
        head_down_count=head_cnt,
        windows=[
            {
                "start_s": 0.0,
                "end_s": study_s,
                "observable_seconds": obs_s,
                "eye_closure_count": eye_cnt,
            }
        ],
        schema_version=SCHEMA_VERSION,
    )


def build_short_demo_coaching_records(
    *,
    groups_per_profile: int = 3,
    variants_per_group: int = 3,
    id_prefix: str = "short_demo",
    source: str = "synthetic_short_demo",
) -> list[CoachingRecord]:
    """Generate unreviewed 30s~3min short-session coaching records across none/eye_only/head_only/both."""
    if groups_per_profile < 1 or variants_per_group < 1:
        raise ValueError("groups_per_profile and variants_per_group must be >= 1")

    records: list[CoachingRecord] = []
    for profile in SHORT_DEMO_PROFILES:
        for group_idx in range(1, groups_per_profile + 1):
            group_id = f"grp-{id_prefix}-{profile}-{group_idx:02d}"
            for variant_idx in range(1, variants_per_group + 1):
                summary = _short_demo_template_summary(
                    profile,
                    group_idx,
                    variant_idx,
                    session_prefix="demo",
                )
                style_idx = (group_idx - 1) * variants_per_group + (variant_idx)
                target = generate_diverse_coaching_text(summary, variant_index=style_idx)
                records.append(
                    CoachingRecord(
                        id=f"{id_prefix}-{profile}-g{group_idx:02d}-v{variant_idx:02d}",
                        scenario=classify_scenario(summary),
                        group_id=group_id,
                        source=source,
                        reviewed=False,
                        input=summary.to_dict(),
                        target=target,
                    )
                )
    return records


def build_short_demo_eval_scenarios(*, cases_per_profile: int = 3) -> list[CoachingRecord]:
    """Generate held-out 30s~3min evaluation scenarios completely disjoint from training groups and inputs."""
    if cases_per_profile < 1:
        raise ValueError("cases_per_profile must be >= 1")

    eval_goals = (
        "마이크로프로세서 인터럽트 복습",
        "임베디드 타이머 제어 실습",
        "RTOS 스케줄링 정리",
        "센서 인터페이스 회로 점검",
        "컴퓨터네트워크 패킷 분석",
        "시스템프로그래밍 소켓 실습",
        "컴파일러 구문 분석 예습",
        "암호학 해시 함수 정리",
    )
    eval_durations = {
        "none": (33.0, 68.0, 108.0, 162.0),
        "eye_only": (37.0, 74.0, 116.0, 168.0),
        "head_only": (42.0, 83.0, 131.0, 173.0),
        "both": (48.0, 94.0, 142.0, 178.0),
    }
    eval_events = {
        "none": ((0, 0.0, 0), (0, 0.0, 0), (0, 0.0, 0), (0, 0.0, 0)),
        "eye_only": ((1, 3.0, 0), (2, 8.0, 0), (3, 13.0, 0), (2, 5.0, 0)),
        "head_only": ((0, 0.0, 1), (0, 0.0, 2), (0, 0.0, 3), (0, 0.0, 4)),
        "both": ((1, 3.0, 1), (2, 9.0, 1), (1, 5.0, 2), (3, 14.0, 2)),
    }

    records: list[CoachingRecord] = []
    for p_idx, profile in enumerate(SHORT_DEMO_PROFILES):
        for case_idx in range(1, cases_per_profile + 1):
            base_list = eval_durations[profile]
            raw_study = base_list[(case_idx - 1) % len(base_list)] + ((case_idx - 1) // len(base_list)) * 6.0
            study_s = float(min(SHORT_DEMO_MAX_SECONDS, max(SHORT_DEMO_MIN_SECONDS, raw_study)))
            unobs_s = float((case_idx % 3) * 3)
            obs_s = study_s - unobs_s
            break_s = float(6 * ((case_idx + p_idx) % 3))
            eye_cnt, raw_eye_sec, head_cnt = eval_events[profile][(case_idx - 1) % len(eval_events[profile])]
            eye_sec = min(float(raw_eye_sec), max(2.0, obs_s - 5.0)) if eye_cnt > 0 else 0.0
            goal = eval_goals[(p_idx * 2 + case_idx - 1) % len(eval_goals)]
            summary = SessionSummary(
                session_id=f"eval-demo-{profile}-{case_idx:02d}",
                goal=goal,
                study_seconds=study_s,
                break_seconds=break_s,
                observable_seconds=obs_s,
                unobservable_seconds=unobs_s,
                eye_closure_count=eye_cnt,
                eye_closure_seconds=eye_sec,
                head_down_count=head_cnt,
                windows=[
                    {
                        "start_s": 0.0,
                        "end_s": study_s,
                        "observable_seconds": obs_s,
                        "eye_closure_count": eye_cnt,
                    }
                ],
                schema_version=SCHEMA_VERSION,
            )
            target = generate_diverse_coaching_text(summary, variant_index=case_idx)
            records.append(
                CoachingRecord(
                    id=f"short_demo_eval-{profile}-{case_idx:02d}",
                    scenario=classify_scenario(summary),
                    group_id=f"grp-short_demo_eval-{profile}-{case_idx:02d}",
                    source="synthetic_short_demo_eval",
                    reviewed=False,
                    input=summary.to_dict(),
                    target=target,
                )
            )
    return records


def _summary_signature(summary: SessionSummary) -> tuple:
    return (
        summary.goal.strip(),
        round(summary.study_seconds, 3),
        round(summary.break_seconds, 3),
        round(summary.observable_seconds, 3),
        round(summary.unobservable_seconds, 3),
        int(summary.eye_closure_count),
        round(summary.eye_closure_seconds, 3),
        int(summary.head_down_count),
    )


def verify_no_dataset_leakage(
    reference_records: Iterable[CoachingRecord | dict],
    eval_records: Iterable[CoachingRecord | dict],
) -> list[str]:
    """Verify zero ID, group_id, session_id, or exact input signature leakage between train/val and eval sets."""
    ref_typed = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in reference_records
    ]
    eval_typed = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in eval_records
    ]
    errors = verify_no_group_leakage({"reference": ref_typed, "eval": eval_typed})

    ref_sessions = {rec.summary().session_id for rec in ref_typed}
    eval_sessions = {rec.summary().session_id for rec in eval_typed}
    overlap_sessions = sorted(ref_sessions & eval_sessions)
    if overlap_sessions:
        errors.append(f"session_id_leakage:{','.join(overlap_sessions)}")

    ref_sigs = {_summary_signature(rec.summary()) for rec in ref_typed}
    leaked_eval_ids = [rec.id for rec in eval_typed if _summary_signature(rec.summary()) in ref_sigs]
    if leaked_eval_ids:
        errors.append(f"exact_input_signature_leakage:{','.join(sorted(leaked_eval_ids))}")

    return errors


def merge_and_validate_coaching_datasets(
    named_datasets: dict[str, Iterable[CoachingRecord | dict]],
    *,
    require_reviewed: bool = False,
) -> tuple[list[CoachingRecord], dict]:
    """Merge multiple named coaching datasets while checking duplicates, group collisions, and numeric rules."""
    merged_raw: list[dict] = []
    origin_counts: dict[str, int] = {}
    short_profile_counts: dict[str, int] = defaultdict(int)
    seen_session_ids: dict[str, str] = {}
    group_origins: dict[str, str] = {}
    cross_errors: dict[str, list[str]] = defaultdict(list)

    for origin_name, items in named_datasets.items():
        count = 0
        for item in items:
            count += 1
            data = item.to_dict() if isinstance(item, CoachingRecord) else dict(item)
            rec_id = str(data.get("id") or f"{origin_name}_{count}")
            gid = str(data.get("group_id") or "")
            if gid:
                prev_origin = group_origins.get(gid)
                if prev_origin is not None and prev_origin != origin_name:
                    cross_errors[rec_id].append(f"cross_dataset_group_collision:{prev_origin}:{origin_name}:{gid}")
                else:
                    group_origins[gid] = origin_name
            inp = data.get("input")
            if isinstance(inp, dict) and "session_id" in inp:
                sid = str(inp["session_id"])
                if sid in seen_session_ids:
                    cross_errors[rec_id].append(f"duplicate_session_id:{sid}")
                else:
                    seen_session_ids[sid] = rec_id
                try:
                    summary = SessionSummary.from_dict(inp)
                    if SHORT_DEMO_MIN_SECONDS - 1e-6 <= summary.study_seconds <= SHORT_DEMO_MAX_SECONDS + 1e-6:
                        short_profile_counts[classify_short_demo_profile(summary)] += 1
                except Exception:
                    pass
            merged_raw.append(data)
        origin_counts[origin_name] = count

    report = validate_coaching_dataset(merged_raw, require_reviewed=require_reviewed)
    combined_errors = {k: list(v) for k, v in report["errors"].items()}
    for rec_id, errs in cross_errors.items():
        combined_errors.setdefault(rec_id, []).extend(errs)

    report["errors"] = combined_errors
    report["invalid_records"] = len(combined_errors)
    report["valid_records"] = report["total_records"] - len(combined_errors)
    report["valid"] = len(combined_errors) == 0 and report["total_records"] > 0
    report["origin_counts"] = origin_counts
    report["short_demo_profile_counts"] = dict(short_profile_counts)

    typed_records: list[CoachingRecord] = []
    if report["valid"]:
        typed_records = [CoachingRecord.from_dict(d) for d in merged_raw]
    return typed_records, report


def _normalize_raw_session_dict(raw: dict) -> SessionSummary:
    """Normalize a raw session snapshot dict from SQLite or exported JSON into a valid SessionSummary."""
    payload = dict(raw.get("summary", raw))
    study_s = max(0.0, float(payload.get("study_seconds", 0.0)))
    obs_s = max(0.0, min(study_s, float(payload.get("observable_seconds", 0.0))))
    unobs_s = max(0.0, float(payload.get("unobservable_seconds", max(0.0, study_s - obs_s))))
    if obs_s + unobs_s > study_s:
        unobs_s = max(0.0, study_s - obs_s)
    eye_cnt = max(0, int(payload.get("eye_closure_count", 0)))
    eye_sec = max(0.0, min(obs_s, float(payload.get("eye_closure_seconds", 0.0))))
    if eye_cnt == 0:
        eye_sec = 0.0
    elif eye_sec <= 0.0 and obs_s > 0.0:
        eye_sec = min(obs_s, float(eye_cnt * 2.0))
    elif eye_sec <= 0.0:
        eye_cnt = 0

    raw_windows = payload.get("windows") or []
    normalized_windows = []
    if raw_windows:
        for w in raw_windows:
            w_start = max(0.0, float(w.get("start_s", 0.0)))
            w_end = max(w_start, float(w.get("end_s", study_s)))
            w_obs = max(0.0, min(w_end - w_start, float(w.get("observable_seconds", obs_s))))
            normalized_windows.append(
                {
                    "start_s": w_start,
                    "end_s": w_end,
                    "observable_seconds": w_obs,
                    "eye_closure_count": max(0, int(w.get("eye_closure_count", eye_cnt))),
                }
            )
    elif study_s > 0:
        normalized_windows.append(
            {
                "start_s": 0.0,
                "end_s": study_s,
                "observable_seconds": obs_s,
                "eye_closure_count": eye_cnt,
            }
        )

    return SessionSummary(
        session_id=str(payload["session_id"]),
        goal=str(payload.get("goal") or "일반 학습"),
        study_seconds=study_s,
        break_seconds=max(0.0, float(payload.get("break_seconds", 0.0))),
        observable_seconds=obs_s,
        unobservable_seconds=unobs_s,
        eye_closure_count=eye_cnt,
        eye_closure_seconds=eye_sec,
        head_down_count=max(0, int(payload.get("head_down_count", 0))),
        windows=normalized_windows,
        schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
    )


def records_from_summaries(
    summaries: Iterable[SessionSummary | dict],
    *,
    source: str = "measured_session",
    reviewed: bool = False,
    variants_per_session: int = 1,
    id_prefix: str = "measured",
) -> list[CoachingRecord]:
    """Convert real measured SessionSummary items into CoachingRecord items with diverse coaching phrasing."""
    if variants_per_session < 1:
        raise ValueError("variants_per_session must be >= 1")

    records: list[CoachingRecord] = []
    for s_idx, item in enumerate(summaries, start=1):
        base_summary = item if isinstance(item, SessionSummary) else _normalize_raw_session_dict(item)
        scenario = classify_scenario(base_summary)
        short_sid = base_summary.session_id.split("-")[0][:8]
        group_id = f"grp-{id_prefix}-{short_sid}"
        for v_idx in range(1, variants_per_session + 1):
            style_idx = (s_idx - 1) * variants_per_session + (v_idx - 1)
            target = generate_diverse_coaching_text(base_summary, variant_index=style_idx)
            rec_id = (
                f"{id_prefix}-{short_sid}"
                if variants_per_session == 1
                else f"{id_prefix}-{short_sid}-v{v_idx:02d}"
            )
            records.append(
                CoachingRecord(
                    id=rec_id,
                    scenario=scenario,
                    group_id=group_id,
                    source=source,
                    reviewed=reviewed,
                    input=base_summary.to_dict(),
                    target=target,
                )
            )
    return records


def extract_coaching_records_from_db(
    db_path: str | Path,
    *,
    limit: int = 500,
    source: str = "measured_session",
    reviewed: bool = False,
    variants_per_session: int = 1,
) -> list[CoachingRecord]:
    """Extract CoachingRecord items from a local SQLite SessionStore database."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Database file not found: {path}")
    store = SessionStore(path)
    try:
        raw_sessions = store.list_sessions(limit=limit)
    finally:
        store.close()
    return records_from_summaries(
        raw_sessions,
        source=source,
        reviewed=reviewed,
        variants_per_session=variants_per_session,
        id_prefix="db",
    )


def extract_coaching_records_from_json(
    json_path_or_dir: str | Path,
    *,
    source: str = "measured_json",
    reviewed: bool = False,
    variants_per_session: int = 1,
) -> list[CoachingRecord]:
    """Extract CoachingRecord items from an exported session JSON file or directory of JSON files."""
    target_path = Path(json_path_or_dir)
    if not target_path.exists():
        raise FileNotFoundError(f"JSON path not found: {target_path}")

    json_files = [target_path] if target_path.is_file() else sorted(target_path.glob("*.json"))
    raw_items: list[dict] = []
    for jf in json_files:
        if jf.name in ("split_manifest.json", "blind_evaluation_sheet.json", "blind_answer_key.json", "evaluation_report.json", "training_metadata.json"):
            continue
        payload = json.loads(jf.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict) and ("session_id" in entry or "summary" in entry):
                    raw_items.append(entry)
        elif isinstance(payload, dict) and ("session_id" in payload or "summary" in payload):
            raw_items.append(payload)

    return records_from_summaries(
        raw_items,
        source=source,
        reviewed=reviewed,
        variants_per_session=variants_per_session,
        id_prefix="json",
    )


def mark_records_reviewed(
    records: Iterable[CoachingRecord | dict],
    *,
    reviewer: str = "human_reviewed",
) -> list[CoachingRecord]:
    """Promote coaching records to reviewed=True with a non-synthetic reviewer source."""
    clean_reviewer = reviewer.strip() or "human_reviewed"
    if clean_reviewer in SYNTHETIC_SOURCES or clean_reviewer.startswith("synthetic"):
        clean_reviewer = "human_reviewed"
    source_tag = clean_reviewer if clean_reviewer in REVIEWED_SOURCES else f"human_reviewed:{clean_reviewer}"

    updated: list[CoachingRecord] = []
    for item in records:
        rec = item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        updated.append(
            CoachingRecord(
                id=rec.id,
                scenario=rec.scenario,
                group_id=rec.group_id,
                source=source_tag,
                reviewed=True,
                input=rec.input,
                target=rec.target,
            )
        )
    return updated


def export_review_batches(
    records: Iterable[CoachingRecord | dict],
    output_dir: str | Path,
    *,
    assignees: tuple[str, ...] = ("Saewoong0106", "nisuhyo", "suyeongim320"),
) -> dict:
    """Partition coaching records by group_id across team reviewers and write JSONL + Markdown sheets."""
    if not assignees:
        raise ValueError("assignees must not be empty")
    typed_records = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in records
    ]
    groups: dict[str, list[CoachingRecord]] = defaultdict(list)
    for rec in typed_records:
        groups[rec.group_id].append(rec)

    sorted_gids = sorted(groups.keys())
    batches: dict[str, list[CoachingRecord]] = {a: [] for a in assignees}
    for idx, gid in enumerate(sorted_gids):
        owner = assignees[idx % len(assignees)]
        batches[owner].extend(groups[gid])

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    summary_info = {"total_records": len(typed_records), "assignees": {}}

    for owner, items in batches.items():
        jsonl_file = out_path / f"review_{owner}.jsonl"
        md_file = out_path / f"review_{owner}.md"
        save_coaching_jsonl(items, jsonl_file)

        md_lines = [
            f"# 코칭 데이터 검수 시트 — 담당: `{owner}` ({len(items)}건)",
            "",
            "- 각 문장이 세션 통계(공부/휴식/관측 시간, 눈 감김 의심 횟수, 고개 숙임 횟수)와 일치하는지 확인하세요.",
            "- 어색한 표현이 있다면 JSONL 파일의 `target` 문장을 수정한 뒤 아래 명령으로 검수 완료 처리합니다:",
            f"  ```sh\n  .venv/bin/python scripts/coaching_dataset.py --mark-reviewed {jsonl_file} --reviewer {owner} --output {out_path / f'reviewed_{owner}.jsonl'}\n  ```",
            "",
            "| # | ID | 시나리오 | 목표 | 공부/휴식/관측 | 눈감김/고개숙임 | 코칭 문장 (`target`) |",
            "|---|---|---|---|---|---|---|",
        ]
        for idx, rec in enumerate(items, start=1):
            s = rec.summary()
            stats_col = f"공부 {format_seconds_ko(s.study_seconds)} / 휴식 {format_seconds_ko(s.break_seconds)} / 관측 {format_seconds_ko(s.observable_seconds)}"
            event_col = f"눈 {s.eye_closure_count}회({int(round(s.eye_closure_seconds))}초) / 고개 {s.head_down_count}회"
            md_lines.append(
                f"| {idx} | `{rec.id}` | `{rec.scenario}` | {s.goal} | {stats_col} | {event_col} | {rec.target} |"
            )
        md_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        summary_info["assignees"][owner] = {
            "record_count": len(items),
            "group_count": len({r.group_id for r in items}),
            "jsonl_path": str(jsonl_file),
            "markdown_path": str(md_file),
        }

    manifest_file = out_path / "review_batches_manifest.json"
    manifest_file.write_text(json.dumps(summary_info, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_info


def encode_causal_lm_sample(
    record_or_summary: CoachingRecord | SessionSummary | dict,
    target_text: str | None = None,
    *,
    tokenizer=None,
    max_length: int = 512,
    eos_token: str = "</s>",
    pad_token_id: int = 3,
) -> dict:
    """Encode a coaching sample with prompt/padding loss masking (labels = -100 on prompt & padding)."""
    if isinstance(record_or_summary, CoachingRecord):
        summary = record_or_summary.summary()
        resolved_target = target_text if target_text is not None else record_or_summary.target
    elif isinstance(record_or_summary, dict) and "input" in record_or_summary and "target" in record_or_summary:
        summary = coerce_summary(record_or_summary["input"])
        resolved_target = target_text if target_text is not None else str(record_or_summary["target"])
    else:
        summary = coerce_summary(record_or_summary)
        if target_text is None:
            raise ValueError("target_text is required when passing a SessionSummary")
        resolved_target = target_text

    prompt_text, full_text = build_causal_lm_text(summary, resolved_target, eos_token=eos_token)
    target_with_eos = full_text[len(prompt_text):]

    if tokenizer is not None:
        prompt_ids = list(tokenizer.encode(prompt_text, add_special_tokens=False))
        target_ids = list(tokenizer.encode(target_with_eos, add_special_tokens=False))
        if getattr(tokenizer, "pad_token_id", None) is not None:
            pad_token_id = int(tokenizer.pad_token_id)
    else:
        # Lightweight subword/whitespace fallback tokenizer approximating BPE token counts without ML deps
        import re as _re

        def _fallback_encode(s: str) -> list[int]:
            tokens = _re.findall(r"[가-힣]{1,3}|[A-Za-z0-9_]+|[^\s가-힣A-Za-z0-9_]|\s+", s)
            return [(zlib_hash := sum(ord(c) * (idx + 1) for idx, c in enumerate(tok)) % 50000) + 10 for tok in tokens]

        prompt_ids = _fallback_encode(prompt_text)
        target_ids = _fallback_encode(target_with_eos)

    untruncated_len = len(prompt_ids) + len(target_ids)
    is_truncated = untruncated_len > max_length

    input_ids = (prompt_ids + target_ids)[:max_length]
    prompt_len = min(len(prompt_ids), len(input_ids))
    target_len = max(0, len(input_ids) - prompt_len)

    labels = ([IGNORE_INDEX] * prompt_len) + input_ids[prompt_len:]
    attention_mask = [1] * len(input_ids)

    return {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "labels": labels,
        "prompt_token_count": prompt_len,
        "target_token_count": target_len,
        "pad_token_id": pad_token_id,
        "is_truncated": is_truncated,
    }


def collate_causal_lm_batch(encoded_samples: list[dict], *, pad_token_id: int | None = None) -> dict:
    """Right-pad a batch of encoded causal LM samples, masking padded label positions with -100."""
    if not encoded_samples:
        raise ValueError("encoded_samples must not be empty")
    max_len = max(len(sample["input_ids"]) for sample in encoded_samples)
    resolved_pad = (
        pad_token_id
        if pad_token_id is not None
        else int(encoded_samples[0].get("pad_token_id", 0))
    )

    batch_input_ids = []
    batch_attention_mask = []
    batch_labels = []
    for sample in encoded_samples:
        cur_len = len(sample["input_ids"])
        pad_len = max_len - cur_len
        batch_input_ids.append(list(sample["input_ids"]) + [resolved_pad] * pad_len)
        batch_attention_mask.append(list(sample["attention_mask"]) + [0] * pad_len)
        batch_labels.append(list(sample["labels"]) + [IGNORE_INDEX] * pad_len)

    return {
        "input_ids": batch_input_ids,
        "attention_mask": batch_attention_mask,
        "labels": batch_labels,
    }


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    idx = min(len(ordered) - 1, max(0, int((len(ordered) * 0.95) + 0.999999) - 1))
    return ordered[idx]


def evaluate_coaching_models(
    records: Iterable[CoachingRecord | dict],
    coaches: dict[str, object],
    *,
    reference_records: Iterable[CoachingRecord | dict] | None = None,
) -> dict:
    """Compare multiple coaches (e.g., base, lora, rule) on the same evaluation records.

    Fallback responses (is_fallback=True or status!='succeeded') are strictly excluded
    from generation_success_count / valid_response_count.
    """
    typed_records = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in records
    ]
    if not typed_records:
        raise ValueError("Evaluation requires at least one CoachingRecord")

    leakage_errors: list[str] = []
    if reference_records is not None:
        leakage_errors = verify_no_dataset_leakage(reference_records, typed_records)
        if leakage_errors:
            raise ValueError(f"Data leakage detected between reference and evaluation records: {leakage_errors}")

    model_metrics = {}
    short_profile_metrics: dict[str, dict] = {}
    detailed_rows = []

    for rec in typed_records:
        summary = rec.summary()
        is_short_window = SHORT_DEMO_MIN_SECONDS - 1e-6 <= summary.study_seconds <= SHORT_DEMO_MAX_SECONDS + 1e-6
        short_profile = classify_short_demo_profile(summary) if is_short_window else None
        row_outputs = {}
        for name, coach in coaches.items():
            result = coach.generate(summary)
            raw_errors = validate_coaching_text(summary, result.raw_text) if result.raw_text else ["empty_response"]
            numeric_errors = [
                e
                for e in raw_errors
                if e
                in (
                    "mismatched_eye_closure_count",
                    "missing_eye_closure_count",
                    "mismatched_head_down_count",
                    "missing_head_down_count",
                    "zero_observable_missing_notice",
                )
            ]
            forbidden_errors = [e for e in raw_errors if e.startswith("forbidden_")]
            genuine_success = (
                result.status == "succeeded"
                and (not result.is_fallback)
                and len(raw_errors) == 0
            )
            row_outputs[name] = {
                "result": result.to_dict(),
                "raw_validation_errors": raw_errors,
                "numeric_faithful": len(numeric_errors) == 0 and bool(result.raw_text.strip()),
                "valid_raw_response": genuine_success,
                "generation_succeeded_without_fallback": genuine_success,
                "has_forbidden_claim": len(forbidden_errors) > 0,
            }
        detailed_rows.append(
            {
                "record_id": rec.id,
                "session_id": summary.session_id,
                "scenario": rec.scenario,
                "short_demo_profile": short_profile,
                "group_id": rec.group_id,
                "reference_target": rec.target,
                "outputs": row_outputs,
            }
        )

    total = len(typed_records)
    for name in coaches.keys():
        valid_cnt = sum(1 for r in detailed_rows if r["outputs"][name]["valid_raw_response"])
        numeric_cnt = sum(1 for r in detailed_rows if r["outputs"][name]["numeric_faithful"])
        fallback_cnt = sum(1 for r in detailed_rows if r["outputs"][name]["result"]["is_fallback"])
        forbidden_cnt = sum(1 for r in detailed_rows if r["outputs"][name]["has_forbidden_claim"])
        elapsed_vals = [
            float(r["outputs"][name]["result"]["elapsed_seconds"] or 0.0)
            for r in detailed_rows
        ]
        model_metrics[name] = {
            "total_records": total,
            "generation_success_count": valid_cnt,
            "generation_success_rate": valid_cnt / total,
            "valid_response_count": valid_cnt,
            "valid_response_rate": valid_cnt / total,
            "numeric_faithfulness_count": numeric_cnt,
            "numeric_faithfulness_rate": numeric_cnt / total,
            "fallback_count": fallback_cnt,
            "fallback_rate": fallback_cnt / total,
            "forbidden_claim_count": forbidden_cnt,
            "forbidden_claim_rate": forbidden_cnt / total,
            "mean_elapsed_seconds": sum(elapsed_vals) / total if total else 0.0,
            "max_elapsed_seconds": max(elapsed_vals) if elapsed_vals else 0.0,
            "p95_elapsed_seconds": _percentile_95(elapsed_vals),
        }

        short_rows = [r for r in detailed_rows if r["short_demo_profile"] is not None]
        if short_rows:
            per_prof = {}
            for prof in SHORT_DEMO_PROFILES:
                prof_rows = [r for r in short_rows if r["short_demo_profile"] == prof]
                if not prof_rows:
                    continue
                p_total = len(prof_rows)
                p_succ = sum(1 for r in prof_rows if r["outputs"][name]["valid_raw_response"])
                p_fb = sum(1 for r in prof_rows if r["outputs"][name]["result"]["is_fallback"])
                per_prof[prof] = {
                    "total": p_total,
                    "generation_success_count": p_succ,
                    "generation_success_rate": p_succ / p_total,
                    "fallback_count": p_fb,
                    "fallback_rate": p_fb / p_total,
                }
            short_profile_metrics[name] = per_prof

    report = {
        "total_records": total,
        "leakage_verified": reference_records is not None,
        "leakage_errors": leakage_errors,
        "metrics": model_metrics,
        "cases": detailed_rows,
    }
    if short_profile_metrics:
        report["short_demo_profile_metrics"] = short_profile_metrics
    return report


def _simulate_short_demo_study_session(run_index: int):
    """Simulate a realistic 30s~3min StudySession across the 5 final-demo rehearsal patterns."""
    from .session import Settings, StudySession

    rehearsal_specs = (
        {
            "profile": "none",
            "goal": "임베디드 인터럽트 개념 점검",
            "study_s": 45.0,
            "eye_windows": (),
            "head_windows": (),
            "pause_window": None,
        },
        {
            "profile": "eye_only",
            "goal": "운영체제 스케줄링 복습",
            "study_s": 65.0,
            "eye_windows": ((18.0, 21.0), (42.0, 46.0)),
            "head_windows": (),
            "pause_window": None,
        },
        {
            "profile": "head_only",
            "goal": "자료구조 트리 필기 정리",
            "study_s": 85.0,
            "eye_windows": (),
            "head_windows": ((20.0, 24.0), (55.0, 60.0)),
            "pause_window": None,
        },
        {
            "profile": "both",
            "goal": "컴퓨터구조 파이프라인 문제 풀이",
            "study_s": 115.0,
            "eye_windows": ((22.0, 25.0), (88.0, 91.5)),
            "head_windows": ((50.0, 54.5),),
            "pause_window": (65.0, 75.0),
        },
        {
            "profile": "both",
            "goal": "네트워크 소켓 통신 실습",
            "study_s": 150.0,
            "eye_windows": ((35.0, 39.0),),
            "head_windows": ((70.0, 74.0), (115.0, 120.0)),
            "pause_window": None,
        },
    )
    spec = rehearsal_specs[(run_index - 1) % len(rehearsal_specs)]
    session = StudySession(
        settings=Settings(calibration_seconds=10.0, calibration_samples=20),
        source="demo_rehearsal",
        goal=spec["goal"],
    )

    pause_win = spec["pause_window"]
    pause_span = (pause_win[1] - pause_win[0]) if pause_win else 0.0
    total_timeline_s = 10.0 + spec["study_s"] + pause_span
    steps = int(round(total_timeline_s * 4))
    paused_now = False
    alarm_count = 0

    for step_idx in range(steps + 1):
        t = round(step_idx * 0.25, 2)
        if pause_win is not None:
            if not paused_now and abs(t - pause_win[0]) < 1e-6:
                session.toggle_pause(t)
                paused_now = True
                continue
            if paused_now and abs(t - pause_win[1]) < 1e-6:
                session.toggle_pause(t)
                paused_now = False
                continue
        in_eye = t > 10.0 and any(ws <= t < we for ws, we in spec["eye_windows"])
        in_head = t > 10.0 and any(ws <= t < we for ws, we in spec["head_windows"])
        ear = 0.10 if in_eye else 0.30
        pitch = 25.0 if in_head else 2.0
        if session.step(t, ear, pitch, eye_valid=True, pose_valid=True):
            alarm_count += 1

    session.finish(round(total_timeline_s, 2), reason="user_exit")
    return session, spec["profile"], alarm_count


def verify_consecutive_demo_runs(
    *,
    coach=None,
    mode: str = "lora",
    adapter_path: str | Path | None = None,
    target_consecutive: int = 5,
    total_runs: int | None = None,
    temp_dir: str | Path | None = None,
) -> dict:
    """Verify Issue #12 end-to-end consecutive demo runs on an isolated temporary SQLite DB.

    A run counts as a success ONLY if StudySession -> SQLite -> RecordService.read ->
    RecordService.generate -> reopen/export all succeed AND CoachingResult has
    status=='succeeded', is_fallback==False, and 0 raw validation errors.
    """
    import tempfile
    from .records import RecordService

    if target_consecutive < 1:
        raise ValueError("target_consecutive must be >= 1")
    num_runs = total_runs if total_runs is not None else target_consecutive
    if num_runs < target_consecutive:
        raise ValueError("total_runs must be >= target_consecutive")

    def _run_in_folder(work_root: Path) -> dict:
        db_path = work_root / "rehearsal_demo.sqlite3"
        if db_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing database: {db_path}")
        export_dir = work_root / "exports"

        run_logs = []
        current_streak = 0
        max_streak = 0

        for run_idx in range(1, num_runs + 1):
            session, profile, alarm_count = _simulate_short_demo_study_session(run_idx)
            store = SessionStore(db_path)
            try:
                store.save(session)
            finally:
                store.close()

            service = RecordService(db_path)
            loaded_summary_dict, loaded_events, _ = service.read(session.session_id)
            expected_events = len(session.events)
            events_intact = len(loaded_events) == expected_events

            result = service.generate(
                session.session_id,
                mode=mode,
                adapter_path=adapter_path,
                coach=coach,
            )
            _, _, reopened_coaching = RecordService(db_path).read(session.session_id)
            exported_paths = service.export(session.session_id, export_dir)

            summary_obj = SessionSummary.from_dict(loaded_summary_dict)
            raw_errors = (
                validate_coaching_text(summary_obj, result.raw_text)
                if result.raw_text
                else ["empty_response"]
            )
            numeric_errors = [
                e
                for e in raw_errors
                if e
                in (
                    "mismatched_eye_closure_count",
                    "missing_eye_closure_count",
                    "mismatched_head_down_count",
                    "missing_head_down_count",
                    "zero_observable_missing_notice",
                )
            ]
            persisted_ok = (
                events_intact
                and reopened_coaching == result.to_dict()
                and all(p.is_file() for p in exported_paths)
                and result.matches_session(session.session_id)
            )
            # Fallback is strictly NOT counted as genuine LoRA generation success
            genuine_success = (
                persisted_ok
                and result.status == "succeeded"
                and (not result.is_fallback)
                and len(raw_errors) == 0
            )

            if genuine_success:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

            run_logs.append(
                {
                    "run_index": run_idx,
                    "session_id": session.session_id,
                    "profile": profile,
                    "study_seconds": summary_obj.study_seconds,
                    "break_seconds": summary_obj.break_seconds,
                    "eye_closure_count": summary_obj.eye_closure_count,
                    "head_down_count": summary_obj.head_down_count,
                    "alarm_count": alarm_count,
                    "events_persisted_intact": events_intact,
                    "coaching_status": result.status,
                    "is_fallback": result.is_fallback,
                    "raw_validation_errors": raw_errors,
                    "numeric_faithful": len(numeric_errors) == 0 and bool(result.raw_text.strip()),
                    "elapsed_seconds": float(result.elapsed_seconds or 0.0),
                    "genuine_generation_success": genuine_success,
                    "consecutive_streak_after_run": current_streak,
                }
            )

        succ_cnt = sum(1 for r in run_logs if r["genuine_generation_success"])
        num_cnt = sum(1 for r in run_logs if r["numeric_faithful"])
        fb_cnt = sum(1 for r in run_logs if r["is_fallback"])
        latencies = [r["elapsed_seconds"] for r in run_logs]

        return {
            "mode": mode,
            "target_consecutive": target_consecutive,
            "total_runs": num_runs,
            "max_consecutive_successes": max_streak,
            "consecutive_target_met": max_streak >= target_consecutive,
            "generation_success_count": succ_cnt,
            "generation_success_rate": succ_cnt / num_runs,
            "numeric_faithfulness_count": num_cnt,
            "numeric_faithfulness_rate": num_cnt / num_runs,
            "fallback_count": fb_cnt,
            "fallback_rate": fb_cnt / num_runs,
            "mean_elapsed_seconds": sum(latencies) / num_runs if latencies else 0.0,
            "max_elapsed_seconds": max(latencies) if latencies else 0.0,
            "p95_elapsed_seconds": _percentile_95(latencies),
            "runs": run_logs,
        }

    if temp_dir is not None:
        base_dir = Path(temp_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base_dir) as sub:
            return _run_in_folder(Path(sub))
    with tempfile.TemporaryDirectory() as sub:
        return _run_in_folder(Path(sub))


def build_blind_evaluation_sheet(evaluation_report: dict) -> tuple[list[dict], list[dict]]:
    """Build an anonymized blind evaluation sheet and a separate answer key from an evaluation report."""
    blind_rows = []
    answer_key = []
    labels = ("candidate_A", "candidate_B", "candidate_C", "candidate_D")

    for case in evaluation_report.get("cases", []):
        model_names = sorted(
            case["outputs"].keys(),
            key=lambda m: hashlib.sha256(f"{case['record_id']}:{m}".encode("utf-8")).hexdigest(),
        )
        candidates = {}
        mapping = {}
        for idx, m_name in enumerate(model_names):
            label = labels[idx] if idx < len(labels) else f"candidate_{idx + 1}"
            out_info = case["outputs"][m_name]["result"]
            candidates[label] = {
                "text": out_info["raw_text"] or out_info["display_text"],
                "faithfulness_score_1_to_5": None,
                "actionability_score_1_to_5": None,
                "readability_score_1_to_5": None,
            }
            mapping[label] = m_name

        blind_rows.append(
            {
                "record_id": case["record_id"],
                "session_id": case["session_id"],
                "scenario": case["scenario"],
                "candidates": candidates,
            }
        )
        answer_key.append(
            {
                "record_id": case["record_id"],
                "session_id": case["session_id"],
                "mapping": mapping,
            }
        )

    return blind_rows, answer_key
