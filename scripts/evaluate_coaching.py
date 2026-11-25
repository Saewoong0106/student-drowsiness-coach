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
    build_short_demo_eval_scenarios,
    evaluate_coaching_models,
    load_coaching_jsonl,
    split_coaching_records,
    verify_consecutive_demo_runs,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="기본 KoGPT2 · LoRA 미세 튜닝 모델 · 규칙 기반 코칭 비교 평가, 데이터 누수 검사 및 5회 연속 시연 검증"
    )
    parser.add_argument("--test-file", type=Path, help="평가할 test.jsonl 경로 (생략 시 시드 데이터의 test 분할 사용)")
    parser.add_argument(
        "--short-demo-eval",
        action="store_true",
        help="30초~3분 독립 짧은 시연 평가 시나리오(12건, 4개 조합)로 평가 수행",
    )
    parser.add_argument(
        "--reference-files",
        nargs="+",
        type=Path,
        help="평가 데이터와의 ID·그룹·세션·입력 수치 누출(leakage) 부재를 검증할 train/val JSONL 경로 목록",
    )
    parser.add_argument(
        "--verify-demo-runs",
        type=int,
        default=0,
        help="Issue #12 최종 시연 대비: 임시 SQLite DB로 N회(예: 5) 연속 전체 흐름 및 실제 LoRA 비-fallback 성공 검증",
    )
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
        elif args.short_demo_eval:
            test_records = build_short_demo_eval_scenarios(cases_per_profile=3)
        else:
            seed_records = build_seed_coaching_records(groups_per_scenario=3, variants_per_group=2)
            test_records = split_coaching_records(seed_records)["test"]

        reference_records = None
        if args.reference_files:
            reference_records = []
            for ref_path in args.reference_files:
                reference_records.extend(load_coaching_jsonl(ref_path))

        coaches = _build_coaches(args)
        report = evaluate_coaching_models(
            test_records,
            coaches,
            reference_records=reference_records,
        )
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

        if args.verify_demo_runs and args.verify_demo_runs > 0:
            demo_report = verify_consecutive_demo_runs(
                coach=coaches["lora"],
                mode="lora",
                adapter_path=args.lora_path,
                target_consecutive=args.verify_demo_runs,
            )
            demo_path = out_dir / "consecutive_demo_report.json"
            demo_path.write_text(json.dumps(demo_report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(
                f"연속 시연 검증 완료 (목표 {demo_report['target_consecutive']}회, 최대 연속 {demo_report['max_consecutive_successes']}회, 통과={demo_report['consecutive_target_met']}): {demo_path}"
            )
    except Exception as exc:
        print(f"코칭 평가 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
