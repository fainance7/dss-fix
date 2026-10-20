#!/usr/bin/env python3
"""
Батч-тренировка прогнозных моделей DSS по направлениям (областной срез).

Для каждого показателя выбранных направлений берёт областной ряд (среднее по
районам) из indicator_values — тот же источник, что и онлайн-прогноз, — обучает
обе модели (linear, holt_winters), делает hold-out бэктест и оставляет лучшую по
MAE со статусом active, вторую — draft. Пишет в ai_models тем же способом, что и
одиночный scripts/train.py (upsert по name+version, model_blob со снимком ряда).

В отличие от model_registry.activate_model (которая держит одну active-модель на
тип ГЛОБАЛЬНО) здесь статус пишется напрямую: модели привязаны к показателю
(indicator_id), поэтому active-моделей много — по одной на показатель на тип.

Запуск (внутри контейнера ai-service, где DATABASE_URL уже указывает на postgres):
  docker exec dss-ai-service python scripts/train_batch.py --dry-run
  docker exec dss-ai-service python scripts/train_batch.py
Флаги: --directions climate labor ... , --min-points N (деф. 6), --dry-run.
"""
import argparse
import asyncio
import json
import math
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
    _fit_holt_winters,
    _fit_linear,
    get_historical_series,
)
from app.services.xai import (  # noqa: E402
    compute_feature_importance_from_series,
    compute_model_metrics,
)

ARTIFACT_VERSION = 1
MODEL_TYPES = ("linear", "holt_winters")
DEFAULT_DIRECTIONS = ("climate", "labor", "social_demog", "economy")


def _fit(model_type: str, series: list[float], horizon: int) -> list[float]:
    if model_type == "holt_winters":
        return _fit_holt_winters(series, horizon)
    return _fit_linear(series, horizon)


def _mae(metrics: dict) -> float:
    v = metrics.get("mae")
    if isinstance(v, (int, float)) and not math.isnan(v):
        return float(v)
    return float("inf")


