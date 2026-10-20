"""
Forecasting service: Holt-Winters, ARIMA (statsmodels) and linear regression.
Provides run_forecast() and get_historical_series() used by other services.
"""
import logging
import math
import pickle
from datetime import date
from uuid import UUID

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing

from app.config import settings
from app.models.ai_models import AIModel, Forecast
from app.services import series as series_loader
from app.services import xai

# Версии артефакта `ai_models.model_blob`, которые умеет читать инференс.
# v1 — конфигурация + снимок обучающего ряда;
# v2 — то же плюс конформные поправки интервалов по шагам горизонта.
SUPPORTED_ARTIFACT_VERSIONS = (1, 2)

logger = logging.getLogger(__name__)


def _fit_linear(values: list[float], horizon: int) -> list[float]:
    """
    Линейная экстраполяция тренда через numpy polyfit.
    Возвращает список из horizon спрогнозированных значений.
    """
    n = len(values)
    if n == 0:
        return [0.0] * horizon
    if n == 1:
        return [values[0]] * horizon

    x = np.arange(n, dtype=float)
    coeffs = np.polyfit(x, values, 1)
    return [float(np.polyval(coeffs, n + i)) for i in range(horizon)]


def _fit_holt_winters(values: list[float], horizon: int) -> list[float]:
    """
    Прогноз методом Хольта-Уинтерса (statsmodels ExponentialSmoothing).
    При нехватке данных откатывается на линейную регрессию.
    Возвращает список из horizon спрогнозированных значений.
    """
    n = len(values)
    arr = np.array(values, dtype=float)

    try:
        if n >= 24:
            model = ExponentialSmoothing(
                arr, trend="add", seasonal="add", seasonal_periods=12,
                initialization_method="estimated",
            )
            fitted = model.fit(optimized=True, use_brute=False)
        else:
            model = ExponentialSmoothing(
                arr, trend="add", seasonal=None,
                initialization_method="estimated",
            )
            fitted = model.fit(optimized=True)
        return [float(v) for v in fitted.forecast(horizon)]
    except Exception as exc:
        logger.warning("Хольт-Уинтерс не сошёлся (%s), откат на линейную регрессию", exc)
        return _fit_linear(values, horizon)


def _fit_arima(values: list[float], horizon: int) -> list[float]:
    """
    Прогноз моделью ARIMA (statsmodels). Перебирает несколько типовых порядков
    (p,d,q) и выбирает лучший по AIC. При нехватке данных или несходимости всех
    кандидатов откатывается на линейную регрессию.
    Возвращает список из horizon спрогнозированных значений.
    """
    n = len(values)
    if n < 4:
        return _fit_linear(values, horizon)

    arr = np.asarray(values, dtype=float)
    # Типовые порядки для коротких годовых рядов (без сезонности).
    candidate_orders = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (2, 1, 1), (1, 0, 0)]
    best_aic = math.inf
    best_forecast: list[float] | None = None

    for order in candidate_orders:
        try:
            fitted = ARIMA(arr, order=order).fit()
            aic = float(fitted.aic)
            if not math.isfinite(aic) or aic >= best_aic:
                continue
            fc = np.asarray(fitted.forecast(horizon), dtype=float)
            if fc.shape[0] == horizon and np.all(np.isfinite(fc)):
                best_aic = aic
                best_forecast = [float(v) for v in fc]
        except Exception:
            continue

    if best_forecast is None:
        logger.warning("ARIMA не сошлась ни на одном порядке, откат на линейную регрессию")
        return _fit_linear(values, horizon)
    return best_forecast


# ── Базовые модели ────────────────────────────────────────────────────
# На коротких годовых рядах DSS (≈11 точек) простые методы нередко точнее
# статистических: отбор по MASE (`scripts/train_global.py`) выбирает их для
# заметной доли показателей, поэтому инференс обязан уметь их исполнять —
# иначе в реестре лежат метрики одной модели, а прогноз строит другая.


def _fit_naive(values: list[float], horizon: int) -> list[float]:
    """Последнее наблюдение переносится на весь горизонт."""
    return [float(values[-1])] * horizon


