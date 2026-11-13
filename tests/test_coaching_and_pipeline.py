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
    build_seed_coaching_records,
    collate_causal_lm_batch,
    encode_causal_lm_sample,
    split_coaching_records,
    validate_coaching_dataset,
    validate_coaching_record,
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
