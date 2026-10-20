#!/usr/bin/env python3
"""
Offline-тренировка прогнозных моделей DSS.

Берёт исторический ряд показателя из indicator_values (тот же источник, что и
онлайн-прогноз), делает hold-out бэктест для метрик качества, сериализует
артефакт модели и сохраняет/обновляет запись в ai_models (model_blob, metrics,
trained_at, status). Запускается вручную/по расписанию вне веб-процесса.

Примеры:
  # по имени показателя, областной срез, линейная модель, активировать
  python ai-service/scripts/train.py --indicator-name "Индекс физического объема ВРП" \
      --model-type linear --version 1.0 --activate

  # по id показателя и конкретному району, Хольт-Уинтерс
  python ai-service/scripts/train.py --indicator-id <uuid> --region-id <uuid> \
      --model-type holt_winters --version 1.0 --activate

Артефакт (model_blob): для линейной/Хольта-Уинтерса оценщики переобучаются
детерминированно тем же кодом, поэтому в blob кладётся конфигурация + снимок
обучающего ряда (а не «тяжёлый»/version-fragile pickle statsmodels-объекта).
"""
import argparse
import asyncio
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

# Позволяем `import app...` при запуске как отдельного скрипта.
AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.forecasting import (  # noqa: E402
    _fit_arima,
    _fit_holt_winters,
    _fit_linear,
    get_historical_series,
)
from app.services.xai import (  # noqa: E402
    compute_feature_importance_from_series,
    compute_model_metrics,
)

ARTIFACT_VERSION = 1
MODEL_TYPES = ("linear", "holt_winters", "arima")


def _fit(model_type: str, series: list[float], horizon: int) -> list[float]:
    if model_type == "holt_winters":
        return _fit_holt_winters(series, horizon)
    if model_type == "arima":
        return _fit_arima(series, horizon)
    return _fit_linear(series, horizon)


async def _resolve_indicator(db, indicator_id: str | None, indicator_name: str | None):
    if indicator_id:
        row = (
            await db.execute(
                text("SELECT id, name FROM indicators WHERE id = :id"),
                {"id": indicator_id},
            )
        ).first()
        if row is None:
            raise SystemExit(f"Показатель с id={indicator_id} не найден")
        return str(row[0]), row[1]

    rows = (
        await db.execute(
            text("SELECT id, name FROM indicators WHERE name ILIKE :q ORDER BY name LIMIT 5"),
            {"q": f"%{indicator_name}%"},
        )
    ).fetchall()
    if not rows:
        raise SystemExit(f"Показатель по имени «{indicator_name}» не найден")
    if len(rows) > 1:
        names = "; ".join(r[1] for r in rows)
        raise SystemExit(f"Неоднозначное имя «{indicator_name}», подходит несколько: {names}")
    return str(rows[0][0]), rows[0][1]


