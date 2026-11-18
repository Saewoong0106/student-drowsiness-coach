"""Compare base KoGPT2, LoRA-tuned KoGPT2, and rule-based coaching on held-out test records (T12)."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study_coach.coaching import (
    DEFAULT_BASE_MODEL_ID,
    DEFAULT_DATASET_VERSION,
    KoGPT2Coach,
    RuleBasedCoach,
    generate_rule_coaching_text,
)
from study_coach.coaching_data import (
    build_blind_evaluation_sheet,
    build_seed_coaching_records,
    evaluate_coaching_models,
    load_coaching_jsonl,
    split_coaching_records,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="기본 KoGPT2 · LoRA 미세 튜닝 모델 · 규칙 기반 코칭 비교 평가 및 블라인드 평가표 생성"
    )
    parser.add_argument("--test-file", type=Path, help="평가할 test.jsonl 경로 (생략 시 시드 데이터의 test 분할 사용)")
    parser.add_argument(
        "--lora-path",
        type=Path,
        default=ROOT / "models" / "kogpt2-coaching-lora",
        help="평가할 LoRA 어댑터 디렉터리 경로",
    )
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL_ID)
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "private" / "eval",
        help="평가 요약 및 블라인드 평가 시트 저장 디렉터리",
    )
    parser.add_argument(
        "--mock-models",
        action="store_true",
        help="실제 가중치 로드 없이 모의 기본 모델과 모의 튜닝 모델로 평가 파이프라인 검증",
    )
    return parser.parse_args(argv)


def _build_coaches(args) -> dict:
    rule_coach = RuleBasedCoach(dataset_version=args.dataset_version)
    if args.mock_models:
        base_coach = KoGPT2Coach(
            model_id=args.base_model,
            model_revision="base-mock",
            dataset_version=args.dataset_version,
            generator_fn=lambda _prompt, summary: (
                f"이번 세션 집중도 90점입니다. 눈 감김 의심 {summary.eye_closure_count + 1}회가 있었습니다."
            ),
        )
        lora_coach = KoGPT2Coach(
            model_id=f"{args.base_model}+lora",
            model_revision="lora-mock",
            dataset_version=args.dataset_version,
            generator_fn=lambda _prompt, summary: generate_rule_coaching_text(summary),
        )
    else:
        base_coach = KoGPT2Coach(
            model_id=args.base_model,
            model_revision="base",
            dataset_version=args.dataset_version,
        )
        lora_coach = KoGPT2Coach.from_lora_adapter(
            args.lora_path,
            base_model_id=args.base_model,
            dataset_version=args.dataset_version,
            lazy=True,
        )
    return {
        "base": base_coach,
        "lora": lora_coach,
        "rule": rule_coach,
    }


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.test_file:
            test_records = load_coaching_jsonl(args.test_file)
        else:
            seed_records = build_seed_coaching_records(groups_per_scenario=3, variants_per_group=2)
            test_records = split_coaching_records(seed_records)["test"]

        coaches = _build_coaches(args)
        report = evaluate_coaching_models(test_records, coaches)
        blind_sheet, answer_key = build_blind_evaluation_sheet(report)

        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        summary_path = out_dir / "evaluation_summary.json"
        blind_path = out_dir / "blind_evaluation_sheet.json"
        key_path = out_dir / "blind_answer_key.json"

        summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        blind_path.write_text(json.dumps(blind_sheet, ensure_ascii=False, indent=2), encoding="utf-8")
        key_path.write_text(json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"코칭 비교 평가 완료 ({report['total_records']}건): {summary_path}")
        print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"코칭 평가 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
