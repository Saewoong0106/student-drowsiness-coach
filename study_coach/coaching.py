"""Coaching prompt formatting, rule baseline, KoGPT2/LoRA inference, and output validation."""

from dataclasses import replace
import json
from pathlib import Path
import re
import time
from typing import Callable

from .contracts import CoachingResult, SessionSummary

DEFAULT_BASE_MODEL_ID = "skt/kogpt2-base-v2"
DEFAULT_RULE_MODEL_ID = "rule-based-coach"
DEFAULT_MODEL_REVISION = "v0.1"
DEFAULT_DATASET_VERSION = "seed-v0.1"

PROMPT_HEADER = "### 세션 통계"
RESPONSE_HEADER = "### 코칭 결과\n"

VALID_SCENARIOS = (
    "normal",
    "short_session",
    "long_continuous",
    "repeated_drowsiness",
    "late_increase",
    "head_down_focused",
    "with_break",
    "low_observability",
)

FORBIDDEN_PATTERNS = (
    (re.compile(r"집중(?:도|력)\s*\d+\s*점"), "forbidden_concentration_score"),
    (re.compile(r"\d+\s*점\s*만점"), "forbidden_score_rating"),
    (re.compile(r"수면\s*(?:장애|질환|확정|진단)"), "forbidden_medical_diagnosis"),
    (re.compile(r"확실히\s*잠들"), "forbidden_sleep_certainty"),
)

_EYE_COUNT_RE = re.compile(r"(?:눈\s*감김(?:\s*의심)?|졸음(?:\s*의심)?)\s*(?:은|는|이|가)?\s*(\d+)\s*회")
_HEAD_COUNT_RE = re.compile(r"고개\s*숙임(?:\s*이벤트)?\s*(?:은|는|이|가)?\s*(\d+)\s*회")
_ACTION_KEYWORDS = (
    "휴식",
    "쉬",
    "조정",
    "유지",
    "점검",
    "계획",
    "스트레칭",
    "권장",
    "보세요",
    "보정",
    "환기",
    "나누어",
    "조절",
)


def coerce_summary(summary_or_dict: SessionSummary | dict) -> SessionSummary:
    """Return an isolated SessionSummary copy so coaching never mutates live session state."""
    if isinstance(summary_or_dict, SessionSummary):
        return replace(summary_or_dict, windows=[dict(w) for w in summary_or_dict.windows])
    if isinstance(summary_or_dict, dict):
        return SessionSummary.from_dict(summary_or_dict)
    raise TypeError("Expected SessionSummary or dict")


def format_seconds_ko(seconds: float) -> str:
    """Format seconds into readable Korean minutes/seconds."""
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    if minutes > 0 and secs > 0:
        return f"{minutes}분 {secs}초"
    if minutes > 0:
        return f"{minutes}분"
    return f"{secs}초"


def classify_scenario(summary_or_dict: SessionSummary | dict) -> str:
    """Classify a SessionSummary into one of the 8 canonical coaching scenarios."""
    summary = coerce_summary(summary_or_dict)
    study_s = summary.study_seconds
    obs_s = summary.observable_seconds
    obs_ratio = (obs_s / study_s) if study_s > 0 else 0.0

    if study_s <= 0 or obs_s <= 0 or obs_ratio < 0.6:
        return "low_observability"
    if study_s < 900:
        return "short_session"
    if len(summary.windows) >= 2:
        first_count = summary.windows[0].get("eye_closure_count", 0)
        last_count = summary.windows[-1].get("eye_closure_count", 0)
        if last_count >= max(2, first_count + 1):
            return "late_increase"
    if summary.eye_closure_count >= 3 or summary.eye_closure_seconds >= 15.0:
        return "repeated_drowsiness"
    if summary.head_down_count >= 3 and summary.head_down_count > summary.eye_closure_count:
        return "head_down_focused"
    if summary.break_seconds >= 180:
        return "with_break"
    if study_s >= 3600 and summary.break_seconds < 60:
        return "long_continuous"
    return "normal"