def _backtest(model_type: str, series: list[float]) -> tuple[dict, int]:
    """Hold-out бэктест: последние holdout точек — для метрик качества."""
    holdout = min(max(3, len(series) // 4), 12)
    train_series, test_series = series[:-holdout], series[-holdout:]
    predicted = _fit(model_type, train_series, horizon=holdout)
    return compute_model_metrics(test_series, predicted, train_series=train_series), holdout


async def _first_user(db) -> str:
    row = (
        await db.execute(text("SELECT id FROM users ORDER BY created_at ASC LIMIT 1"))
    ).first()
    if row is None:
        raise SystemExit("В БД нет пользователей — укажите --created-by <uuid>")
    return str(row[0])


async def _indicators_for(db, directions: list[str], names: list[str] | None = None):
    rows = (
        await db.execute(
            text(
                """
                SELECT i.id, i.name, d.code
                FROM indicators i
                JOIN directions d ON d.id = i.direction_id
                WHERE {filter_clause}
                ORDER BY d.code, i.sort_order, i.name
                """.format(
                    filter_clause=(
                        "i.name = ANY(:names)" if names else "d.code = ANY(:codes)"
                    )
                )
            ),
            {"names": list(names)} if names else {"codes": list(directions)},
        )
    ).fetchall()
    return [(str(r[0]), r[1], r[2]) for r in rows]


async def _write_model(db, params: dict) -> None:
    # Ключ апсерта — показатель+тип+версия (а НЕ имя: имена показателей могут
    # дублироваться, и апсерт по имени схлопнул бы разные показатели в одну модель).
    existing = (
        await db.execute(
            text(
                "SELECT id FROM ai_models "
                "WHERE indicator_id = :indicator_id AND model_type = :model_type "
                "AND version = :version"
            ),
            {
                "indicator_id": params["indicator_id"],
                "model_type": params["model_type"],
                "version": params["version"],
            },
        )
    ).first()
    if existing:
        params = {**params, "id": str(existing[0])}
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
    else:
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
                """
            ),
            params,
        )


async def run(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        created_by = args.created_by or await _first_user(db)
        indicators = await _indicators_for(db, args.directions, args.indicators)

        trained = 0
        skipped: list[tuple[str, str]] = []
        per_dir: dict[str, dict] = {}

        for ind_id, ind_name, dir_code in indicators:
            try:
                series = await get_historical_series(
                    db,
                    region_id=None,  # областной срез: level 1, при нехватке — агрегация районов
                    periods=args.max_periods,
                    indicator_id=UUID(ind_id),
                )
            except ValueError:
                skipped.append((ind_name, "нет данных (<3 точек)"))
                continue
            if len(series) < args.min_points:
                skipped.append((ind_name, f"{len(series)} точек (<{args.min_points})"))
                continue

            results = {mt: _backtest(mt, series) for mt in MODEL_TYPES}
            best = min(MODEL_TYPES, key=lambda mt: _mae(results[mt][0]))
            feature_importance = compute_feature_importance_from_series(series)

            best_metrics = results[best][0]
            bucket = per_dir.setdefault(
                dir_code, {"n": 0, "mape_sum": 0.0, "mae_sum": 0.0, "good": 0}
            )
            bucket["n"] += 1
            bucket["mape_sum"] += best_metrics.get("mape", 0.0)
            bucket["mae_sum"] += best_metrics.get("mae", 0.0)
            if best_metrics.get("mape", 100.0) <= 15.0:
                bucket["good"] += 1

            print(
                f"[{dir_code}] {ind_name[:52]:52s} точек={len(series):3d} "
                f"→ {best:12s} MAE={best_metrics['mae']} MAPE={best_metrics['mape']}%"
            )

            for mt in MODEL_TYPES:
                metrics, holdout = results[mt]
                status = "active" if mt == best else "draft"
                name = f"{ind_name} [{mt}] / область #{ind_id[:8]}"
                artifact = {
                    "artifact_version": ARTIFACT_VERSION,
                    "model_type": mt,
                    "indicator_id": ind_id,
                    "region_id": None,
                    "trained_at": datetime.now(timezone.utc).isoformat(),
                    "n_obs": len(series),
                    "horizon": args.horizon,
                    "series_tail": series[-36:],
                }
                parameters = {
                    "indicator_id": ind_id,
                    "indicator_name": ind_name,
                    "direction_code": dir_code,
                    "region_id": None,
                    "horizon": args.horizon,
                    "holdout": holdout,
                    "source": "indicator_values",
                    "n_obs": len(series),
                    "selected": (mt == best),
                }
                metrics_full = {**metrics, "feature_importance": feature_importance}
                if args.dry_run:
                    continue
                await _write_model(
                    db,
                    {
                        "name": name,
                        "version": args.version,
                        "model_type": mt,
                        "indicator_id": ind_id,
                        "parameters": json.dumps(parameters, ensure_ascii=False),
                        "metrics": json.dumps(metrics_full, ensure_ascii=False),
                        "model_blob": pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL),
                        "status": status,
                        "created_by": created_by,
                    },
                )
            trained += 1

        if not args.dry_run:
            await db.commit()

        print("\n" + "=" * 64)
        print(f"Обучено показателей: {trained}   пропущено: {len(skipped)}"
              f"   {'(dry-run, без записи)' if args.dry_run else ''}")
        print("Сводка по направлениям (лучшая модель на показатель):")
        for dc, b in sorted(per_dir.items()):
            n = b["n"]
            print(
                f"  {dc:14s} обучено={n:3d}  ср.MAPE={b['mape_sum']/n:6.2f}%  "
                f"ср.MAE={b['mae_sum']/n:10.2f}  MAPE≤15%: {b['good']}/{n}"
            )
        if skipped:
            print(f"\nПропущено ({len(skipped)}):")
            for nm, why in skipped[:40]:
                print(f"  — {nm[:60]:60s} {why}")


def main() -> None:
    p = argparse.ArgumentParser(description="Батч-тренировка прогнозных моделей DSS (область)")
    p.add_argument("--indicators", nargs="+",
                   help="Переобучить только эти показатели по имени; отменяет --directions")
    p.add_argument("--directions", nargs="+", default=list(DEFAULT_DIRECTIONS),
                   help="Коды направлений (деф.: climate labor social_demog economy)")
    p.add_argument("--min-points", type=int, default=6, help="Минимум точек ряда (деф. 6)")
    p.add_argument("--version", default="1.0")
    p.add_argument("--horizon", type=int, default=24, help="Горизонт прогноза (шагов)")
    p.add_argument("--max-periods", type=int, default=240, help="Максимум исторических точек")
    p.add_argument("--created-by", help="UUID владельца (деф. — первый в users)")
    p.add_argument("--dry-run", action="store_true", help="Только метрики, без записи")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
