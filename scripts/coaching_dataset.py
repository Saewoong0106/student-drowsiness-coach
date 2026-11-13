"""CLI utility to generate seed coaching JSONL, validate records, and split train/val/test by group_id."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study_coach.coaching_data import (
    build_seed_coaching_records,
    load_coaching_jsonl,
    save_coaching_jsonl,
    split_coaching_records,
    validate_coaching_dataset,
    verify_no_group_leakage,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="코칭 학습 데이터(JSONL) 시드 생성, 무결성 검증, 그룹 단위 분할 도구"
    )
    parser.add_argument(
        "--generate-seed",
        type=Path,
        help="소량 합성 시드 JSONL 파일 생성 경로 (source='synthetic_seed', reviewed=False)",
    )
    parser.add_argument("--groups-per-scenario", type=int, default=3)
    parser.add_argument("--variants-per-group", type=int, default=2)
    parser.add_argument("--validate", type=Path, help="검증할 코칭 JSONL 파일 경로")
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="실제 학습 전 사람이 검수한(reviewed=True, 비합성) 데이터만 통과시킬지 여부",
    )
    parser.add_argument("--split", type=Path, help="train/val/test로 분할할 입력 JSONL 파일 경로")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "splits",
        help="분할된 train.jsonl, val.jsonl, test.jsonl 및 명세 저장 폴더",
    )
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not (args.generate_seed or args.validate or args.split):
        print("실행할 작업을 지정하세요 (--generate-seed, --validate, --split 중 하나 이상).", file=sys.stderr)
        return 2

    try:
        if args.generate_seed:
            records = build_seed_coaching_records(
                groups_per_scenario=args.groups_per_scenario,
                variants_per_group=args.variants_per_group,
            )
            out_path = save_coaching_jsonl(records, args.generate_seed)
            print(f"시드 데이터 생성 완료: {out_path} ({len(records)}건, synthetic_seed / reviewed=False)")

        if args.validate:
            raw_rows = [
                json.loads(line)
                for line in Path(args.validate).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            report = validate_coaching_dataset(raw_rows, require_reviewed=args.require_reviewed)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not report["valid"]:
                return 1

        if args.split:
            records = load_coaching_jsonl(args.split)
            report = validate_coaching_dataset(records, require_reviewed=args.require_reviewed)
            if not report["valid"]:
                print("데이터 검증에 실패하여 분할을 중단합니다.", file=sys.stderr)
                print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
                return 1

            splits = split_coaching_records(
                records,
                train_ratio=args.train_ratio,
                val_ratio=args.val_ratio,
                test_ratio=args.test_ratio,
            )
            leakage = verify_no_group_leakage(splits)
            if leakage:
                print(f"그룹 누수 감지: {leakage}", file=sys.stderr)
                return 1

            out_dir = Path(args.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "source_file": str(args.split),
                "total_records": len(records),
                "group_leakage_errors": leakage,
                "splits": {},
            }
            for split_name, split_items in splits.items():
                split_path = save_coaching_jsonl(split_items, out_dir / f"{split_name}.jsonl")
                manifest["splits"][split_name] = {
                    "path": str(split_path),
                    "record_count": len(split_items),
                    "group_ids": sorted({item.group_id for item in split_items}),
                    "scenario_counts": {
                        sc: sum(1 for item in split_items if item.scenario == sc)
                        for sc in sorted({item.scenario for item in split_items})
                    },
                }

            manifest_path = out_dir / "split_manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"데이터 분할 완료: {manifest_path}")
            print(
                f"train={len(splits['train'])}건, val={len(splits['val'])}건, test={len(splits['test'])}건 (그룹 누수 0건)"
            )
    except Exception as exc:
        print(f"데이터 처리 실패: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
