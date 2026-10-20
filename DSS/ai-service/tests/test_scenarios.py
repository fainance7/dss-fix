"""Тесты для сервиса сценариев ai-service.

KPI считаются от реальных данных `indicator_values` через два DB-хелпера
(`_resolve_indicator`, `_annual_oblast_series`) — в тестах они замоканы, так что
проверяется чистая логика расчёта (`_compute_kpis`) без обращения к БД.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.scenarios import (
    MAX_HORIZON_YEARS,
    REFERENCE_HORIZON_YEARS,
    SCENARIO_MULTIPLIERS,
)
from app.services.tasks import TASK_TEMPLATES

# Ключи KPI, которые сейчас считает сервис (АПК + демография СКО).
KPI_KEYS = ("agri_output", "natural_increase", "population", "agri_investment")


def make_scenario(scenario_type: str = "optimal", status: str = "approved", target_year: int = 2030):
    s = MagicMock()
    s.id = uuid4()
    s.scenario_type = scenario_type
    s.status = status
    s.target_year = target_year
    s.region_id = uuid4()
    s.parameters = {}
    s.created_by = uuid4()
    s.created_at = datetime.now(timezone.utc)
    return s


def _flat_series(baseline: float, base_year: int = 2023, points: int = 5):
    """Ровный годовой ряд, заканчивающийся (base_year, baseline)."""
    start = base_year - (points - 1)
    return [(yr, baseline) for yr in range(start, base_year + 1)]


def _patch_data(series):
    """Патчит оба DB-хелпера: любой показатель резолвится и отдаёт `series`."""
    return (
        patch("app.services.scenarios._resolve_indicator",
              new=AsyncMock(return_value=(uuid4(), "млрд ₸"))),
        patch("app.services.scenarios._annual_oblast_series",
              new=AsyncMock(return_value=series)),
    )


async def _compute(series, scenario_type, target_year, oblast_id=None):
    from app.services.scenarios import _compute_kpis
    p_res, p_ser = _patch_data(series)
    with p_res, p_ser:
        return await _compute_kpis(
            db=AsyncMock(), oblast_id=oblast_id or uuid4(),
            target_year=target_year, scenario_type=scenario_type,
        )


# --------------------------------------------------------------------------- #
# calculate_scenario / generate_all_scenarios — контракт возврата
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_calculate_scenario_returns_current_kpi_keys():
    """calculate_scenario возвращает scenario_id/scenario_type/kpis с актуальными ключами."""
    from app.services.scenarios import calculate_scenario

    scenario = make_scenario(scenario_type="optimal", status="draft")

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scenario
    mock_db.execute.return_value = mock_result
    mock_db.flush = AsyncMock()
    mock_db.commit = AsyncMock()

    p_res, p_ser = _patch_data(_flat_series(100.0))
    with p_res, p_ser:
        result = await calculate_scenario(db=mock_db, scenario_id=scenario.id)

    assert set(("scenario_id", "scenario_type", "kpis")).issubset(result)
    assert isinstance(result["kpis"], dict)
    for key in KPI_KEYS:
        assert key in result["kpis"], f"Ключ '{key}' отсутствует в kpis"


@pytest.mark.asyncio
async def test_generate_all_scenarios_creates_three():
    """generate_all_scenarios создаёт ровно 3 сценария (optimal/stable/risk)."""
    from app.services.scenarios import generate_all_scenarios

    mock_db = AsyncMock()
    mock_db.flush = AsyncMock()
    added = []
    mock_db.add = lambda obj: added.append(obj)

    p_res, p_ser = _patch_data(_flat_series(100.0))
    with p_res, p_ser:
        results = await generate_all_scenarios(
            db=mock_db, region_id=uuid4(), target_year=2030, created_by=uuid4(),
        )

    assert len(results) == 3
    assert {r["scenario_type"] for r in results} == {"optimal", "stable", "risk"}


# --------------------------------------------------------------------------- #
# Соотношения между типами сценариев
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_risk_lower_agri_output_than_stable():
    """Валовая продукция в 'risk' ниже, чем в 'stable' (при том же годе)."""
    series = _flat_series(200.0)
    risk_kpis, _ = await _compute(series, "risk", target_year=2030)
    stable_kpis, _ = await _compute(series, "stable", target_year=2030)
    assert risk_kpis["agri_output"] < stable_kpis["agri_output"]


@pytest.mark.asyncio
async def test_optimal_higher_investment_than_stable():
    """Инвестиции в 'optimal' выше, чем в 'stable' (при том же годе)."""
    series = _flat_series(150.0)
    optimal_kpis, _ = await _compute(series, "optimal", target_year=2030)
    stable_kpis, _ = await _compute(series, "stable", target_year=2030)
    assert optimal_kpis["agri_investment"] > stable_kpis["agri_investment"]


# --------------------------------------------------------------------------- #
# Влияние target_year на KPI (ядро фикса)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_optimal_kpi_grows_with_target_year():
    """Optimal: дальний год → выше KPI; ближний год → выше базы."""
    series = _flat_series(100.0, base_year=2023)
    near, _ = await _compute(series, "optimal", target_year=2026)   # H=3
    far, _ = await _compute(series, "optimal", target_year=2035)    # H=12
    baseline = 100.0
    assert far["agri_output"] > near["agri_output"] > baseline


@pytest.mark.asyncio
async def test_risk_kpi_shrinks_with_target_year_but_stays_positive():
    """Risk: дальний год → ниже KPI, но значение остаётся положительным."""
    series = _flat_series(100.0, base_year=2023)
    near, _ = await _compute(series, "risk", target_year=2026)
    far, _ = await _compute(series, "risk", target_year=2035)
    assert far["agri_output"] < near["agri_output"]
    assert far["agri_output"] > 0.0


@pytest.mark.asyncio
async def test_horizon_zero_yields_baseline():
    """target_year == base_year → множитель 1.0, KPI == база."""
    series = _flat_series(100.0, base_year=2023)
    kpis, meta = await _compute(series, "optimal", target_year=2023)
    assert kpis["agri_output"] == 100.0
    assert meta["agri_output"]["horizon"] == 0
    assert meta["agri_output"]["effective_multiplier"] == 1.0


@pytest.mark.asyncio
async def test_target_year_before_base_year_is_not_inverted():
    """target_year < base_year → горизонт клампится к 0, эффект не инвертируется."""
    series = _flat_series(100.0, base_year=2023)
    kpis, meta = await _compute(series, "risk", target_year=2020)
    assert kpis["agri_output"] == 100.0
    assert meta["agri_output"]["horizon"] == 0


@pytest.mark.asyncio
async def test_exact_formula():
    """value == round(baseline * base_mult ** (H / REFERENCE_HORIZON_YEARS), 2)."""
    baseline, base_year, target_year = 100.0, 2023, 2028   # H=5
    series = _flat_series(baseline, base_year=base_year)
    kpis, _ = await _compute(series, "optimal", target_year=target_year)

    base_mult = SCENARIO_MULTIPLIERS["optimal"]["agri_output"]
    horizon = target_year - base_year
    expected = round(baseline * base_mult ** (horizon / REFERENCE_HORIZON_YEARS), 2)
    assert kpis["agri_output"] == expected


@pytest.mark.asyncio
async def test_horizon_capped_at_max():
    """Очень дальний год → горизонт не превышает MAX_HORIZON_YEARS."""
    series = _flat_series(100.0, base_year=2023)
    # target_year 2023 + MAX + 10 → фактический горизонт должен упереться в MAX.
    _, meta = await _compute(series, "optimal", target_year=2023 + MAX_HORIZON_YEARS + 10)
    assert meta["agri_output"]["horizon"] == MAX_HORIZON_YEARS


@pytest.mark.asyncio
async def test_meta_carries_transparency_fields():
    """kpi_meta несёт horizon/effective_multiplier/target_year для прозрачности UI."""
    series = _flat_series(100.0, base_year=2023)
    _, meta = await _compute(series, "optimal", target_year=2030)
    m = meta["agri_output"]
    for field in ("horizon", "effective_multiplier", "target_year", "baseline", "base_year"):
        assert field in m
    assert m["target_year"] == 2030
    assert m["horizon"] == 7


# --------------------------------------------------------------------------- #
# Знаковый показатель (отрицательная база) — направление не инвертируется
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_negative_baseline_direction_not_inverted():
    """natural_increase (отриц. база): optimal улучшает (в плюс), risk ухудшает (в минус)."""
    baseline = -1000.0
    series = _flat_series(baseline, base_year=2023)   # non_negative=False у natural_increase
    optimal, _ = await _compute(series, "optimal", target_year=2030)
    stable, _ = await _compute(series, "stable", target_year=2030)
    risk, _ = await _compute(series, "risk", target_year=2030)

    # stable (множитель 1.0) не меняет знаковую базу
    assert stable["natural_increase"] == baseline
    # optimal сдвигает к плюсу (меньше по модулю убыль), risk — глубже в минус
    assert optimal["natural_increase"] > baseline
    assert risk["natural_increase"] < baseline
    # порядок сохранён: optimal > stable > risk
    assert optimal["natural_increase"] > stable["natural_increase"] > risk["natural_increase"]


@pytest.mark.asyncio
async def test_positive_baseline_unchanged_by_signed_fix():
    """Для положительной базы формула тождественна baseline * effective_mult."""
    baseline, base_year, target_year = 100.0, 2023, 2028   # H=5 → effective_mult == base_mult
    series = _flat_series(baseline, base_year=base_year)
    kpis, _ = await _compute(series, "optimal", target_year=target_year)
    base_mult = SCENARIO_MULTIPLIERS["optimal"]["agri_output"]
    assert kpis["agri_output"] == round(baseline * base_mult, 2)


# --------------------------------------------------------------------------- #
# Задачи из сценария (без изменений в этом фиксе — регресс)
# --------------------------------------------------------------------------- #
def test_task_templates_correct_counts():
    """Шаблоны задач содержат правильное количество для каждого типа."""
    assert len(TASK_TEMPLATES["optimal"]) == 5
    assert len(TASK_TEMPLATES["stable"]) == 3
    assert len(TASK_TEMPLATES["risk"]) == 4
    assert len(TASK_TEMPLATES["custom"]) == 0


@pytest.mark.asyncio
async def test_generate_tasks_from_approved_scenario():
    """generate_tasks_from_scenario создаёт задачи для одобренного сценария."""
    from app.services.tasks import generate_tasks_from_scenario

    scenario = make_scenario(scenario_type="optimal", status="approved")

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scenario
    mock_db.execute.return_value = mock_result
    mock_db.flush = AsyncMock()
    mock_db.refresh = AsyncMock()

    added_tasks = []
    def fake_add(obj):
        obj.id = uuid4()
        obj.created_at = datetime.now(timezone.utc)
        obj.updated_at = datetime.now(timezone.utc)
        added_tasks.append(obj)

    mock_db.add = fake_add

    tasks = await generate_tasks_from_scenario(mock_db, scenario.id)

    assert len(tasks) == len(TASK_TEMPLATES["optimal"])
    for task in tasks:
        assert task.scenario_id == scenario.id
        assert task.region_id == scenario.region_id
        assert task.status == "pending"


@pytest.mark.asyncio
async def test_generate_tasks_rejected_for_non_approved():
    """generate_tasks_from_scenario отказывает для неодобренного сценария."""
    from app.services.tasks import generate_tasks_from_scenario

    scenario = make_scenario(status="draft")

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scenario
    mock_db.execute.return_value = mock_result

    with pytest.raises(ValueError, match="одобренных"):
        await generate_tasks_from_scenario(mock_db, scenario.id)
