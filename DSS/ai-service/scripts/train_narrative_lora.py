#!/usr/bin/env python3
"""
Дообучение (LoRA/QLoRA) языковой модели нарратива на корпусе DSS.

Запускается на GPU-сервере в отдельном окружении `/srv/ai/venv-train`, НЕ в
контейнере vLLM и не в прод-образе ai-service: обучению нужен свой стек torch,
и смешивать его с инференсом нельзя.

## Установка окружения (однократно)

    python3 -m venv /srv/ai/venv-train && . /srv/ai/venv-train/bin/activate
    pip install --index-url https://download.pytorch.org/whl/cu128 torch
    pip install transformers peft trl accelerate datasets bitsandbytes

Почему именно так:
* колёса cu128 — первая ветка torch с поддержкой sm_120 (RTX PRO 6000 Blackwell);
  проверить: `torch.cuda.get_device_capability() == (12, 0)`;
* attn_implementation="sdpa", а не flash-attention: готовых колёс FA2 под sm_120
  может не быть, а выигрыш на контексте 4k незначителен;
* bitsandbytes нужен ТОЛЬКО для QLoRA (модель 32B). Для 14B в bf16 адаптер
  обучается без квантования — на 96 ГБ VRAM места достаточно.

## Что важно в самом обучении

Функция потерь считается ТОЛЬКО по ответу ассистента. Для этого корпус подаётся
колонками `prompt`/`completion` — с единым полем `text` флаг
`completion_only_loss` не срабатывает, и модель учится воспроизводить контекст
запроса вместо того, чтобы писать заключение.

## Запуск

    python scripts/train_narrative_lora.py \\
        --base Qwen/Qwen3-14B \\
        --dataset /srv/ai/datasets/narrative \\
        --out /srv/ai/runs/narrative-14b-r32
"""
import argparse
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("lora")


def check_environment() -> None:
    """Проверка GPU и поддержки Blackwell до загрузки модели."""
    import torch

    if not torch.cuda.is_available():
        raise SystemExit("CUDA недоступна — обучение бессмысленно")
    cap = torch.cuda.get_device_capability()
    arch_list = torch.cuda.get_arch_list()
    logger.info("torch %s, CUDA %s, capability %s", torch.__version__, torch.version.cuda, cap)
    if cap == (12, 0) and not any("sm_120" in a for a in arch_list):
        raise SystemExit(
            "Сборка torch не содержит sm_120 — на Blackwell она упадёт с "
            "'no kernel image is available'. Нужны колёса cu128 и новее."
        )
    total = torch.cuda.get_device_properties(0).total_memory / 1e9
    logger.info("GPU: %s, %.0f ГБ VRAM", torch.cuda.get_device_name(0), total)


def load_dataset(path: Path, tokenizer):
    """Корпус JSONL → датасет из колонок `prompt` и `completion`.

    Важно: именно такой формат (а не одно поле `text`) заставляет TRL маскировать
    промпт при расчёте потерь. С единым текстовым полем `completion_only_loss`
    не срабатывает, и модель учится воспроизводить контекст запроса вместо того,
    чтобы писать заключение.

    Шаблон применяется с `enable_thinking=False`: инференс идёт с отключённым
    режимом рассуждений, и обучать модель на шаблоне с ним — значит учить её
    тому, чего в бою не будет.
    """
    from datasets import Dataset

    def _apply(messages: list[dict], add_generation_prompt: bool) -> str:
        try:
            return tokenizer.apply_chat_template(
                messages, tokenize=False,
                add_generation_prompt=add_generation_prompt,
                enable_thinking=False,
            )
        except TypeError:
            # Шаблон модели не знает про enable_thinking — не беда.
            return tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=add_generation_prompt,
            )

    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            prompt = _apply(
                [
                    {"role": "system", "content": item["system"]},
                    {"role": "user", "content": item["user"]},
                ],
                add_generation_prompt=True,
            )
            completion = item["assistant"].strip() + (tokenizer.eos_token or "")
            rows.append({"prompt": prompt, "completion": completion})

    logger.info("%s: %d примеров", path.name, len(rows))
    if rows:
        logger.info("Длина промпта (символов), первый пример: %d", len(rows[0]["prompt"]))
    return Dataset.from_list(rows)