def format_coaching_prompt(summary_or_dict: SessionSummary | dict) -> str:
    """Serialize SessionSummary into a deterministic Korean prompt for KoGPT2."""
    summary = coerce_summary(summary_or_dict)
    goal_text = summary.goal.strip() if summary.goal and summary.goal.strip() else "일반 학습"
    scenario = classify_scenario(summary)
    if summary.observable_seconds > 0:
        closure_ratio = f"{(summary.eye_closure_seconds / summary.observable_seconds) * 100:.1f}%"
    else:
        closure_ratio = "측정 불가"

    window_parts = []
    for idx, window in enumerate(summary.windows, start=1):
        start_m = int(round(window["start_s"] / 60))
        end_m = int(round(window["end_s"] / 60))
        obs_w = format_seconds_ko(window["observable_seconds"])
        cnt_w = int(window["eye_closure_count"])
        window_parts.append(f"{idx}구간({start_m}~{end_m}분, 관측 {obs_w}): 눈 감김 {cnt_w}회")
    windows_line = " / ".join(window_parts) if window_parts else "구간 정보 없음"

    lines = [
        PROMPT_HEADER,
        f"세션 ID: {summary.session_id}",
        f"학습 목표: {goal_text}",
        f"시나리오 분류: {scenario}",
        (
            f"공부 시간: {format_seconds_ko(summary.study_seconds)} ({int(round(summary.study_seconds))}초) | "
            f"휴식 시간: {format_seconds_ko(summary.break_seconds)} ({int(round(summary.break_seconds))}초)"
        ),
        (
            f"관측 가능 시간: {format_seconds_ko(summary.observable_seconds)} ({int(round(summary.observable_seconds))}초) | "
            f"미측정 시간: {format_seconds_ko(summary.unobservable_seconds)} ({int(round(summary.unobservable_seconds))}초)"
        ),
        (
            f"눈 감김 의심: {summary.eye_closure_count}회 "
            f"(누적 {int(round(summary.eye_closure_seconds))}초, 관측 대비 {closure_ratio})"
        ),
        f"고개 숙임: {summary.head_down_count}회",
        f"시간창 요약: {windows_line}",
        RESPONSE_HEADER.rstrip("\n"),
    ]
    return "\n".join(lines) + "\n"


def build_causal_lm_text(summary_or_dict: SessionSummary | dict, target_text: str, eos_token: str = "</s>") -> tuple[str, str]:
    """Return (prompt_text, full_text) for causal LM supervised fine-tuning with prompt masking."""
    prompt = format_coaching_prompt(summary_or_dict)
    clean_target = target_text.strip()
    if eos_token and not clean_target.endswith(eos_token):
        clean_target = f"{clean_target}{eos_token}"
    return prompt, f"{prompt}{clean_target}"


