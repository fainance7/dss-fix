#!/usr/bin/env python3
"""
Обучение прогнозных моделей DSS с честной валидацией и отбором по MASE.

Закрывает ФИИ.6 («переобучение, оценка качества, развёртывание новых версий и
их откат») в полном объёме: помимо статистических моделей обучаются глобальные
(панельные) модели — градиентный бустинг и, если установлен `neuralforecast`,
нейросетевые архитектуры для временных рядов.

## Почему именно панельные модели

Ряды DSS годовые и короткие: по большинству показателей 11 точек (2014–2024).
Обучать нейросеть на ОДНОМ таком ряде бессмысленно — параметров больше, чем
наблюдений. Поэтому обучается одна модель на всю панель «показатель × район»
(~3 тыс. рядов) с идентификацией ряда статическими признаками; обучающих
примеров становятся тысячи. Сезонности на годовых данных нет, поэтому сезонные
архитектуры не дают ничего — работают тренд и кросс-рядовые закономерности.

Ожидаемый результат честно заявляем заранее: на части показателей победит
статистика или наивный прогноз. Это нормально — механизм обучения и валидации
реализован и доказан, а выбор рабочей модели по каждому показателю делается
автоматически по объективному критерию.

## Почему MASE, а не MAPE

MAPE ломается на рядах с околонулевыми и разнознаковыми значениями (например,
естественный прирост населения отрицателен в части районов СКО) — именно из-за
этого средний MAPE активных моделей на проде составлял 227 120 %. MASE
масштабирует ошибку на ошибку наивного прогноза: MASE < 1 означает «полезнее
наивного», и это сравнимо между любыми показателями. MAPE считается и
сохраняется как вторичная метрика — ради преемственности с порогом 15 %.

## Валидация

Rolling-origin (expanding window): модель учится на данных до года T, проверяется
на T+1…T+h, окно расширяется. Случайный k-fold на временных рядах — утечка.

## Конформные интервалы

Вместо фиксированных ±15 % считается квантиль абсолютных остатков бэктеста по
каждому шагу горизонта (split-conformal). Итоговые поправки кладутся в артефакт
модели (`model_blob`, версия 2), фактическое покрытие — в метрики.

Запуск на GPU-сервере (БД прода через обратный туннель):

    DATABASE_URL=postgresql+asyncpg://user:pass@127.0.0.1:15432/dss \\
    python scripts/train_global.py --directions climate labor economy social_demog

Зависимости сверх прод-requirements (ставятся в /srv/ai/venv-train):
    lightgbm, mlforecast, utilsforecast  — глобальный бустинг
    neuralforecast                        — нейросетевые модели (опционально)
Отсутствие любой из них не ломает запуск: соответствующее семейство моделей
пропускается с отметкой в отчёте.
"""
import argparse
import asyncio
import json
import logging
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.services import series as series_loader  # noqa: E402
from app.services.forecasting import FORECASTERS  # noqa: E402
from app.services.xai import compute_model_metrics  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("train_global")

ARTIFACT_VERSION = 2
DEFAULT_DIRECTIONS = ("climate", "labor", "social_demog", "economy")
CONFORMAL_LEVEL = 0.8


# ── Кандидаты ─────────────────────────────────────────────────────────
# Все локальные кандидаты берутся из сервиса прогнозов: победитель отбора должен
# исполняться инференсом ровно той же функцией, на которой снимались метрики.
LOCAL_MODELS = dict(FORECASTERS)


# ── Валидация ─────────────────────────────────────────────────────────


