#!/usr/bin/env python3
"""
Регистрация языковой модели нарратива в реестре моделей DSS.

Зачем: требование ФИИ.6 — «возможность переобучения, оценки качества,
развертывания новых версий моделей и их отката». Если языковая модель живёт
отдельно от реестра, это требование закрывается для неё только на словах.
Регистрация в той же таблице `ai_models`, что и прогнозные модели, даёт единый
механизм: версия, метрики, статус draft/active/deprecated, активация и откат
через те же эндпоинты `/api/v1/models/{id}/activate` и `/rollback`.

Физически переключение модели — это смена пути в конфигурации inference-сервера;
запись в реестре фиксирует, какая именно версия считается действующей, с какими
метриками и на каких данных обучена.

Запуск:

    python scripts/register_llm_model.py \\
        --version 1.0 \\
        --card /srv/ai/runs/narrative-14b-r32/training_card.json \\
        --eval /srv/ai/artifacts/eval.json \\
        --model-path /srv/ai/runs/narrative-14b-r32/merged \\
        --dataset /srv/ai/datasets/narrative \\
        --status draft
"""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402

MODEL_TYPE = "llm_narrative"


def dataset_fingerprint(dataset_dir: Path) -> dict:
    """Отпечаток обучающего корпуса: состав и хэш содержимого.

    Нужен, чтобы по записи в реестре можно было однозначно сказать, на каких
    данных обучена версия, и воспроизвести обучение.
    """
    info: dict = {"path": str(dataset_dir)}
    for name in ("train.jsonl", "test.jsonl"):
        path = dataset_dir / name
        if not path.exists():
            continue
        data = path.read_bytes()
        info[name] = {
            "examples": data.count(b"\n"),
            "sha256": hashlib.sha256(data).hexdigest()[:32],
            "bytes": len(data),
        }
    stats = dataset_dir / "stats.json"
    if stats.exists():
        info["stats"] = json.loads(stats.read_text(encoding="utf-8"))
    return info


async def register(args: argparse.Namespace) -> int:
    card = json.loads(Path(args.card).read_text(encoding="utf-8")) if args.card else {}
    evaluation = json.loads(Path(args.eval).read_text(encoding="utf-8")) if args.eval else {}
    summary = evaluation.get("summary", evaluation)

    parameters = {
        "base_model": card.get("base_model"),
        "adapter": card.get("lora"),
        "hyperparams": card.get("hyperparams"),
        "model_path": args.model_path,
        "dataset": dataset_fingerprint(Path(args.dataset)) if args.dataset else None,
        "trained_at": card.get("trained_at"),
        "served_model_name": args.served_name,
    }
    metrics = {
        "evaluation": summary.get("configs"),
        "pairwise": summary.get("pairwise"),
        "decision": summary.get("решение"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }

    async with AsyncSessionLocal() as db:
        created_by = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
        if created_by is None:
            raise SystemExit("В базе нет пользователей — некому приписать модель")

        existing = (await db.execute(text("""
            SELECT id FROM ai_models
            WHERE model_type = :mt AND version = :ver AND indicator_id IS NULL
            LIMIT 1
        """), {"mt": MODEL_TYPE, "ver": args.version})).scalar()

        params = {
            "name": args.name,
            "ver": args.version,
            "mt": MODEL_TYPE,
            "parameters": json.dumps(parameters, ensure_ascii=False),
            "metrics": json.dumps(metrics, ensure_ascii=False),
            "status": args.status,
            "created_by": created_by,
            "trained_at": datetime.now(timezone.utc),
        }
        if existing:
            params["id"] = existing
            await db.execute(text("""
                UPDATE ai_models
                SET name = :name, parameters = CAST(:parameters AS jsonb),
                    metrics = CAST(:metrics AS jsonb), status = :status,
                    trained_at = :trained_at
                WHERE id = :id
            """), params)
            model_id = existing
        else:
            model_id = (await db.execute(text("""
                INSERT INTO ai_models
                    (name, version, model_type, parameters, metrics, status, trained_at, created_by)
                VALUES
                    (:name, :ver, :mt, CAST(:parameters AS jsonb), CAST(:metrics AS jsonb),
                     :status, :trained_at, :created_by)
                RETURNING id
            """), params)).scalar()

        if args.status == "active":
            # Прежняя действующая версия языковой модели уходит в deprecated —
            # та же дисциплина, что и у прогнозных моделей.
            await db.execute(text("""
                UPDATE ai_models SET status = 'deprecated'
                WHERE model_type = :mt AND status = 'active' AND id <> :id
            """), {"mt": MODEL_TYPE, "id": model_id})

        await db.commit()

    print(json.dumps({
        "model_id": str(model_id),
        "model_type": MODEL_TYPE,
        "version": args.version,
        "status": args.status,
        "base_model": parameters["base_model"],
        "dataset_examples": (parameters.get("dataset") or {}).get("train.jsonl", {}).get("examples"),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Регистрация языковой модели в реестре DSS")
    p.add_argument("--version", required=True)
    p.add_argument("--name", default="Языковая модель аналитических заключений DSS")
    p.add_argument("--card", help="training_card.json из каталога обучения")
    p.add_argument("--eval", help="Отчёт сравнения моделей (eval_narrative.py)")
    p.add_argument("--model-path", help="Путь к модели на inference-узле")
    p.add_argument("--dataset", help="Каталог обучающего корпуса")
    p.add_argument("--served-name", default="dss-narrative")
    p.add_argument("--status", default="draft", choices=["draft", "active", "deprecated"])
    args = p.parse_args()
    raise SystemExit(asyncio.run(register(args)))


if __name__ == "__main__":
    main()