def _fit_drift(values: list[float], horizon: int) -> list[float]:
    """Наивный прогноз со сносом: продолжение средней годовой динамики."""
    if len(values) < 2:
        return _fit_naive(values, horizon)
    slope = (values[-1] - values[0]) / (len(values) - 1)
    return [float(values[-1] + slope * (i + 1)) for i in range(horizon)]


def _fit_theta(values: list[float], horizon: int) -> list[float]:
    """Упрощённый метод Theta: среднее наивного со сносом и линейного тренда.

    Полноценная реализация требует statsforecast; для коротких годовых рядов
    эта аппроксимация ведёт себя близко и не тянет зависимость.
    """
    drift = _fit_drift(values, horizon)
    linear = _fit_linear(values, horizon)
    return [(d + l) / 2.0 for d, l in zip(drift, linear)]


# Тип модели в реестре → функция прогноза. Неизвестный тип — линейная регрессия.
FORECASTERS = {
    "naive": _fit_naive,
    "drift": _fit_drift,
    "theta": _fit_theta,
    "linear": _fit_linear,
    "holt_winters": _fit_holt_winters,
    "arima": _fit_arima,
}


async def get_annual_series(
    db: AsyncSession,
    region_id: UUID | None = None,
    periods: int = 36,
    indicator_id: UUID | None = None,
) -> list[tuple[int, float]]:
    """
    Годовой ряд показателя как пары `(год, значение)`.

    Уровни территориальной иерархии не смешиваются: областной срез
    (`region_id=None`) берётся из прямых областных значений, а при их нехватке —
    агрегацией районов (см. `app.services.series.annual_series`). Прежняя
    реализация усредняла по ВСЕМ регионам сразу, из-за чего в одни годы в
    среднее попадала и область, и районы, а в другие — только районы; на
    абсолютных счётных показателях это давало разрыв масштаба и вырожденный MAPE.
    """
    if indicator_id is None:
        raise ValueError("Годовой ряд требует indicator_id")
    points = await series_loader.annual_series(db, indicator_id, region_id)
    if len(points) < 3:
        raise ValueError(
            "Недостаточно одобренных значений в indicator_values для прогноза "
            f"(показатель {indicator_id}, регион {region_id}): "
            f"найдено {len(points)} точек, требуется ≥ 3"
        )
    return points[-periods:]


async def get_historical_series(
    db: AsyncSession,
    region_id: UUID | None = None,
    model_type: str = "linear",
    periods: int = 36,
    indicator_id: UUID | None = None,
) -> list[float]:
    """
    Загружает исторический ряд для прогноза (только значения, без годов).

    Приоритетный источник — `indicator_values` (реальные данные показателей,
    канал intake-portal). Если задан `indicator_id`, ряд берётся строго по этому
    показателю с разделением уровней иерархии (`get_annual_series`). При
    нехватке одобренных значений (< 3 точек) поднимается ValueError.

    Если `indicator_id` не задан, остаётся устаревший путь по `spatial_data`
    (обратная совместимость); синтетический ряд выдаётся только при явно
    включённом `ALLOW_SYNTHETIC_SERIES`.
    """
    # 1) Основной путь: реальные данные показателя из indicator_values.
    if indicator_id is not None:
        return [v for _, v in await get_annual_series(
            db, region_id=region_id, periods=periods, indicator_id=indicator_id,
        )]

    # 2) Устаревший путь: spatial_data без привязки к показателю.
    try:
        if region_id is not None:
            q = text("""
                SELECT properties->>'value' AS val
                FROM spatial_data
                WHERE region_id = :region_id
                  AND validation_status = 'approved'
                  AND properties->>'value' IS NOT NULL
                ORDER BY data_date ASC
                LIMIT :periods
            """)
            result = await db.execute(q, {"region_id": str(region_id), "periods": periods})
        else:
            q = text("""
                SELECT properties->>'value' AS val
                FROM spatial_data
                WHERE validation_status = 'approved'
                  AND properties->>'value' IS NOT NULL
                ORDER BY data_date ASC
                LIMIT :periods
            """)
            result = await db.execute(q, {"periods": periods})

        values = []
        for row in result.fetchall():
            try:
                values.append(float(row[0]))
            except (TypeError, ValueError):
                pass

        if len(values) >= 3:
            return values
    except Exception as exc:
        logger.warning("Ошибка загрузки данных: %s. Синтетический ряд.", exc)

    # 3) Синтетический ряд — только при явно включённом флаге (локальные тесты).
    # По умолчанию выключен: «успешный» прогноз по выдуманному ряду со
    # status="completed" неотличим от настоящего и подрывает доверие к системе.
    if not settings.ALLOW_SYNTHETIC_SERIES:
        raise ValueError(
            "Нет одобренных данных для прогноза, а синтетический ряд запрещён "
            "(ALLOW_SYNTHETIC_SERIES=false). Укажите indicator_id с заполненным рядом."
        )
    logger.warning("Используется СИНТЕТИЧЕСКИЙ ряд — результат не является прогнозом по данным")
    import random
    random.seed(42)
    base = 100.0
    synthetic = []
    for i in range(periods):
        trend = i * 0.8
        seasonal = 5.0 * math.sin(2 * math.pi * i / 12)
        noise = random.gauss(0, 1.5)
        synthetic.append(round(base + trend + seasonal + noise, 4))
    return synthetic