def generate_rule_coaching_text(summary_or_dict: SessionSummary | dict) -> str:
    """Generate a deterministic 3-part Korean coaching message (facts, cautious interpretation, next action)."""
    summary = coerce_summary(summary_or_dict)
    scenario = classify_scenario(summary)
    study_ko = format_seconds_ko(summary.study_seconds)
    break_ko = format_seconds_ko(summary.break_seconds)
    obs_ko = format_seconds_ko(summary.observable_seconds)
    unobs_ko = format_seconds_ko(summary.unobservable_seconds)
    eye_cnt = summary.eye_closure_count
    eye_sec = int(round(summary.eye_closure_seconds))
    head_cnt = summary.head_down_count
    goal_prefix = f"'{summary.goal.strip()}' 세션에서 " if summary.goal and summary.goal.strip() else "이번 세션에서 "

    if summary.observable_seconds <= 0:
        fact = (
            f"{goal_prefix}총 공부 시간 {study_ko}(휴식 {break_ko}) 중 얼굴과 눈이 유효하게 관측된 시간이 0초여야 할 만큼 "
            f"미측정 구간({unobs_ko})이 전체를 차지해 관측 비율은 측정 불가로 기록되었습니다(눈 감김 의심 {eye_cnt}회, 고개 숙임 {head_cnt}회)."
        )
    else:
        fact = (
            f"{goal_prefix}공부 {study_ko}, 휴식 {break_ko} 동안 관측 가능 시간은 {obs_ko}(미측정 {unobs_ko})였으며, "
            f"눈 감김 의심 {eye_cnt}회(누적 {eye_sec}초)와 고개 숙임 {head_cnt}회가 기록되었습니다."
        )

    if scenario == "low_observability":
        interpretation = (
            "얼굴 가림·조명 역광 또는 카메라 각도 문제로 관측 가능 시간이 부족하여 졸음 여부를 단정하기 어렵습니다."
        )
        action = (
            "다음 공부 시작 전 카메라 높이와 조명을 점검해 정면 보정을 다시 수행하고, 25분 단위로 자세와 화면 표시를 확인해 보세요."
        )
    elif scenario == "short_session":
        interpretation = (
            "짧은 시간 동안의 초기 관측 기록이므로 장시간 피로 추세를 판단하기에는 표본이 적습니다."
        )
        action = (
            "다음에는 25~30분 단위로 목표 공부 시간을 설정하고 종료 후 5분간 짧은 휴식과 스트레칭을 계획해 보세요."
        )
    elif scenario == "late_increase":
        first_cnt = summary.windows[0]["eye_closure_count"] if summary.windows else 0
        last_cnt = summary.windows[-1]["eye_closure_count"] if summary.windows else eye_cnt
        interpretation = (
            f"초반 구간(눈 감김 {first_cnt}회)보다 후반 구간(눈 감김 {last_cnt}회)에 눈 감김 의심이 늘어 후반부 피로가 누적된 신호로 보입니다."
        )
        action = (
            "다음 공부를 시작하기 전에 10분간 눈을 감거나 먼 곳을 보며 휴식하고, 이후에는 30~40분마다 5분 휴식을 미리 넣어 구간을 나누어 보세요."
        )
    elif scenario == "repeated_drowsiness":
        interpretation = (
            "지속적인 눈 감김 의심 이벤트가 반복되어 현재 눈의 피로나 졸음 신호가 높은 상태로 해석됩니다."
        )
        action = (
            "지금 바로 10~15분간 자리에서 일어나 환기하거나 가벼운 스트레칭으로 휴식한 뒤, 다음 세션을 25분 단위로 짧게 조절해 보세요."
        )
    elif scenario == "head_down_focused":
        interpretation = (
            "눈 감김에 비해 고개 숙임 횟수가 많아 교재 필기나 문제 풀이로 고개를 자주 숙였을 가능성이 있으며, 목과 어깨에 부담이 쌓였을 수 있습니다."
        )
        action = (
            "필기 중에는 독서대나 거치대를 활용해 고개 각도를 완만하게 조정하고, 다음 공부 전 5분간 목·어깨 스트레칭으로 휴식해 보세요."
        )
    elif scenario == "with_break":
        interpretation = (
            "공부 중간에 휴식 시간을 함께 활용하여 연속 부담을 줄이면서 세션을 진행한 것으로 보입니다."
        )
        action = (
            "다음 세션에서도 40~50분 공부 후 5~10분 휴식 리듬을 유지하고, 눈의 피로가 느껴지면 가벼운 스트레칭을 병행해 보세요."
        )
    elif scenario == "long_continuous":
        interpretation = (
            "1시간 이상 거의 쉬지 않고 연속으로 공부하여 지금은 눈 감김이 적더라도 다음 구간에서 피로가 급격히 나타날 수 있습니다."
        )
        action = (
            "다음 공부를 이어가기 전에 최소 10분간 휴식하며 수분을 보충하고, 이후에는 45~50분마다 짧은 휴식 알람을 설정해 보세요."
        )
    else:
        interpretation = (
            "관측된 구간 동안 지속적인 눈 감김이나 급격한 자세 변화가 적게 나타나 비교적 안정적인 학습 흐름을 보였습니다."
        )
        action = (
            "현재 학습 자세를 유지하되 다음 세션을 시작하기 전 5분간 눈과 목을 가볍게 쉬게 한 뒤 계획한 분량을 이어가 보세요."
        )

    return f"{fact} {interpretation} {action}"