async def _resolve_created_by(db, created_by: str | None) -> str:
    if created_by:
        return created_by
    row = (
        await db.execute(text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1"))
    ).first()
    if row is None:
        raise SystemExit("В БД нет пользователей — укажите --created-by <uuid>")
    return str(row[0])


async def train(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        indicator_id, indicator_name = await _resolve_indicator(
            db, args.indicator_id, args.indicator_name
        )
        created_by = await _resolve_created_by(db, args.created_by)

        region_uuid = UUID(args.region_id) if args.region_id else None
        try:
            series = await get_historical_series(
                db,
                region_id=region_uuid,
                model_type=args.model_type,
                periods=args.max_periods,
                indicator_id=UUID(indicator_id),
            )
        except ValueError as exc:
            raise SystemExit(str(exc))
        if len(series) < 6:
            raise SystemExit(
                f"Недостаточно точек для тренировки/бэктеста: {len(series)} (нужно ≥ 6)"
            )

        # Hold-out бэктест: последние holdout точек — для метрик.
        holdout = min(max(3, len(series) // 4), 12)
        train_series, test_series = series[:-holdout], series[-holdout:]
        predicted = _fit(args.model_type, train_series, horizon=holdout)
        metrics = compute_model_metrics(test_series, predicted, train_series=train_series)
        feature_importance = compute_feature_importance_from_series(series)

        # Финальный фит на полном ряду — пример прогноза на горизонт.
        forecast_preview = _fit(args.model_type, series, horizon=args.horizon)

        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "model_type": args.model_type,
            "indicator_id": indicator_id,
            "region_id": args.region_id,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "n_obs": len(series),
            "horizon": args.horizon,
            "series_tail": series[-36:],  # снимок для детерминированного реинференса
        }
        blob = pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL)

        region_suffix = f" / {args.region_id[:8]}" if args.region_id else " / область"
        name = args.name or f"{indicator_name} [{args.model_type}]{region_suffix}"
        status = "active" if args.activate else "draft"
        parameters = {
            "indicator_id": indicator_id,
            "indicator_name": indicator_name,
            "region_id": args.region_id,
            "horizon": args.horizon,
            "holdout": holdout,
            "source": "indicator_values",
            "n_obs": len(series),
        }
        metrics_full = {**metrics, "feature_importance": feature_importance}

        print(
            f"Показатель: {indicator_name}\n"
            f"Регион: {args.region_id or 'область (среднее по районам)'}\n"
            f"Тип модели: {args.model_type}  точек: {len(series)}  hold-out: {holdout}\n"
            f"Метрики бэктеста: MAE={metrics['mae']} RMSE={metrics['rmse']} "
            f"R2={metrics['r2']} MAPE={metrics['mape']}%\n"
            f"Прогноз (первые 3 из {args.horizon}): "
            f"{[round(v, 2) for v in forecast_preview[:3]]}\n"
            f"Артефакт: {len(blob)} байт  статус: {status}"
        )

        if args.dry_run:
            print("dry-run: запись в ai_models пропущена")
            return

        existing = (
            await db.execute(
                text("SELECT id FROM ai_models WHERE name = :name AND version = :version"),
                {"name": name, "version": args.version},
            )
        ).first()

        params = {
            "name": name,
            "version": args.version,
            "model_type": args.model_type,
            "indicator_id": indicator_id,
            "parameters": json.dumps(parameters, ensure_ascii=False),
            "metrics": json.dumps(metrics_full, ensure_ascii=False),
            "model_blob": blob,
            "status": status,
            "created_by": created_by,
        }
        if existing:
            params["id"] = str(existing[0])
            await db.execute(
                text(
                    """
                    UPDATE ai_models SET
                        model_type = :model_type,
                        indicator_id = :indicator_id,
                        parameters = CAST(:parameters AS JSONB),
                        metrics = CAST(:metrics AS JSONB),
                        model_blob = :model_blob,
                        status = :status,
                        trained_at = NOW()
                    WHERE id = :id
                    """
                ),
                params,
            )
            print(f"Обновлена модель ai_models.id={existing[0]}")
        else:
            inserted = (
                await db.execute(
                    text(
                        """
                        INSERT INTO ai_models
                            (name, version, model_type, indicator_id, parameters,
                             metrics, model_blob, status, trained_at, created_by)
                        VALUES
                            (:name, :version, :model_type, :indicator_id,
                             CAST(:parameters AS JSONB), CAST(:metrics AS JSONB),
                             :model_blob, :status, NOW(), :created_by)
                        RETURNING id
                        """
                    ),
                    params,
                )
            ).first()
            print(f"Создана модель ai_models.id={inserted[0]}")

        await db.commit()


def main() -> None:
    p = argparse.ArgumentParser(description="Offline-тренировка прогнозных моделей DSS")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--indicator-id", help="UUID показателя")
    g.add_argument("--indicator-name", help="Имя показателя (ILIKE, должно быть однозначным)")
    p.add_argument("--region-id", help="UUID района; без него — областной срез (среднее по районам)")
    p.add_argument("--model-type", choices=MODEL_TYPES, default="linear")
    p.add_argument("--version", default="1.0")
    p.add_argument("--name", help="Имя модели (по умолчанию из показателя/региона)")
    p.add_argument("--horizon", type=int, default=24, help="Горизонт прогноза (мес.)")
    p.add_argument("--max-periods", type=int, default=240, help="Максимум исторических точек")
    p.add_argument("--created-by", help="UUID пользователя-владельца (по умолчанию первый в users)")
    p.add_argument("--activate", action="store_true", help="status=active (иначе draft)")
    p.add_argument("--dry-run", action="store_true", help="Посчитать и показать, без записи")
    args = p.parse_args()

    asyncio.run(train(args))


if __name__ == "__main__":
    main()