def write_card(out_dir: Path, args, train_ds, eval_ds, peft_config, trainer=None) -> None:
    """Карточка обучения: чем, на чём и с каким результатом обучено.

    `target_modules` в peft — множество, а оно не сериализуется в JSON; приводим
    к отсортированному списку. История потерь берётся из состояния тренера,
    чтобы карточка сама по себе отвечала на вопрос «обучение вообще шло?».
    """
    history = []
    if trainer is not None:
        history = [
            {k: v for k, v in entry.items() if k in ("step", "epoch", "loss", "eval_loss")}
            for entry in getattr(trainer.state, "log_history", [])
            if "loss" in entry or "eval_loss" in entry
        ]
    card = {
        "base_model": args.base,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(Path(args.dataset)),
        "train_examples": len(train_ds) if train_ds is not None else None,
        "eval_examples": len(eval_ds) if eval_ds is not None else 0,
        "lora": {
            "r": args.rank, "alpha": args.alpha, "dropout": args.dropout,
            "targets": sorted(peft_config.target_modules),
        },
        "hyperparams": {
            "lr": args.lr, "epochs": args.epochs,
            "batch_size": args.batch_size, "grad_accum": args.grad_accum,
            "max_seq_len": args.max_seq_len, "qlora": args.qlora,
        },
        "loss_history": history,
    }
    path = out_dir / "training_card.json"
    path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Карточка обучения: %s", path)


