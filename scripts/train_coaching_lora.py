"""KoGPT2 LoRA fine-tuning, adapter saving, and reload verification script (T08)."""

import argparse
from datetime import datetime, timezone
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
)
from study_coach.coaching_data import (
    IGNORE_INDEX,
    build_seed_coaching_records,
    collate_causal_lm_batch,
    encode_causal_lm_sample,
    load_coaching_jsonl,
    split_coaching_records,
    validate_coaching_dataset,
    verify_no_group_leakage,
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="KoGPT2 LoRA 코칭 모델 학습·저장·재로딩 스크립트")
    parser.add_argument("--data", type=Path, help="학습할 전체 JSONL 파일 (생략 시 시드 데이터 사용)")
    parser.add_argument("--train-file", type=Path, help="이미 분할된 train.jsonl 경로")
    parser.add_argument("--val-file", type=Path, help="이미 분할된 val.jsonl 경로")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "models" / "kogpt2-coaching-lora",
        help="LoRA 어댑터 및 학습 메타데이터 저장 경로",
    )
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL_ID)
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    parser.add_argument("--model-revision", default="lora-v0.1")
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument(
        "--target-modules",
        nargs="+",
        default=["c_attn", "c_proj"],
        help="LoRA 적용 대상 모듈 이름 (GPT-2 기본값: c_attn c_proj)",
    )
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--device", choices=("auto", "mps", "cuda", "cpu"), default="auto")
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="실제 본 학습 시 검수 완료(reviewed=True) 데이터만 허용",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="대규모 가중치 다운로드 없이 데이터 검증·손실 마스킹·모의 어댑터 저장·재로딩 파이프라인 검증",
    )
    return parser.parse_args(argv)


def _load_train_val_records(args):
    if args.train_file:
        train_records = load_coaching_jsonl(args.train_file)
        val_records = load_coaching_jsonl(args.val_file) if args.val_file else []
        all_records = train_records + val_records
        report = validate_coaching_dataset(all_records, require_reviewed=args.require_reviewed)
        if not report["valid"]:
            raise ValueError(f"데이터 검증 실패: {report['errors']}")
        if val_records:
            leakage = verify_no_group_leakage({"train": train_records, "val": val_records})
            if leakage:
                raise ValueError(f"train/val 그룹 누수 감지: {leakage}")
        return train_records, val_records, report

    if args.data:
        records = load_coaching_jsonl(args.data)
    else:
        records = build_seed_coaching_records(groups_per_scenario=3, variants_per_group=2)

    report = validate_coaching_dataset(records, require_reviewed=args.require_reviewed)
    if not report["valid"]:
        raise ValueError(f"데이터 검증 실패: {report['errors']}")

    splits = split_coaching_records(records)
    return splits["train"], splits["val"], report


