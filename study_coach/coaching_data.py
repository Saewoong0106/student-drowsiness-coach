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
    generate_rule_coaching_text,
    validate_coaching_text,
)
from .contracts import SCHEMA_VERSION, SessionSummary

IGNORE_INDEX = -100
SYNTHETIC_SOURCES = ("synthetic_seed", "synthetic_auto", "rule_bootstrap")
REVIEWED_SOURCES = ("team_authored", "human_reviewed", "human_independent")


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
        scenario_to_groups[primary_scenario].append(gid)

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
    goals = ("자료구조 복습", "운영체제 정리", "임베디드 실습 예습", "알고리즘 문제 풀이")
    goal = goals[(group_idx + variant_idx) % len(goals)]
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
) -> list[CoachingRecord]:
    """Generate small synthetic seed records for pipeline testing (marked source='synthetic_seed', reviewed=False)."""
    if groups_per_scenario < 1 or variants_per_group < 1:
        raise ValueError("groups_per_scenario and variants_per_group must be >= 1")

    records: list[CoachingRecord] = []
    for scenario in VALID_SCENARIOS:
        for group_idx in range(1, groups_per_scenario + 1):
            group_id = f"grp-{scenario}-{group_idx:02d}"
            for variant_idx in range(1, variants_per_group + 1):
                summary = _scenario_template_summary(scenario, group_idx, variant_idx)
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


def evaluate_coaching_models(
    records: Iterable[CoachingRecord | dict],
    coaches: dict[str, object],
) -> dict:
    """Compare multiple coaches (e.g., base, lora, rule) on the same evaluation records."""
    typed_records = [
        item if isinstance(item, CoachingRecord) else CoachingRecord.from_dict(item)
        for item in records
    ]
    if not typed_records:
        raise ValueError("Evaluation requires at least one CoachingRecord")

    model_metrics = {}
    detailed_rows = []

    for rec in typed_records:
        summary = rec.summary()
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
            row_outputs[name] = {
                "result": result.to_dict(),
                "raw_validation_errors": raw_errors,
                "numeric_faithful": len(numeric_errors) == 0 and bool(result.raw_text.strip()),
                "valid_raw_response": len(raw_errors) == 0 and not result.is_fallback,
                "has_forbidden_claim": len(forbidden_errors) > 0,
            }
        detailed_rows.append(
            {
                "record_id": rec.id,
                "session_id": summary.session_id,
                "scenario": rec.scenario,
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
            "valid_response_count": valid_cnt,
            "valid_response_rate": valid_cnt / total,
            "numeric_faithfulness_count": numeric_cnt,
            "numeric_faithfulness_rate": numeric_cnt / total,
            "fallback_count": fallback_cnt,
            "fallback_rate": fallback_cnt / total,
            "forbidden_claim_count": forbidden_cnt,
            "forbidden_claim_rate": forbidden_cnt / total,
            "mean_elapsed_seconds": sum(elapsed_vals) / total if total else 0.0,
        }

    return {
        "total_records": total,
        "metrics": model_metrics,
        "cases": detailed_rows,
    }


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