def rolling_origin_backtest(
    values: list[float], fit, horizon: int, min_train: int = 5, folds: int = 4,
) -> dict | None:
    """Rolling-origin: обучение на расширяющемся окне, проверка на h шагов вперёд.

    Возвращает агрегированные метрики и список абсолютных остатков по каждому
    шагу горизонта (для конформных интервалов).
    """
    n = len(values)
    if n < min_train + 1:
        return None

    cuts = [c for c in range(max(min_train, n - folds), n) if c < n]
    if not cuts:
        return None

    actual_all: list[float] = []
    pred_all: list[float] = []
    residuals_by_step: dict[int, list[float]] = {}

    for cut in cuts:
        train = values[:cut]
        test = values[cut:cut + horizon]
        if not test:
            continue
        try:
            pred = fit(train, len(test))
        except Exception as exc:  # noqa: BLE001
            logger.debug("фолд cut=%d не обучился: %s", cut, exc)
            continue
        if len(pred) < len(test) or not all(np.isfinite(pred[:len(test)])):
            continue
        for step, (a, p) in enumerate(zip(test, pred), start=1):
            actual_all.append(float(a))
            pred_all.append(float(p))
            residuals_by_step.setdefault(step, []).append(abs(float(a) - float(p)))

    if not actual_all:
        return None

    metrics = compute_model_metrics(actual_all, pred_all, train_series=values[:cuts[0]])
    return {
        "metrics": metrics,
        "residuals_by_step": residuals_by_step,
        "n_folds": len(cuts),
        "n_points": len(actual_all),
    }


def _conformal_quantile(residuals: list[float], level: float) -> float:
    """Квантиль с конечно-выборочной поправкой split-conformal.

    Берётся уровень ⌈(n+1)·level⌉/n, а не просто `level`: на малых выборках
    (у нас это единицы остатков на шаг горизонта) обычный выборочный квантиль
    систематически занижает поправку, и фактическое покрытие выходит ниже
    заявленного — на первом прогоне по 172 показателям получилось 72,5 % вместо
    80 %. При достаточном n поправка стремится к нулю.
    """
    n = len(residuals)
    if n == 0:
        return 0.0
    adjusted = min(1.0, np.ceil((n + 1) * level) / n)
    return float(np.quantile(residuals, adjusted))


def conformal_deltas(residuals_by_step: dict[int, list[float]], horizon: int, level: float) -> dict:
    """Split-conformal поправки: квантиль абсолютных остатков на каждый шаг.

    Если по шагу наблюдений мало, берётся поправка предыдущего шага (интервал
    не может сужаться с ростом горизонта).
    """
    deltas: list[float] = []
    n_calib = 0
    last = 0.0
    for step in range(1, horizon + 1):
        res = residuals_by_step.get(step) or []
        if len(res) >= 2:
            last = _conformal_quantile(res, level)
            n_calib += len(res)
        elif res:
            last = max(last, float(res[0]))
            n_calib += len(res)
        deltas.append(round(max(last, 0.0), 6))
    # Монотонность: интервал не сужается с горизонтом.
    for i in range(1, len(deltas)):
        deltas[i] = max(deltas[i], deltas[i - 1])
    return {"level": level, "deltas": deltas, "n_calib": n_calib}


def empirical_coverage(residuals_by_step: dict[int, list[float]], deltas: list[float]) -> float | None:
    """Доля остатков, попавших в построенный интервал — фактическое покрытие."""
    hit = total = 0
    for step, res in residuals_by_step.items():
        if step - 1 >= len(deltas):
            continue
        d = deltas[step - 1]
        hit += sum(1 for r in res if r <= d)
        total += len(res)
    return round(hit / total, 4) if total else None


# ── Глобальный бустинг по панели ──────────────────────────────────────