def merge_adapter(base_model: str, out_dir: Path, dtype_key: str) -> Path:
    """Слияние адаптера с базовой моделью — чтобы vLLM поднимал её как обычную."""
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    logger.info("Слияние адаптера с базой…")
    base = AutoModelForCausalLM.from_pretrained(
        base_model, device_map="cpu", trust_remote_code=True,
        **{dtype_key: torch.bfloat16},
    )
    merged = PeftModel.from_pretrained(base, str(out_dir / "adapter")).merge_and_unload()
    merged_dir = out_dir / "merged"
    merged.save_pretrained(str(merged_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_model, trust_remote_code=True).save_pretrained(str(merged_dir))
    logger.info("Слитая модель: %s", merged_dir)
    return merged_dir


def main() -> None:
    p = argparse.ArgumentParser(description="LoRA-дообучение нарративной модели DSS")
    p.add_argument("--base", default="Qwen/Qwen3-14B", help="Базовая модель")
    p.add_argument("--dataset", default="/srv/ai/datasets/narrative")
    p.add_argument("--out", default="/srv/ai/runs/narrative-lora")
    p.add_argument("--qlora", action="store_true", help="4-битная база (для моделей 32B)")
    p.add_argument("--rank", type=int, default=32)
    p.add_argument("--alpha", type=int, default=64)
    p.add_argument("--dropout", type=float, default=0.05)
    p.add_argument("--lr", type=float, default=1e-4)
    p.add_argument("--epochs", type=float, default=2.0)
    p.add_argument("--max-seq-len", type=int, default=4096)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=16)
    p.add_argument("--merge", action="store_true", help="Слить адаптер с базой после обучения")
    p.add_argument("--merge-only", action="store_true",
                   help="Только слияние уже обученного адаптера, без обучения")
    args = p.parse_args()

    check_environment()

    import transformers as _tf0

    if args.merge_only:
        # Обучение уже прошло: сливаем сохранённый адаптер, не повторяя его.
        dtype_key = "dtype" if int(_tf0.__version__.split(".")[0]) >= 5 else "torch_dtype"
        merge_adapter(args.base, Path(args.out), dtype_key)
        return

    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    data_dir = Path(args.dataset)
    train_ds = load_dataset(data_dir / "train.jsonl", tokenizer)
    eval_path = data_dir / "test.jsonl"
    eval_ds = load_dataset(eval_path, tokenizer) if eval_path.exists() else None

    # В transformers 5.x параметр torch_dtype переименован в dtype. Сигнатура
    # from_pretrained принимает **kwargs, поэтому определять по ней нельзя —
    # смотрим версию библиотеки.
    import transformers as _tf

    dtype_key = "dtype" if int(_tf.__version__.split(".")[0]) >= 5 else "torch_dtype"
    model_kwargs: dict = {
        dtype_key: torch.bfloat16,
        "attn_implementation": "sdpa",
        # Явно на GPU: с "auto" при занятой видеопамяти часть весов молча уезжает
        # на CPU («offloaded to the cpu»), и обучение либо падает, либо ползёт.
        "device_map": {"": 0},
        "trust_remote_code": True,
    }
    if args.qlora:
        from transformers import BitsAndBytesConfig

        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    logger.info("Загрузка базовой модели %s (qlora=%s)", args.base, args.qlora)
    model = AutoModelForCausalLM.from_pretrained(args.base, **model_kwargs)
    model.config.use_cache = False

    if args.qlora:
        # При 4-битной базе нужно подготовить модель к обучению: включить
        # градиенты на входах (иначе контрольные точки активаций их обрывают) и
        # привести нормализации к float32. TRL делает это сам не во всех
        # версиях, а молча обучающийся адаптер с нулевыми градиентами выглядит
        # как успешный прогон.
        from peft import prepare_model_for_kbit_training

        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=True,
        )
        logger.info("Модель подготовлена к обучению на 4-битной базе")

    peft_config = LoraConfig(
        r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    out_dir = Path(args.out)
    # Шагов оптимизации всего: примеры / (батч × накопление) × эпохи.
    steps_per_epoch = max(1, len(train_ds) // (args.batch_size * args.grad_accum))
    total_steps = max(1, int(steps_per_epoch * args.epochs))

    wanted = {
        "output_dir": str(out_dir),
        "num_train_epochs": args.epochs,
        "per_device_train_batch_size": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "learning_rate": args.lr,
        "lr_scheduler_type": "cosine",
        "warmup_steps": max(1, round(total_steps * 0.03)),
        "bf16": True,
        "logging_steps": 5,
        "save_strategy": "epoch",
        "eval_strategy": "epoch" if eval_ds is not None else "no",
        "max_length": args.max_seq_len,
        "gradient_checkpointing": True,
        "report_to": [],
        "seed": 42,
        # Потери считаем только по ответу ассистента: иначе модель учится
        # воспроизводить контекст промпта, а не писать заключение. Работает
        # благодаря колонкам prompt/completion (см. load_dataset).
        "completion_only_loss": True,
    }
    # Состав параметров SFTConfig меняется от версии к версии (в TRL 1.13
    # исчез warmup_ratio). Отбрасываем неподдерживаемые, но громко сообщаем —
    # молча потерять, например, маскирование промпта недопустимо.
    supported = {f.name for f in dataclasses.fields(SFTConfig)}
    dropped = sorted(set(wanted) - supported)
    if dropped:
        logger.warning("SFTConfig не поддерживает параметры: %s", ", ".join(dropped))
        if "completion_only_loss" in dropped:
            raise SystemExit(
                "В этой версии TRL нет completion_only_loss — обучение без "
                "маскирования промпта бессмысленно, обновите библиотеку."
            )
    sft_config = SFTConfig(**{k: v for k, v in wanted.items() if k in supported})
    logger.info("Шагов оптимизации: %d (прогрев %d)", total_steps, wanted["warmup_steps"])

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        peft_config=peft_config,
        processing_class=tokenizer,
    )

    trainable = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in trainer.model.parameters())
    logger.info(
        "Обучаемых параметров: %.2f млн из %.0f млн (%.3f %%)",
        trainable / 1e6, total / 1e6, 100 * trainable / max(total, 1),
    )
    if trainable == 0:
        raise SystemExit(
            "Ни один параметр не помечен обучаемым — адаптер не подключился. "
            "Обучение в таком виде бессмысленно."
        )

    logger.info("Старт обучения → %s", out_dir)
    trainer.train()
    trainer.save_model(str(out_dir / "adapter"))
    tokenizer.save_pretrained(str(out_dir / "adapter"))

    write_card(out_dir, args, train_ds, eval_ds, peft_config, trainer)
    logger.info("Адаптер сохранён: %s", out_dir / "adapter")

    if args.merge:
        merged_dir = merge_adapter(args.base, out_dir, dtype_key)
        logger.info(
            "Дальше: прописать %s в VLLM_MODEL на GPU-узле — имя dss-narrative "
            "сохраняется, конфигурация прода не меняется.", merged_dir,
        )


if __name__ == "__main__":
    main()