def validate_coaching_text(summary_or_dict: SessionSummary | dict, text: str) -> list[str]:
    """Validate raw model coaching text against SessionSummary faithfulness and safety rules."""
    summary = coerce_summary(summary_or_dict)
    errors: list[str] = []

    if not isinstance(text, str) or not text.strip():
        return ["empty_response"]

    cleaned = text.strip()
    if len(cleaned) < 25:
        errors.append("response_too_short")

    for pattern, code in FORBIDDEN_PATTERNS:
        if pattern.search(cleaned):
            errors.append(code)

    # Check zero-observability rule
    if summary.observable_seconds <= 0:
        if not any(token in cleaned for token in ("측정 불가", "관측 불가", "관측되지", "관측된 시간이 0", "관측 가능 시간 0")):
            errors.append("zero_observable_missing_notice")

    # Check eye closure count faithfulness (total count must appear; window counts are allowed when total is cited)
    allowed_eye_counts = {summary.eye_closure_count} | {
        int(w.get("eye_closure_count", 0)) for w in summary.windows
    }
    eye_matches = [int(m.group(1)) for m in _EYE_COUNT_RE.finditer(cleaned)]
    if eye_matches:
        if summary.eye_closure_count not in eye_matches or any(
            val not in allowed_eye_counts for val in eye_matches
        ):
            errors.append("mismatched_eye_closure_count")
    elif summary.eye_closure_count > 0:
        if f"{summary.eye_closure_count}회" not in cleaned:
            errors.append("missing_eye_closure_count")

    # Check head down count faithfulness
    head_matches = [int(m.group(1)) for m in _HEAD_COUNT_RE.finditer(cleaned)]
    if head_matches:
        if any(val != summary.head_down_count for val in head_matches):
            errors.append("mismatched_head_down_count")
    elif summary.head_down_count > 0:
        if f"{summary.head_down_count}회" not in cleaned:
            errors.append("missing_head_down_count")

    # Check actionable next step presence
    if not any(keyword in cleaned for keyword in _ACTION_KEYWORDS):
        errors.append("missing_actionable_advice")

    return errors


def build_fallback_result(
    summary_or_dict: SessionSummary | dict,
    *,
    model_id: str,
    model_revision: str = DEFAULT_MODEL_REVISION,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    raw_text: str = "",
    validation_errors: list[str] | None = None,
    elapsed_seconds: float | None = None,
) -> CoachingResult:
    """Create a safe fallback CoachingResult preserving raw_text and validation_errors."""
    summary = coerce_summary(summary_or_dict)
    rule_text = generate_rule_coaching_text(summary)
    display_text = f"[규칙 기반 대체 안내] {rule_text}"
    return CoachingResult(
        session_id=summary.session_id,
        model_id=model_id,
        model_revision=model_revision,
        dataset_version=dataset_version,
        status="failed",
        raw_text=raw_text or "",
        display_text=display_text,
        validation_errors=list(validation_errors or ["generation_failed"]),
        elapsed_seconds=elapsed_seconds,
        is_fallback=True,
    )


class RuleBasedCoach:
    """Deterministic rule-based coach for baseline comparison and offline operation."""

    def __init__(
        self,
        model_id: str = DEFAULT_RULE_MODEL_ID,
        model_revision: str = DEFAULT_MODEL_REVISION,
        dataset_version: str = DEFAULT_DATASET_VERSION,
    ):
        self.model_id = model_id
        self.model_revision = model_revision
        self.dataset_version = dataset_version

    def generate(self, summary_or_dict: SessionSummary | dict) -> CoachingResult:
        summary = coerce_summary(summary_or_dict)
        started = time.perf_counter()
        text = generate_rule_coaching_text(summary)
        elapsed = max(0.0, time.perf_counter() - started)
        errors = validate_coaching_text(summary, text)
        return CoachingResult(
            session_id=summary.session_id,
            model_id=self.model_id,
            model_revision=self.model_revision,
            dataset_version=self.dataset_version,
            status="succeeded" if not errors else "failed",
            raw_text=text,
            display_text=text,
            validation_errors=errors,
            elapsed_seconds=elapsed,
            is_fallback=False,
        )


def _extract_response_from_generated(full_text: str, prompt: str) -> str:
    text = full_text
    if text.startswith(prompt):
        text = text[len(prompt):]
    elif RESPONSE_HEADER.strip() in text:
        text = text.split(RESPONSE_HEADER.strip(), 1)[-1]
    for stop_token in ("</s>", "<|endoftext|>", "<pad>", "### 세션 통계"):
        if stop_token in text:
            text = text.split(stop_token, 1)[0]
    return text.strip()