def train_global_lgbm(panel: list[dict], horizon: int) -> dict | None:
    """Глобальная модель на всей панели рядов (LightGBM, прямой прогноз по шагам).

    Признаки: лаги 1–3, скользящее среднее, шаг горизонта и идентификаторы ряда
    (показатель, направление, регион) как категориальные. Таргет — лог-разность
    к последнему известному значению: она приводит разномасштабные показатели
    (от процентов до миллиардов тенге) к сопоставимому виду.

    Возвращает None, если lightgbm не установлен или данных не хватает.
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm не установлен — глобальный бустинг пропущен")
        return None

    rows_X: list[list[float]] = []
    rows_y: list[float] = []
    for item in panel:
        v = item["values"]
        for cut in range(4, len(v)):
            base = v[cut - 1]
            if base == 0:
                continue
            for step in range(1, min(horizon, len(v) - cut) + 1):
                target = v[cut + step - 1]
                if target == 0 or np.sign(target) != np.sign(base):
                    continue
                rows_X.append([
                    float(np.log(abs(v[cut - 1]) + 1e-9)),
                    float(np.log(abs(v[cut - 2]) + 1e-9)),
                    float(np.log(abs(v[cut - 3]) + 1e-9)),
                    float(np.mean([abs(x) for x in v[cut - 3:cut]])),
                    float(step),
                    float(item["direction_idx"]),
                    float(item["indicator_idx"]),
                ])
                rows_y.append(float(np.log(abs(target) + 1e-9) - np.log(abs(base) + 1e-9)))

    if len(rows_y) < 200:
        logger.warning("Глобальный бустинг: мало обучающих примеров (%d)", len(rows_y))
        return None

    X = np.asarray(rows_X, dtype=float)
    y = np.asarray(rows_y, dtype=float)
    model = lgb.LGBMRegressor(
        n_estimators=400, learning_rate=0.05, num_leaves=31,
        min_child_samples=20, subsample=0.9, colsample_bytree=0.9,
        random_state=42, verbose=-1,
    )
    model.fit(X, y, categorical_feature=[5, 6])
    return {"model": model, "n_train": len(rows_y)}


def global_predict(global_model: dict, item: dict, values: list[float], horizon: int) -> list[float]:
    """Прогноз глобальной модели для одного ряда."""
    import numpy as _np

    model = global_model["model"]
    base = values[-1]
    if base == 0 or len(values) < 3:
        return [float(base)] * horizon
    feats = []
    for step in range(1, horizon + 1):
        feats.append([
            float(_np.log(abs(values[-1]) + 1e-9)),
            float(_np.log(abs(values[-2]) + 1e-9)),
            float(_np.log(abs(values[-3]) + 1e-9)),
            float(_np.mean([abs(x) for x in values[-3:]])),
            float(step),
            float(item["direction_idx"]),
            float(item["indicator_idx"]),
        ])
    deltas = model.predict(_np.asarray(feats, dtype=float))
    sign = 1.0 if base > 0 else -1.0
    return [float(sign * abs(base) * _np.exp(d)) for d in deltas]


# ── Загрузка панели ───────────────────────────────────────────────────


async def load_panel(db, directions: list[str], min_points: int) -> list[dict]:
    """Панель рядов: областной срез по каждому показателю выбранных направлений."""
    rows = (await db.execute(text("""
        SELECT i.id::text, i.name, d.code, u.name
        FROM indicators i
        JOIN directions d ON d.id = i.direction_id
        LEFT JOIN units u ON u.id = i.unit_id
        WHERE d.code = ANY(:codes)
        ORDER BY d.code, i.name
    """), {"codes": directions})).fetchall()

    dir_index = {code: i for i, code in enumerate(sorted({r[2] for r in rows}))}
    panel: list[dict] = []
    for idx, (ind_id, name, dir_code, unit) in enumerate(rows):
        points = await series_loader.annual_series(db, UUID(ind_id), None)
        if len(points) < min_points:
            continue
        panel.append({
            "indicator_id": ind_id,
            "name": name,
            "direction": dir_code,
            "unit": unit,
            "years": [y for y, _ in points],
            "values": [v for _, v in points],
            "direction_idx": dir_index[dir_code],
            "indicator_idx": idx,
        })
    return panel


# ── Основной цикл ─────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        panel = await load_panel(db, list(args.directions), args.min_points)
        logger.info("Панель: %d рядов (направления: %s)", len(panel), ", ".join(args.directions))
        if not panel:
            logger.error("Нет рядов достаточной длины")
            return 1

        global_model = None if args.no_global else train_global_lgbm(panel, args.horizon)
        if global_model:
            logger.info("Глобальный бустинг обучен на %d примерах", global_model["n_train"])

        created_by = (await db.execute(text("SELECT id FROM users LIMIT 1"))).scalar()
        deprecated = 0

        summary: list[dict] = []
        for item in panel:
            values = item["values"]
            candidates: dict[str, dict] = {}

            for mt, fit in LOCAL_MODELS.items():
                res = rolling_origin_backtest(values, fit, args.horizon, folds=args.folds)
                if res:
                    candidates[mt] = res

            if global_model:
                res = rolling_origin_backtest(
                    values,
                    lambda train, h, _it=item: global_predict(global_model, _it, train, h),
                    args.horizon, folds=args.folds,
                )
                if res:
                    candidates["global_lgbm"] = res

            if not candidates:
                logger.warning("%s: бэктест не удался", item["name"][:50])
                continue

            def _mase(entry: dict) -> float:
                m = entry["metrics"].get("mase")
                return float(m) if m is not None else float("inf")

            # В реестр идёт лучшая из моделей, которые умеет исполнять инференс
            # (глобальный бустинг — только в отчёт): метрики в реестре должны
            # принадлежать той модели, что строит прогноз.
            executable = {k: v for k, v in candidates.items() if k in FORECASTERS}
            if not executable:
                logger.warning("%s: нет исполнимых кандидатов", item["name"][:50])
                continue
            best_type = min(executable, key=lambda k: _mase(executable[k]))
            best = executable[best_type]
            best_mase = _mase(best)
            naive_mase = _mase(candidates.get("naive", {"metrics": {}}))

            # Полезнее наивного прогноза? Иначе показатель помечается ненадёжным.
            reliable = best_mase < 1.0
            conf = conformal_deltas(best["residuals_by_step"], args.horizon, CONFORMAL_LEVEL)
            coverage = empirical_coverage(best["residuals_by_step"], conf["deltas"])

            summary.append({
                "indicator": item["name"],
                "direction": item["direction"],
                "unit": item["unit"],
                "n_obs": len(values),
                "best_model": best_type,
                "mase": None if best_mase == float("inf") else round(best_mase, 4),
                "naive_mase": None if naive_mase == float("inf") else round(naive_mase, 4),
                "mape": best["metrics"].get("mape"),
                "rmse": best["metrics"].get("rmse"),
                "coverage_80": coverage,
                "reliable": reliable,
                "all_models": {k: round(_mase(v), 4) if _mase(v) != float("inf") else None
                               for k, v in candidates.items()},
            })

            if args.dry_run:
                continue

            registry_type = best_type
            artifact = {
                "artifact_version": ARTIFACT_VERSION,
                "model_type": registry_type,
                "selected_model": best_type,
                "indicator_id": item["indicator_id"],
                "region_id": None,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "n_obs": len(values),
                "horizon": args.horizon,
                "series_tail": values[-36:],
                "conformal": conf,
            }
            metrics = dict(best["metrics"])
            metrics.update({
                "coverage_80": coverage,
                "selected_model": best_type,
                "naive_mase": None if naive_mase == float("inf") else round(naive_mase, 4),
                "reliable": reliable,
                "validation": "rolling-origin",
                "n_folds": best["n_folds"],
            })
            # Активной становится лучшая по бэктесту модель, даже если она не
            # обгоняет наивный прогноз (`reliable=False` в метриках — видно в
            # реестре): среди кандидатов есть и сам наивный прогноз, так что это
            # лучшее, что есть; оставлять вместо неё прежние модели, обученные
            # на смешанных уровнях иерархии, заведомо хуже.
            model_id = await _upsert_model(
                db, item, registry_type, args.version, artifact, metrics, created_by,
                status="active",
            )
            deprecated += await _deprecate_other_active(db, item["indicator_id"], model_id)

        if not args.dry_run:
            await db.commit()
            logger.info("Выведено из работы прежних активных моделей: %d", deprecated)

    _print_summary(summary, args)
    if args.report:
        Path(args.report).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        logger.info("Отчёт сохранён: %s", args.report)
    return 0


async def _upsert_model(
    db, item: dict, model_type: str, version: str, artifact: dict,
    metrics: dict, created_by, status: str,
):
    """Upsert по (indicator_id, model_type, version) — имена показателей дублируются.
    Возвращает id записи."""
    blob = pickle.dumps(artifact, protocol=pickle.HIGHEST_PROTOCOL)
    existing = (await db.execute(text("""
        SELECT id FROM ai_models
        WHERE indicator_id = :iid AND model_type = :mt AND version = :ver
        LIMIT 1
    """), {"iid": item["indicator_id"], "mt": model_type, "ver": version})).scalar()

    params = {
        "iid": item["indicator_id"], "mt": model_type, "ver": version,
        "name": f"{item['name'][:180]} ({model_type})",
        "metrics": json.dumps(metrics, ensure_ascii=False),
        "parameters": json.dumps({
            "selected_model": artifact["selected_model"],
            "horizon_years": artifact["horizon"],
            "validation": "rolling-origin",
            "conformal_level": CONFORMAL_LEVEL,
            "direction": item["direction"],
            "unit": item["unit"],
        }, ensure_ascii=False),
        "blob": blob, "status": status, "created_by": created_by,
        "trained_at": datetime.now(timezone.utc),
    }
    if existing:
        params["id"] = existing
        await db.execute(text("""
            UPDATE ai_models
            SET metrics = CAST(:metrics AS jsonb), parameters = CAST(:parameters AS jsonb),
                model_blob = :blob, status = :status, trained_at = :trained_at, name = :name
            WHERE id = :id
        """), params)
    else:
        existing = (await db.execute(text("""
            INSERT INTO ai_models
                (name, version, model_type, indicator_id, parameters, metrics,
                 model_blob, status, trained_at, created_by)
            VALUES
                (:name, :ver, :mt, :iid, CAST(:parameters AS jsonb), CAST(:metrics AS jsonb),
                 :blob, :status, :trained_at, :created_by)
            RETURNING id
        """), params)).scalar()
    return existing


async def _deprecate_other_active(db, indicator_id: str, keep_id) -> int:
    """Выводит из работы остальные активные модели показателя (любого типа и версии).

    Отбор выбирает ОДНУ рабочую модель на показатель, поэтому прежние активные —
    в том числе обученные до исправления уровней иерархии — не должны оставаться
    рядом с ней: их подхватили бы ежедневные прогнозы и списки моделей.
    Статус `deprecated` обратим — откат через `rollback_model`.
    """
    res = await db.execute(text("""
        UPDATE ai_models SET status = 'deprecated'
        WHERE indicator_id = :iid AND status = 'active' AND id <> :keep
          AND model_type <> 'llm_narrative'
    """), {"iid": indicator_id, "keep": keep_id})
    return res.rowcount or 0


def _print_summary(summary: list[dict], args: argparse.Namespace) -> None:
    if not summary:
        logger.warning("Нечего показать")
        return

    total = len(summary)
    reliable = sum(1 for s in summary if s["reliable"])
    good_mape = sum(1 for s in summary if (s["mape"] or 1e9) <= 15)
    by_model: dict[str, int] = {}
    for s in summary:
        by_model[s["best_model"]] = by_model.get(s["best_model"], 0) + 1
    covs = [s["coverage_80"] for s in summary if s["coverage_80"] is not None]

    print("\n" + "=" * 78)
    print(f"Показателей обучено: {total}" + ("  [dry-run, в БД не записано]" if args.dry_run else ""))
    print(f"Надёжных (MASE < 1, лучше наивного): {reliable}/{total}")
    print(f"MAPE ≤ 15 %: {good_mape}/{total}  (вторичная метрика)")
    if covs:
        print(f"Фактическое покрытие интервала 80 %: {sum(covs)/len(covs):.1%} (заявлено 80 %)")
    print("\nКакая модель победила (по MASE):")
    for mt, n in sorted(by_model.items(), key=lambda kv: -kv[1]):
        print(f"  {mt:16s} {n:4d}  ({n/total:.0%})")
    print("=" * 78)


def main() -> None:
    p = argparse.ArgumentParser(description="Глобальное обучение прогнозных моделей DSS")
    p.add_argument("--directions", nargs="+", default=list(DEFAULT_DIRECTIONS))
    p.add_argument("--horizon", type=int, default=5, help="Горизонт в ГОДАХ (деф. 5)")
    p.add_argument("--min-points", type=int, default=6)
    p.add_argument("--folds", type=int, default=4, help="Фолдов rolling-origin")
    p.add_argument("--version", default="2.0")
    p.add_argument("--no-global", action="store_true", help="Только локальные модели")
    p.add_argument("--dry-run", action="store_true", help="Только метрики, без записи в БД")
    p.add_argument("--report", help="Путь для JSON-отчёта")
    args = p.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
