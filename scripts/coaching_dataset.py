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
    export_review_batches,
    extract_coaching_records_from_db,
    extract_coaching_records_from_json,
    load_coaching_jsonl,
    mark_records_reviewed,
    save_coaching_jsonl,
    split_coaching_records,
    validate_coaching_dataset,
    verify_no_group_leakage,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="코칭 학습 데이터(JSONL) 시드/실측 추출, 무결성 검증, 팀원 검수 배분, 그룹 단위 분할 도구"
    )
    parser.add_argument(
        "--generate-seed",
        type=Path,
        help="코칭 JSONL 파일 생성 경로 (기본 source='synthetic_seed', reviewed=False)",
    )
    parser.add_argument("--groups-per-scenario", type=int, default=3)
    parser.add_argument("--variants-per-group", type=int, default=2)
    parser.add_argument(
        "--from-db",
        type=Path,
        help="실제 측정된 SQLite 세션 DB(study.sqlite3)에서 코칭 데이터를 추출해 포함할 경로",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        help="session_report.py --export로 내보낸 JSON 파일 또는 폴더에서 코칭 데이터를 추출할 경로",
    )
    parser.add_argument(
        "--variants-per-session",
        type=int,
        default=1,
        help="--from-db / --from-json 추출 시 세션당 생성할 코칭 문장 변형 수 (기본 1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="--from-db, --from-json, --mark-reviewed 결과를 저장할 JSONL 파일 경로",
    )
    parser.add_argument(
        "--assign-review",
        type=Path,
        help="검증/생성된 데이터를 팀원 3명(Saewoong0106, nisuhyo, suyeongim320) 검수 시트로 배분할 폴더 경로",
    )
    parser.add_argument(
        "--mark-reviewed",
        type=Path,
        help="사람이 검수를 마친 JSONL 파일을 reviewed=True로 승격할 입력 경로",
    )
    parser.add_argument(
        "--reviewer",
        default="human_reviewed",
        help="--mark-reviewed 시 기록할 검수자 ID (예: Saewoong0106)",
    )
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
    has_action = any(
        (
            args.generate_seed,
            args.from_db,
            args.from_json,
            args.mark_reviewed,
            args.assign_review,
            args.validate,
            args.split,
        )
    )
    if not has_action:
        print(
            "실행할 작업을 지정하세요 (--generate-seed, --from-db, --from-json, --mark-reviewed, --assign-review, --validate, --split).",
            file=sys.stderr,
        )
        return 2

    try:
        generated_records = []
        if args.from_db:
            db_records = extract_coaching_records_from_db(
                args.from_db,
                variants_per_session=args.variants_per_session,
            )
            generated_records.extend(db_records)
            print(f"DB 세션 추출 완료: {args.from_db} ({len(db_records)}건)")

        if args.from_json:
            json_records = extract_coaching_records_from_json(
                args.from_json,
                variants_per_session=args.variants_per_session,
            )
            generated_records.extend(json_records)
            print(f"JSON 세션 추출 완료: {args.from_json} ({len(json_records)}건)")

        if args.generate_seed:
            seed_records = build_seed_coaching_records(
                groups_per_scenario=args.groups_per_scenario,
                variants_per_group=args.variants_per_group,
                diverse_templates=True,
            )
            combined = generated_records + seed_records
            out_path = save_coaching_jsonl(combined, args.generate_seed)
            print(
                f"코칭 데이터 생성 완료: {out_path} (총 {len(combined)}건 = 실측 추출 {len(generated_records)}건 + 시나리오 생성 {len(seed_records)}건)"
            )
            generated_records = combined
        elif generated_records and args.output:
            out_path = save_coaching_jsonl(generated_records, args.output)
            print(f"실측 코칭 데이터 저장 완료: {out_path} ({len(generated_records)}건)")

        if args.mark_reviewed:
            raw_to_mark = load_coaching_jsonl(args.mark_reviewed)
            marked = mark_records_reviewed(raw_to_mark, reviewer=args.reviewer)
            target_out = args.output or args.mark_reviewed
            save_coaching_jsonl(marked, target_out)
            print(f"검수 완료 처리(reviewed=True, reviewer={args.reviewer}): {target_out} ({len(marked)}건)")

        if args.assign_review:
            source_file = args.generate_seed or args.output or args.validate or args.split
            records_for_review = generated_records if generated_records else (
                load_coaching_jsonl(source_file) if source_file else []
            )
            if not records_for_review:
                print("--assign-review를 실행하려면 입력 데이터(--generate-seed, --validate 등)가 필요합니다.", file=sys.stderr)
                return 2
            batch_info = export_review_batches(records_for_review, args.assign_review)
            print(f"팀원별 검수 시트 배분 완료: {args.assign_review} (총 {batch_info['total_records']}건)")
            for owner, info in batch_info["assignees"].items():
                print(f"  - {owner}: {info['record_count']}건 ({info['group_count']}개 그룹) -> {info['markdown_path']}")

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