class KoGPT2Coach:
    """KoGPT2 / LoRA coaching engine with output validation and automatic rule-based fallback."""

    def __init__(
        self,
        model_id: str = DEFAULT_BASE_MODEL_ID,
        *,
        adapter_path: str | Path | None = None,
        model_revision: str = DEFAULT_MODEL_REVISION,
        dataset_version: str = DEFAULT_DATASET_VERSION,
        model=None,
        tokenizer=None,
        generator_fn: Callable[[str, SessionSummary], str] | None = None,
        max_new_tokens: int = 160,
        temperature: float = 0.3,
        top_p: float = 0.9,
        do_sample: bool = False,
        device: str | None = None,
    ):
        self.model_id = model_id
        self.adapter_path = Path(adapter_path) if adapter_path is not None else None
        self.model_revision = model_revision
        self.dataset_version = dataset_version
        self.model = model
        self.tokenizer = tokenizer
        self.generator_fn = generator_fn
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.do_sample = do_sample
        self.device = device
        self._load_error: str | None = None

        if self.adapter_path is not None:
            meta_file = self.adapter_path / "training_metadata.json"
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    self.model_revision = str(meta.get("model_revision") or self.model_revision)
                    self.dataset_version = str(meta.get("dataset_version") or self.dataset_version)
                except (OSError, ValueError, TypeError):
                    pass

    @classmethod
    def from_lora_adapter(
        cls,
        adapter_path: str | Path,
        *,
        base_model_id: str | None = None,
        device: str | None = None,
        lazy: bool = False,
        **kwargs,
    ) -> "KoGPT2Coach":
        """Create a KoGPT2Coach configured to load a saved LoRA adapter directory."""
        path = Path(adapter_path)
        resolved_base = base_model_id or DEFAULT_BASE_MODEL_ID
        revision = kwargs.pop("model_revision", f"lora-{path.name}")
        dataset_ver = kwargs.pop("dataset_version", DEFAULT_DATASET_VERSION)

        meta_file = path / "training_metadata.json"
        if meta_file.is_file():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                resolved_base = str(meta.get("base_model_id") or resolved_base)
                revision = str(meta.get("model_revision") or revision)
                dataset_ver = str(meta.get("dataset_version") or dataset_ver)
            except (OSError, ValueError, TypeError):
                pass

        coach = cls(
            model_id=f"{resolved_base}+lora:{path.name}",
            adapter_path=path,
            model_revision=revision,
            dataset_version=dataset_ver,
            device=device,
            **kwargs,
        )
        coach._base_model_id = resolved_base
        if not lazy and coach.generator_fn is None and coach.model is None:
            coach.ensure_loaded()
        return coach

    def ensure_loaded(self) -> bool:
        """Load tokenizer, base KoGPT2 model, and optional LoRA adapter on demand."""
        if self.generator_fn is not None or (self.model is not None and self.tokenizer is not None):
            return True
        if self._load_error is not None:
            return False

        base_id = getattr(self, "_base_model_id", self.model_id)
        if self.adapter_path is not None:
            if not self.adapter_path.exists():
                self._load_error = f"adapter_path_not_found:{self.adapter_path}"
                return False
            mock_file = self.adapter_path / "mock_adapter.json"
            if mock_file.is_file() and self.generator_fn is None:
                try:
                    mock_meta = json.loads(mock_file.read_text(encoding="utf-8"))
                    prefix = mock_meta.get("response_prefix", "")
                    self.generator_fn = lambda _prompt, summary: (
                        f"{prefix}{generate_rule_coaching_text(summary)}".strip()
                    )
                    return True
                except (OSError, ValueError, TypeError) as exc:
                    self._load_error = f"mock_adapter_load_error:{exc}"
                    return False

        try:
            import torch
            from transformers import AutoModelForCausalLM, PreTrainedTokenizerFast
        except ImportError as exc:
            self._load_error = f"missing_ml_dependencies:{exc}"
            return False

        try:
            resolved_device = self.device
            if resolved_device is None:
                if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    resolved_device = "mps"
                elif torch.cuda.is_available():
                    resolved_device = "cuda"
                else:
                    resolved_device = "cpu"
            self.device = resolved_device

            tokenizer_source = (
                str(self.adapter_path)
                if self.adapter_path is not None and (self.adapter_path / "tokenizer.json").is_file()
                else base_id
            )
            self.tokenizer = PreTrainedTokenizerFast.from_pretrained(
                tokenizer_source,
                bos_token="</s>",
                eos_token="</s>",
                unk_token="<unk>",
                pad_token="<pad>",
                mask_token="<mask>",
            )
            base_model = AutoModelForCausalLM.from_pretrained(base_id)
            if self.adapter_path is not None:
                from peft import PeftModel

                base_model = PeftModel.from_pretrained(base_model, str(self.adapter_path))
            base_model.to(self.device)
            base_model.eval()
            self.model = base_model
            return True
        except Exception as exc:
            self._load_error = f"model_load_failed:{exc}"
            return False

    def _run_inference(self, prompt: str, summary: SessionSummary) -> str:
        if self.generator_fn is not None:
            return self.generator_fn(prompt, summary)
        if not self.ensure_loaded():
            raise RuntimeError(self._load_error or "model_not_loaded")

        import torch

        encoded = self.tokenizer(prompt, return_tensors="pt")
        input_ids = encoded["input_ids"].to(self.device)
        attention_mask = encoded.get("attention_mask")
        if attention_mask is not None:
            attention_mask = attention_mask.to(self.device)

        gen_kwargs = {
            "max_new_tokens": self.max_new_tokens,
            "pad_token_id": self.tokenizer.pad_token_id or self.tokenizer.eos_token_id or 0,
            "eos_token_id": self.tokenizer.eos_token_id,
            "do_sample": self.do_sample,
            "repetition_penalty": 1.15,
        }
        if self.do_sample:
            gen_kwargs["temperature"] = self.temperature
            gen_kwargs["top_p"] = self.top_p

        with torch.no_grad():
            output_ids = self.model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                **gen_kwargs,
            )
        decoded = self.tokenizer.decode(output_ids[0], skip_special_tokens=False)
        return _extract_response_from_generated(decoded, prompt)

    def generate(self, summary_or_dict: SessionSummary | dict) -> CoachingResult:
        summary = coerce_summary(summary_or_dict)
        prompt = format_coaching_prompt(summary)
        started = time.perf_counter()
        try:
            raw_output = self._run_inference(prompt, summary)
        except Exception as exc:
            elapsed = max(0.0, time.perf_counter() - started)
            return build_fallback_result(
                summary,
                model_id=self.model_id,
                model_revision=self.model_revision,
                dataset_version=self.dataset_version,
                raw_text="",
                validation_errors=[str(exc)],
                elapsed_seconds=elapsed,
            )

        elapsed = max(0.0, time.perf_counter() - started)
        cleaned_raw = _extract_response_from_generated(raw_output, prompt) if isinstance(raw_output, str) else ""
        errors = validate_coaching_text(summary, cleaned_raw)
        if errors:
            return build_fallback_result(
                summary,
                model_id=self.model_id,
                model_revision=self.model_revision,
                dataset_version=self.dataset_version,
                raw_text=cleaned_raw,
                validation_errors=errors,
                elapsed_seconds=elapsed,
            )

        result = CoachingResult(
            session_id=summary.session_id,
            model_id=self.model_id,
            model_revision=self.model_revision,
            dataset_version=self.dataset_version,
            status="succeeded",
            raw_text=cleaned_raw,
            display_text=cleaned_raw,
            validation_errors=[],
            elapsed_seconds=elapsed,
            is_fallback=False,
        )
        if not result.matches_session(summary.session_id):
            return build_fallback_result(
                summary,
                model_id=self.model_id,
                model_revision=self.model_revision,
                dataset_version=self.dataset_version,
                raw_text=cleaned_raw,
                validation_errors=["session_id_mismatch"],
                elapsed_seconds=elapsed,
            )
        return result


