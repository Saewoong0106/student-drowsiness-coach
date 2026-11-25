"""Tests for coaching inference (#8), dataset/group-split tools (#7), and contract/device checks (#21)."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.check_training_env import check_device_and_packages, verify_cross_module_contracts
from study_coach.coaching import (
    VALID_SCENARIOS,
    KoGPT2Coach,
    RuleBasedCoach,
    classify_scenario,
    format_coaching_prompt,
    generate_coaching_for_session,
    generate_rule_coaching_text,
    validate_coaching_text,
)
from study_coach.coaching_data import (
    IGNORE_INDEX,
    SHORT_DEMO_PROFILES,
    build_seed_coaching_records,
    build_short_demo_coaching_records,
    build_short_demo_eval_scenarios,
    classify_short_demo_profile,
    collate_causal_lm_batch,
    encode_causal_lm_sample,
    evaluate_coaching_models,
    export_review_batches,
    extract_coaching_records_from_db,
    extract_coaching_records_from_json,
    mark_records_reviewed,
    merge_and_validate_coaching_datasets,
    split_coaching_records,
    validate_coaching_dataset,
    validate_coaching_record,
    verify_consecutive_demo_runs,
    verify_no_dataset_leakage,
    verify_no_group_leakage,
)
from study_coach.contracts import FrameMeasurement, SessionSummary
from study_coach.session import Settings, StudySession
from study_coach.storage import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class CoachingAndValidationTests(unittest.TestCase):
    def test_all_eight_scenarios_generate_valid_rule_coaching(self):
        records = build_seed_coaching_records(groups_per_scenario=1, variants_per_group=1)
        seen_scenarios = set()
        coach = RuleBasedCoach()
        for rec in records:
            summary = rec.summary()
            inferred = classify_scenario(summary)
            self.assertEqual(inferred, rec.scenario)
            seen_scenarios.add(inferred)
            result = coach.generate(summary)
            self.assertEqual(result.status, "succeeded")
            self.assertFalse(result.is_fallback)
            self.assertEqual(result.validation_errors, [])
            self.assertTrue(result.matches_session(summary.session_id))
        self.assertEqual(seen_scenarios, set(VALID_SCENARIOS))

    def test_zero_observable_seconds_requires_unobservable_notice(self):
        summary = SessionSummary(
            session_id="zero-obs-001",
            goal="임베디드 복습",
            study_seconds=600,
            break_seconds=0,
            observable_seconds=0,
            unobservable_seconds=600,
            eye_closure_count=0,
            eye_closure_seconds=0,
            head_down_count=0,
        )
        prompt = format_coaching_prompt(summary)
        self.assertIn("측정 불가", prompt)
        rule_text = generate_rule_coaching_text(summary)
        self.assertIn("측정 불가", rule_text)
        self.assertEqual(validate_coaching_text(summary, rule_text), [])

        bad_text = "눈 감김 0회와 고개 숙임 0회가 기록되었습니다. 다음 공부 전 5분 휴식해 보세요."
        errors = validate_coaching_text(summary, bad_text)
        self.assertIn("zero_observable_missing_notice", errors)

    def test_kogpt2_coach_preserves_raw_text_and_falls_back_on_validation_failure(self):
        summary = SessionSummary(
            session_id="sess-val-01",
            goal="알고리즘",
            study_seconds=1800,
            break_seconds=0,
            observable_seconds=1700,
            unobservable_seconds=100,
            eye_closure_count=3,
            eye_closure_seconds=12.0,
            head_down_count=2,
        )
        hallucinated = (
            "이번 세션 집중도 95점입니다! 눈 감김 의심 5회와 고개 숙임 2회가 관찰되었으니 10분 휴식해 보세요."
        )
        coach = KoGPT2Coach(
            model_id="skt/kogpt2-base-v2",
            model_revision="test-rev",
            dataset_version="test-ds",
            generator_fn=lambda _prompt, _summary: hallucinated,
        )
        result = coach.generate(summary)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.is_fallback)
        self.assertEqual(result.raw_text, hallucinated)
        self.assertIn("[규칙 기반 대체 안내]", result.display_text)
        self.assertIn("눈 감김 의심 3회", result.display_text)
        self.assertIn("forbidden_concentration_score", result.validation_errors)
        self.assertIn("mismatched_eye_closure_count", result.validation_errors)

    def test_kogpt2_coach_succeeds_when_output_passes_validation(self):
        summary = SessionSummary(
            session_id="sess-ok-01",
            goal="운영체제",
            study_seconds=2400,
            break_seconds=300,
            observable_seconds=2300,
            unobservable_seconds=100,
            eye_closure_count=2,
            eye_closure_seconds=6.0,
            head_down_count=1,
        )
        good_output = (
            "40분 공부와 5분 휴식 중 눈 감김 의심 2회(6초)와 고개 숙임 1회가 기록되었습니다. "
            "전반적으로 안정적인 흐름이었으나 다음 공부 전 5분간 가볍게 스트레칭하며 휴식해 보세요."
        )
        coach = KoGPT2Coach(
            model_id="skt/kogpt2-base-v2",
            generator_fn=lambda prompt, _summary: f"{prompt}{good_output}</s>",
        )
        result = coach.generate(summary)
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(result.is_fallback)
        self.assertEqual(result.raw_text, good_output)
        self.assertEqual(result.display_text, good_output)
        self.assertEqual(result.validation_errors, [])

    def test_missing_lora_adapter_falls_back_gracefully(self):
        summary = SessionSummary(
            session_id="sess-missing-lora",
            goal="네트워크",
            study_seconds=1200,
            break_seconds=0,
            observable_seconds=1150,
            unobservable_seconds=50,
            eye_closure_count=1,
            eye_closure_seconds=3.0,
            head_down_count=0,
        )
        result = generate_coaching_for_session(
            summary,
            mode="lora",
            adapter_path=Path("/nonexistent/lora/adapter/path"),
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.is_fallback)
        self.assertIn("[규칙 기반 대체 안내]", result.display_text)
        self.assertTrue(any("adapter_path_not_found" in err for err in result.validation_errors))


class DatasetAndTrainingPipelineTests(unittest.TestCase):
    def test_synthetic_vs_reviewed_separation_and_record_validation(self):
        seed_records = build_seed_coaching_records(groups_per_scenario=2, variants_per_group=2)
        report_seed = validate_coaching_dataset(seed_records, require_reviewed=False)
        self.assertTrue(report_seed["valid"])
        self.assertEqual(report_seed["synthetic_records"], len(seed_records))
        self.assertEqual(report_seed["reviewed_records"], 0)

        report_strict = validate_coaching_dataset(seed_records, require_reviewed=True)
        self.assertFalse(report_strict["valid"])
        self.assertEqual(report_strict["invalid_records"], len(seed_records))

        bad_synthetic = seed_records[0].to_dict()
        bad_synthetic["reviewed"] = True
        errs = validate_coaching_record(bad_synthetic)
        self.assertIn("synthetic_record_marked_reviewed", errs)

        human_rec = seed_records[0].to_dict()
        human_rec["source"] = "team_authored"
        human_rec["reviewed"] = True
        self.assertEqual(validate_coaching_record(human_rec, require_reviewed=True), [])

    def test_group_split_prevents_variant_leakage_across_splits(self):
        records = build_seed_coaching_records(groups_per_scenario=4, variants_per_group=3)
        splits = split_coaching_records(records, train_ratio=0.5, val_ratio=0.25, test_ratio=0.25)
        self.assertEqual(verify_no_group_leakage(splits), [])
        self.assertEqual(
            len(splits["train"]) + len(splits["val"]) + len(splits["test"]),
            len(records),
        )
        for split_items in splits.values():
            group_counts = {}
            for item in split_items:
                group_counts[item.group_id] = group_counts.get(item.group_id, 0) + 1
            for count in group_counts.values():
                self.assertEqual(count, 3)

    def test_causal_lm_encoding_masks_prompt_and_padding_labels(self):
        rec = build_seed_coaching_records(groups_per_scenario=1, variants_per_group=1)[0]
        encoded = encode_causal_lm_sample(rec, max_length=512, pad_token_id=3)
        prompt_len = encoded["prompt_token_count"]
        target_len = encoded["target_token_count"]
        self.assertGreater(prompt_len, 0)
        self.assertGreater(target_len, 0)
        self.assertFalse(encoded["is_truncated"])
        self.assertTrue(all(lbl == IGNORE_INDEX for lbl in encoded["labels"][:prompt_len]))
        self.assertTrue(all(lbl != IGNORE_INDEX for lbl in encoded["labels"][prompt_len:]))

        shorter = {
            "input_ids": [11, 12, 13],
            "attention_mask": [1, 1, 1],
            "labels": [IGNORE_INDEX, 12, 13],
            "pad_token_id": 3,
        }
        batch = collate_causal_lm_batch([encoded, shorter], pad_token_id=3)
        self.assertEqual(len(batch["input_ids"][1]), len(encoded["input_ids"]))
        self.assertEqual(batch["attention_mask"][1][3:], [0] * (len(encoded["input_ids"]) - 3))
        self.assertEqual(batch["labels"][1][3:], [IGNORE_INDEX] * (len(encoded["input_ids"]) - 3))

    def test_dry_run_training_and_evaluation_cli_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            seed_file = root / "seed.jsonl"
            splits_dir = root / "splits"
            lora_dir = root / "lora_adapter"
            eval_dir = root / "eval"

            ds_cmd = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "coaching_dataset.py"),
                    "--generate-seed",
                    str(seed_file),
                    "--validate",
                    str(seed_file),
                    "--split",
                    str(seed_file),
                    "--output-dir",
                    str(splits_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(ds_cmd.returncode, 0, ds_cmd.stderr)
            self.assertTrue((splits_dir / "train.jsonl").is_file())
            self.assertTrue((splits_dir / "test.jsonl").is_file())

            train_cmd = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "train_coaching_lora.py"),
                    "--train-file",
                    str(splits_dir / "train.jsonl"),
                    "--val-file",
                    str(splits_dir / "val.jsonl"),
                    "--output-dir",
                    str(lora_dir),
                    "--dry-run",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(train_cmd.returncode, 0, train_cmd.stderr)
            meta = json.loads((lora_dir / "training_metadata.json").read_text(encoding="utf-8"))
            self.assertTrue(meta["reload_verified"])
            self.assertTrue(meta["loss_masking_verified"])

            eval_cmd = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "evaluate_coaching.py"),
                    "--test-file",
                    str(splits_dir / "test.jsonl"),
                    "--lora-path",
                    str(lora_dir),
                    "--output-dir",
                    str(eval_dir),
                    "--mock-models",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(eval_cmd.returncode, 0, eval_cmd.stderr)
            eval_summary = json.loads((eval_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(eval_summary["metrics"]["lora"]["numeric_faithfulness_rate"], 1.0)
            self.assertEqual(eval_summary["metrics"]["rule"]["numeric_faithfulness_rate"], 1.0)
            self.assertEqual(eval_summary["metrics"]["base"]["fallback_rate"], 1.0)
            blind_sheet = json.loads((eval_dir / "blind_evaluation_sheet.json").read_text(encoding="utf-8"))
            self.assertEqual(len(blind_sheet), eval_summary["total_records"])

    def test_initial_96_diverse_records_db_json_extraction_and_review_batches(self):
        records_96 = build_seed_coaching_records(
            groups_per_scenario=4,
            variants_per_group=3,
            diverse_templates=True,
        )
        self.assertEqual(len(records_96), 96)
        report = validate_coaching_dataset(records_96, require_reviewed=False)
        self.assertTrue(report["valid"], report["errors"])

        # Ensure variants within a group have distinct phrasing
        by_group = {}
        for rec in records_96:
            by_group.setdefault(rec.group_id, []).append(rec.target)
        for gid, targets in by_group.items():
            self.assertEqual(len(set(targets)), 3, f"Expected 3 distinct phrasings in {gid}")

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db_path = root / "measured.sqlite3"
            export_dir = root / "exported"
            review_dir = root / "reviews"

            session = StudySession(
                settings=Settings(calibration_seconds=1.0, calibration_samples=3),
                source="video_sample",
                goal="공개 영상 추출 테스트",
            )
            for idx in range(0, 35):
                t = round(idx * 0.2, 2)
                ear = 0.12 if 1.2 <= t <= 3.6 else 0.30
                session.step(t, ear, 5.0, eye_valid=True, pose_valid=True)
            session.finish(7.2, reason="video_end")

            store = SessionStore(db_path)
            try:
                store.save(session)
                store.export(session.session_id, export_dir)
            finally:
                store.close()

            db_records = extract_coaching_records_from_db(db_path, variants_per_session=2)
            self.assertEqual(len(db_records), 2)
            self.assertTrue(validate_coaching_dataset(db_records)["valid"])

            json_records = extract_coaching_records_from_json(export_dir, variants_per_session=2)
            self.assertEqual(len(json_records), 2)
            self.assertTrue(validate_coaching_dataset(json_records)["valid"])

            batch_info = export_review_batches(records_96, review_dir)
            self.assertEqual(batch_info["total_records"], 96)
            total_assigned = 0
            for assignee in ("Saewoong0106", "nisuhyo", "suyeongim320"):
                cnt = batch_info["assignees"][assignee]["record_count"]
                self.assertIn(cnt, (30, 33))
                total_assigned += cnt
                self.assertTrue(Path(batch_info["assignees"][assignee]["markdown_path"]).is_file())
            self.assertEqual(total_assigned, 96)

            reviewed_96 = mark_records_reviewed(records_96, reviewer="Saewoong0106")
            strict_report = validate_coaching_dataset(reviewed_96, require_reviewed=True)
            self.assertTrue(strict_report["valid"], strict_report["errors"])
            self.assertEqual(strict_report["reviewed_records"], 96)

    def test_short_demo_records_cover_four_profiles_review_sheets_and_safe_merge(self):
        short_records = build_short_demo_coaching_records(groups_per_profile=3, variants_per_group=3)
        self.assertEqual(len(short_records), 36)

        profiles_seen = set()
        eye_counts_seen = set()
        head_counts_seen = set()
        for rec in short_records:
            summary = rec.summary()
            self.assertGreaterEqual(summary.study_seconds, 30.0)
            self.assertLessEqual(summary.study_seconds, 180.0)
            self.assertEqual(rec.source, "synthetic_short_demo")
            self.assertFalse(rec.reviewed)
            self.assertTrue(rec.is_synthetic)
            prof = classify_short_demo_profile(summary)
            profiles_seen.add(prof)
            eye_counts_seen.add(summary.eye_closure_count)
            head_counts_seen.add(summary.head_down_count)

        self.assertEqual(profiles_seen, set(SHORT_DEMO_PROFILES))
        self.assertGreaterEqual(len(eye_counts_seen), 4)
        self.assertGreaterEqual(len(head_counts_seen), 4)

        unreviewed_report = validate_coaching_dataset(short_records, require_reviewed=False)
        self.assertTrue(unreviewed_report["valid"], unreviewed_report["errors"])
        blocked_report = validate_coaching_dataset(short_records, require_reviewed=True)
        self.assertFalse(blocked_report["valid"])

        records_96 = build_seed_coaching_records(groups_per_scenario=4, variants_per_group=3)
        reviewed_96 = mark_records_reviewed(records_96, reviewer="Saewoong0106")
        reviewed_short = mark_records_reviewed(short_records, reviewer="Saewoong0106")

        merged, merge_report = merge_and_validate_coaching_datasets(
            {"canonical_96": reviewed_96, "short_demo_36": reviewed_short},
            require_reviewed=True,
        )
        self.assertTrue(merge_report["valid"], merge_report["errors"])
        self.assertEqual(len(merged), 132)
        self.assertEqual(merge_report["origin_counts"], {"canonical_96": 96, "short_demo_36": 36})
        self.assertEqual(set(merge_report["short_demo_profile_counts"].keys()), set(SHORT_DEMO_PROFILES))

        splits = split_coaching_records(merged, train_ratio=0.7, val_ratio=0.15, test_ratio=0.15)
        self.assertEqual(verify_no_group_leakage(splits), [])
        for split_name in ("train", "val", "test"):
            split_short_profiles = {
                classify_short_demo_profile(r.summary())
                for r in splits[split_name]
                if r.group_id.startswith("grp-short_demo-")
            }
            self.assertEqual(split_short_profiles, set(SHORT_DEMO_PROFILES))

    def test_short_demo_eval_leakage_guard_and_fallback_excluded_from_lora_success(self):
        train_candidates = build_short_demo_coaching_records(groups_per_profile=3, variants_per_group=3)
        canonical_96 = build_seed_coaching_records(groups_per_scenario=4, variants_per_group=3)
        eval_scenarios = build_short_demo_eval_scenarios(cases_per_profile=3)
        self.assertEqual(len(eval_scenarios), 12)
        self.assertEqual(verify_no_dataset_leakage(train_candidates + canonical_96, eval_scenarios), [])

        # Intentional leakage must be rejected
        with self.assertRaises(ValueError):
            evaluate_coaching_models(
                [train_candidates[0]],
                {"rule": RuleBasedCoach()},
                reference_records=train_candidates,
            )

        # Fallback outputs must NEVER count as LoRA generation success even though display_text has rule fallback
        flaky_lora = KoGPT2Coach(
            model_id="skt/kogpt2-base-v2+lora:test",
            generator_fn=lambda _prompt, summary: (
                generate_rule_coaching_text(summary)
                if summary.eye_closure_count == 0
                else f"눈 감김 의심 {summary.eye_closure_count + 2}회가 관찰되었습니다. 5분 휴식해 보세요."
            ),
        )
        report = evaluate_coaching_models(
            eval_scenarios,
            {"lora": flaky_lora, "rule": RuleBasedCoach()},
            reference_records=train_candidates + canonical_96,
        )
        self.assertTrue(report["leakage_verified"])
        self.assertEqual(report["leakage_errors"], [])

        lora_metrics = report["metrics"]["lora"]
        # Only none + head_only (6 of 12) have eye_closure_count == 0; eye_only + both (6 of 12) fall back!
        self.assertEqual(lora_metrics["generation_success_count"], 6)
        self.assertAlmostEqual(lora_metrics["generation_success_rate"], 0.5)
        self.assertEqual(lora_metrics["fallback_count"], 6)
        self.assertAlmostEqual(lora_metrics["fallback_rate"], 0.5)
        self.assertGreaterEqual(lora_metrics["p95_elapsed_seconds"], 0.0)
        self.assertEqual(report["short_demo_profile_metrics"]["lora"]["none"]["generation_success_rate"], 1.0)
        self.assertEqual(report["short_demo_profile_metrics"]["lora"]["both"]["generation_success_rate"], 0.0)

    def test_consecutive_demo_runs_verification_passes_5_valid_and_fails_on_fallback(self):
        good_lora = KoGPT2Coach(
            model_id="skt/kogpt2-base-v2+lora:good",
            generator_fn=lambda _prompt, summary: generate_rule_coaching_text(summary),
        )
        pass_report = verify_consecutive_demo_runs(coach=good_lora, target_consecutive=5)
        self.assertTrue(pass_report["consecutive_target_met"])
        self.assertEqual(pass_report["max_consecutive_successes"], 5)
        self.assertEqual(pass_report["generation_success_count"], 5)
        self.assertEqual(pass_report["fallback_count"], 0)

        call_counter = {"n": 0}

        def _fail_third(_prompt, summary):
            call_counter["n"] += 1
            if call_counter["n"] == 3:
                return "집중도 99점입니다! 잠시 쉬세요."
            return generate_rule_coaching_text(summary)

        intermittent_lora = KoGPT2Coach(
            model_id="skt/kogpt2-base-v2+lora:flaky",
            generator_fn=_fail_third,
        )
        fail_report = verify_consecutive_demo_runs(coach=intermittent_lora, target_consecutive=5)
        self.assertFalse(fail_report["consecutive_target_met"])
        self.assertEqual(fail_report["max_consecutive_successes"], 2)
        self.assertEqual(fail_report["generation_success_count"], 4)
        self.assertEqual(fail_report["fallback_count"], 1)
        self.assertTrue(fail_report["runs"][2]["is_fallback"])
        self.assertFalse(fail_report["runs"][2]["genuine_generation_success"])


class ContractHandoffAndEnvironmentTests(unittest.TestCase):
    def test_environment_and_cross_module_contract_handoff(self):
        env_info = check_device_and_packages()
        self.assertIn("python_version", env_info)
        self.assertIn("pytorch_device", env_info)

        handoff = verify_cross_module_contracts()
        self.assertTrue(handoff["contracts_verified"])
        self.assertEqual(handoff["eye_closure_count"], 1)
        self.assertEqual(handoff["head_down_count"], 1)
        self.assertEqual(handoff["coaching_status"], "succeeded")
        self.assertTrue(handoff["coaching_matches_session"])


if __name__ == "__main__":
    unittest.main()