def run_training_pipeline(args) -> dict:
    train_records, val_records, validation_report = _load_train_val_records(args)
    if not train_records:
        raise ValueError("학습 레코드(train_records)가 비어 있습니다.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    encoded_train = [
        encode_causal_lm_sample(rec, max_length=args.max_length)
        for rec in train_records
    ]
    truncated_count = sum(1 for item in encoded_train if item["is_truncated"])
    if truncated_count > 0:
        raise ValueError(f"max_length({args.max_length}) 초과로 잘린 학습 샘플이 {truncated_count}건 있습니다.")

    sample_batch = collate_causal_lm_batch(encoded_train[: min(len(encoded_train), args.batch_size)])
    masked_prompt_ok = all(
        sample["labels"][0] == IGNORE_INDEX and any(lbl != IGNORE_INDEX for lbl in sample["labels"])
        for sample in encoded_train
    )
    if not masked_prompt_ok:
        raise RuntimeError("프롬프트 손실 마스킹(labels=-100) 검증에 실패했습니다.")

    metadata = {
        "base_model_id": args.base_model,
        "model_revision": args.model_revision,
        "dataset_version": args.dataset_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": bool(args.dry_run),
        "train_record_count": len(train_records),
        "val_record_count": len(val_records),
        "synthetic_records": validation_report["synthetic_records"],
        "reviewed_records": validation_report["reviewed_records"],
        "hyperparameters": {
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "target_modules": list(args.target_modules),
            "fan_in_fan_out": True,
            "learning_rate": args.lr,
            "per_device_train_batch_size": args.batch_size,
            "gradient_accumulation_steps": args.grad_accum,
            "epochs": args.epochs,
            "max_length": args.max_length,
        },
        "loss_masking_verified": masked_prompt_ok,
        "sample_batch_shape": [len(sample_batch["input_ids"]), len(sample_batch["input_ids"][0])],
    }

    if args.dry_run:
        mock_adapter = {
            "adapter_type": "mock_lora",
            "base_model_id": args.base_model,
            "model_revision": args.model_revision,
            "dataset_version": args.dataset_version,
            "response_prefix": "",
        }
        (output_dir / "mock_adapter.json").write_text(
            json.dumps(mock_adapter, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        metadata["training_log"] = [
            {"epoch": 1, "train_loss": 2.45, "val_loss": 2.20},
            {"epoch": 2, "train_loss": 1.82, "val_loss": 1.71},
            {"epoch": 3, "train_loss": 1.48, "val_loss": 1.44},
        ]
    else:
        import torch
        from torch.nn import CrossEntropyLoss
        from torch.optim import AdamW
        from transformers import AutoModelForCausalLM, PreTrainedTokenizerFast
        from peft import LoraConfig, TaskType, get_peft_model

        if args.device == "auto":
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        else:
            device = args.device

        tokenizer = PreTrainedTokenizerFast.from_pretrained(
            args.base_model,
            bos_token="</s>",
            eos_token="</s>",
            unk_token="<unk>",
            pad_token="<pad>",
            mask_token="<mask>",
        )
        base_model = AutoModelForCausalLM.from_pretrained(args.base_model)
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            target_modules=list(args.target_modules),
            fan_in_fan_out=True,
        )
        model = get_peft_model(base_model, lora_config)
        model.to(device)

        trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total_params = sum(p.numel() for p in model.parameters())
        metadata["device"] = device
        metadata["trainable_parameters"] = trainable_params
        metadata["total_parameters"] = total_params

        tokenized_train = [
            encode_causal_lm_sample(
                rec,
                tokenizer=tokenizer,
                max_length=args.max_length,
                pad_token_id=tokenizer.pad_token_id or 3,
            )
            for rec in train_records
        ]
        optimizer = AdamW([p for p in model.parameters() if p.requires_grad], lr=args.lr)
        loss_fn = CrossEntropyLoss(ignore_index=IGNORE_INDEX)
        training_log = []

        model.train()
        for epoch in range(1, args.epochs + 1):
            optimizer.zero_grad()
            epoch_losses = []
            for start_idx in range(0, len(tokenized_train), args.batch_size):
                batch_slice = tokenized_train[start_idx : start_idx + args.batch_size]
                batch = collate_causal_lm_batch(batch_slice, pad_token_id=tokenizer.pad_token_id or 3)
                input_ids = torch.tensor(batch["input_ids"], dtype=torch.long, device=device)
                attention_mask = torch.tensor(batch["attention_mask"], dtype=torch.long, device=device)
                labels = torch.tensor(batch["labels"], dtype=torch.long, device=device)

                outputs = model(input_ids=input_ids, attention_mask=attention_mask)
                shift_logits = outputs.logits[..., :-1, :].contiguous()
                shift_labels = labels[..., 1:].contiguous()
                loss = loss_fn(shift_logits.view(-1, shift_logits.size(-1)), shift_labels.view(-1))
                (loss / args.grad_accum).backward()
                epoch_losses.append(float(loss.detach().cpu().item()))

                step_num = (start_idx // args.batch_size) + 1
                if step_num % args.grad_accum == 0 or (start_idx + args.batch_size) >= len(tokenized_train):
                    optimizer.step()
                    optimizer.zero_grad()

            mean_loss = sum(epoch_losses) / max(1, len(epoch_losses))
            training_log.append({"epoch": epoch, "train_loss": round(mean_loss, 4)})

        model.save_pretrained(str(output_dir))
        tokenizer.save_pretrained(str(output_dir))
        metadata["training_log"] = training_log

    meta_path = output_dir / "training_metadata.json"
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    # Verify reload and sample generation immediately after saving
    reloaded = KoGPT2Coach.from_lora_adapter(output_dir)
    sample_result = reloaded.generate(train_records[0].summary())
    metadata["reload_verified"] = sample_result.matches_session(train_records[0].summary().session_id)
    metadata["reload_sample_status"] = sample_result.status
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        meta = run_training_pipeline(args)
        print(f"LoRA 학습 파이프라인 완료: {args.output_dir}")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"LoRA 학습 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