def _load_artifact(model: AIModel) -> dict | None:
    """Читает артефакт обученной модели из `ai_models.model_blob`.

    Возвращает None, если артефакта нет или его версия неизвестна — вызывающий
    код в этом случае переобучает модель на актуальном ряду и помечает это в
    метаданных прогноза.
    """
    blob = getattr(model, "model_blob", None)
    if not blob:
        return None
    try:
        artifact = pickle.loads(blob)
    except Exception as exc:  # повреждённый/несовместимый pickle
        logger.warning("model_blob модели %s не читается: %s", model.id, exc)
        return None
    if not isinstance(artifact, dict):
        return None
    version = artifact.get("artifact_version")
    if version not in SUPPORTED_ARTIFACT_VERSIONS:
        logger.warning(
            "model_blob модели %s имеет версию %s (поддерживаются %s) — переобучение на лету",
            model.id, version, SUPPORTED_ARTIFACT_VERSIONS,
        )
        return None
    return artifact


async def run_forecast(
    db: AsyncSession,
    model_id: UUID,
    region_id: UUID | None,
    horizon_years: int | None = None,
    triggered_by: UUID | None = None,
    indicator_id: UUID | None = None,
    horizon_months: int | None = None,
) -> Forecast:
    """
    Запускает прогноз для указанной модели, показателя и региона.
    Алгоритм выбирается по типу модели (см. `FORECASTERS`).
    Сохраняет Forecast в БД и возвращает его.

    Горизонт задаётся в ГОДАХ: ряды DSS годовые (одно значение на год), поэтому
    прежний `horizon_months` фактически означал годы и растягивал прогноз на
    десятилетия, раскладывая его по месяцам. Параметр `horizon_months` оставлен
    как устаревший псевдоним и трактуется как число годовых шагов.

    Ряд берётся из артефакта обученной модели (`model_blob`), если он есть:
    так прогноз соответствует именно той версии модели, которая активна, и
    откат версии реально меняет результат. При отсутствии/несовместимости
    артефакта модель переобучается на актуальном ряду, и это фиксируется в
    `xai_explanation.meta.refit_on_the_fly`.
    """
    result = await db.execute(select(AIModel).where(AIModel.id == model_id))
    model = result.scalar_one_or_none()
    if model is None:
        raise ValueError(f"Модель {model_id} не найдена")

    if horizon_years is None:
        horizon_years = horizon_months if horizon_months is not None else settings.FORECAST_HORIZON_YEARS
        if horizon_months is not None:
            logger.warning(
                "run_forecast: horizon_months устарел, значение %s трактуется как годы",
                horizon_months,
            )
    horizon = int(horizon_years)

    # Показатель прогноза: явный из запроса, иначе — привязанный к модели.
    effective_indicator_id = indicator_id or getattr(model, "indicator_id", None)

    artifact = _load_artifact(model)
    refit_on_the_fly = True
    series_points = await get_annual_series(
        db, region_id=region_id, periods=36, indicator_id=effective_indicator_id,
    ) if effective_indicator_id is not None else None

    if artifact is not None and artifact.get("series_tail"):
        tail = [float(v) for v in artifact["series_tail"]]
        # Артефакт хранит снимок обучающего ряда без годов; годы для разметки
        # прогноза берём из актуального ряда (последний фактический год).
        historical = tail
        refit_on_the_fly = False
    elif series_points is not None:
        historical = [v for _, v in series_points]
    else:
        historical = await get_historical_series(
            db, region_id=region_id, model_type=model.model_type, periods=36,
            indicator_id=effective_indicator_id,
        )

    forecast_values = FORECASTERS.get(model.model_type, _fit_linear)(historical, horizon=horizon)

    today = date.today()
    # Базовый год = последний фактический год ряда; при его отсутствии — текущий.
    base_year = series_points[-1][0] if series_points else today.year
    target_from = date(base_year + 1, 1, 1)
    target_to = date(base_year + horizon, 12, 31)

    # Годовые ключи: одна точка на год, дата — 1 января.
    result_values: dict[str, float] = {}
    confidence_scores: dict[str, dict[str, float]] = {}

    conformal = (artifact or {}).get("conformal") or {}
    deltas = conformal.get("deltas") or []
    for i, v in enumerate(forecast_values):
        dt_str = date(base_year + 1 + i, 1, 1).isoformat()
        val = round(v, 4)
        result_values[dt_str] = val
        if deltas:
            # Split-conformal: поправка — квантиль абсолютных остатков бэктеста
            # для этого шага горизонта. Интервал шире у дальних лет не по
            # выдуманной формуле, а по фактическому разбросу ошибок.
            delta = float(deltas[min(i, len(deltas) - 1)])
            confidence_scores[dt_str] = {
                "low": round(val - delta, 4),
                "high": round(val + delta, 4),
                "level": conformal.get("level", 0.8),
                "method": "split-conformal",
                "n_calib": conformal.get("n_calib"),
            }
        else:
            margin = abs(val) * (0.15 + 0.005 * i)
            confidence_scores[dt_str] = {
                "low": round(val - margin, 4),
                "high": round(val + margin, 4),
                # Эвристика (фиксированный процент), а не интервал, выведенный из
                # распределения ошибок модели. Метод помечен явно, чтобы фронт и
                # отчёты не выдавали его за статистический доверительный интервал.
                "method": "fixed-margin",
            }

    # Реальный XAI: важность признаков считается по фактическому историческому
    # ряду (RandomForest на лаг/тренд/скользящих признаках), а не задаётся
    # константами. Объяснимость не должна ронять прогноз — при сбое откатываемся
    # на равномерные веса.
    try:
        feature_importance = xai.compute_feature_importance_from_series(historical)
    except Exception as exc:
        logger.warning("XAI: не удалось вычислить важность признаков (%s)", exc)
        feature_importance = xai._uniform_importance()

    forecast = Forecast(
        model_id=model_id,
        indicator_id=effective_indicator_id,
        region_id=region_id,
        forecast_date=today,
        target_date_from=target_from,
        target_date_to=target_to,
        result_values=result_values,
        confidence_scores=confidence_scores,
        xai_explanation=None,
        status="completed",
        triggered_by=triggered_by,
    )
    db.add(forecast)
    await db.flush()
    await db.refresh(forecast)

    # Полный XAI-отчёт с текстовой интерпретацией (нужен forecast.id).
    report = await xai.generate_xai_report(
        forecast.id, feature_importance, metrics=getattr(model, "metrics", None),
        # Уровень интервала — фактический: конформный из артефакта или «оценочный».
        confidence_interval=float(conformal.get("level", 0.8)) if deltas else None,
    )
    report["meta"] = {
        "horizon_years": horizon,
        "base_year": base_year,
        "refit_on_the_fly": refit_on_the_fly,
        "model_version": getattr(model, "version", None),
        "n_obs": len(historical),
    }
    forecast.xai_explanation = report
    await db.flush()

    logger.info(
        "Прогноз создан: id=%s model=%s indicator=%s region=%s горизонт=%d лет refit=%s",
        forecast.id, model_id, effective_indicator_id, region_id, horizon, refit_on_the_fly,
    )
    return forecast