def generate_coaching_for_session(
    summary_or_dict: SessionSummary | dict,
    *,
    mode: str = "rule",
    adapter_path: str | Path | None = None,
    base_model_id: str = DEFAULT_BASE_MODEL_ID,
    model_revision: str = DEFAULT_MODEL_REVISION,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    coach=None,
) -> CoachingResult | None:
    """High-level entry point to generate a CoachingResult for a finished session."""
    if mode in ("none", "off", None):
        return None
    summary = coerce_summary(summary_or_dict)
    if coach is not None:
        return coach.generate(summary)
    if mode == "rule":
        return RuleBasedCoach(
            model_revision=model_revision,
            dataset_version=dataset_version,
        ).generate(summary)
    if mode == "lora":
        resolved_adapter = Path(adapter_path) if adapter_path else Path("models/kogpt2-coaching-lora")
        engine = KoGPT2Coach.from_lora_adapter(
            resolved_adapter,
            base_model_id=base_model_id,
            model_revision=model_revision,
            dataset_version=dataset_version,
            lazy=True,
        )
        return engine.generate(summary)
    if mode == "kogpt2":
        engine = KoGPT2Coach(
            model_id=base_model_id,
            model_revision=model_revision,
            dataset_version=dataset_version,
        )
        return engine.generate(summary)
    raise ValueError(f"Unsupported coaching mode: {mode}")
