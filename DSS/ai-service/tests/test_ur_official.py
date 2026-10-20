"""Тесты воспроизведения официального индекса устойчивого развития.

Главный тест — побитовая сверка с опубликованными значениями расчётной книги
заказчика. Если он проходит, реализация методики верна; если нет, разбираться
нужно с реализацией, а не ослаблять пороги.
"""
import pytest

from app.services.landscape_impact import ur_official as uo

# Критерии приёмки из плана работ.
MAE_LIMIT = 0.001
MAX_ABS_ERROR_LIMIT = 0.005


@pytest.fixture(scope="module")
def model():
    return uo.load_model()


def test_model_window_and_territories(model):
    """Окно — опубликованные 2014–2024, территорий ровно 14."""
    assert model.years == list(range(2014, 2025))
    assert len(model.territories) == 14
    assert "г.Петропавловск" in model.territories


def test_weights_sum_to_one_in_every_block(model):
    """Условие нормировки весов соблюдено во всех трёх блоках.

    Ровно об этом было наше замечание 2: в отчёте сумма экономического блока
    равна 1,01 из-за веса 0,26 у промышленной продукции. Ответ подтвердил, что
    в расчётах применялось 0,25.
    """
    for block in uo.BLOCKS:
        total = sum(
            spec["weight"] for spec in model.blocks[block]["indicators"].values()
        )
        assert total == pytest.approx(1.0, abs=1e-9), f"блок {block}: сумма весов {total}"

    industry = model.blocks["economic"]["indicators"]["industry_pc"]["weight"]
    assert industry == 0.25


def test_bounds_are_fixed_not_sample_minmax(model):
    """Границы нормирования фиксированы и шире фактического диапазона.

    Проверка сути ответа на замечание 1: верхняя граница рождаемости 20 ‰ при
    фактическом максимуме 16,67 ‰. Если бы нормирование шло по выборке, граница
    совпала бы с максимумом.
    """
    bounds = model.blocks["social"]["indicators"]["birth_rate"]["bounds"]
    assert bounds["low"] == 0 and bounds["high"] == 20
    assert bounds["polarity"] == +1

    observed_max = max(
        v for series in model.blocks["social"]["indicators"]["birth_rate"]["raw"].values()
        for v in series if v is not None
    )
    assert observed_max < bounds["high"], "граница должна быть шире наблюдённого максимума"

    migration = model.blocks["social"]["indicators"]["migration_balance"]["bounds"]
    assert (migration["low"], migration["high"]) == (-70.0, 25.0)


def test_destimulators_have_negative_polarity(model):
    """Смертность, безработица, преступность и выбросы — дестимуляторы."""
    expected = {
        ("social", "death_rate"), ("social", "unemployment"),
        ("social", "ageing_index"), ("social", "crime_rate"),
        ("ecological", "emission_sources"), ("ecological", "emissions_pc"),
        ("ecological", "emissions_per_area"),
    }
    for block, indicator in expected:
        polarity = model.blocks[block]["indicators"][indicator]["bounds"]["polarity"]
        assert polarity == -1, f"{indicator}: ожидался дестимулятор"


def test_transforms_match_workbook(model):
    """Наши приведения к душе населения и площади совпадают с книгой.

    Без этой проверки спецификации приведений остались бы непроверенным
    допущением: ошибка в множителе не помешала бы индексу посчитаться.
    Именно так обнаружилось, что количество стационарных источников делится на
    площадь в ТЫСЯЧАХ км², а выбросы — на площадь в км².
    """
    report = uo.verify_transforms(model)
    failed = {
        name: rep for name, rep in report.items()
        if rep.get("mismatches")
    }
    assert not failed, f"расходятся: { {k: v['examples'][:1] for k, v in failed.items()} }"

    checked = sum(rep.get("checked", 0) for rep in report.values())
    assert checked >= 13 * 154 - 154, "проверено подозрительно мало значений"


def test_reproduces_published_indices(model):
    """Сверка с опубликованными значениями книги по всем 154 наблюдениям."""
    report = uo.verify_against_published(model)
    for block in (*uo.BLOCKS, "total"):
        rep = report[block]
        assert rep["n"] == 154, f"блок {block}: сверено {rep['n']} значений вместо 154"
        assert rep["mae"] <= MAE_LIMIT, f"блок {block}: MAE {rep['mae']}, худший {rep['worst']}"
        assert rep["max_abs_error"] <= MAX_ABS_ERROR_LIMIT, (
            f"блок {block}: максимальное отклонение {rep['max_abs_error']}, "
            f"худший случай {rep['worst']}"
        )


def test_total_is_arithmetic_mean_of_blocks(model):
    """Итоговый индекс — простое среднее трёх блоков (компенсаторная свёртка).

    Наше замечание 7 об этом и было: падение одного блока полностью
    возмещается ростом другого. Ответа на него нет, методика воспроизводится
    как есть.
    """
    blocks = uo.compute_blocks(model, "Кызылжарский")
    for i in range(len(model.years)):
        parts = [blocks[b][i] for b in uo.BLOCKS]
        assert blocks["total"][i] == pytest.approx(sum(parts) / 3, abs=1e-12)


def test_region_value_unweighted_by_default(model):
    """Областное значение — невзвешенное среднее; взвешенное отличается.

    Замечание 10: вклад Петропавловска равен вкладу сельского района, хотя в
    городе живёт свыше 40 % населения. Проверяем, что оба ряда считаются и
    различаются, то есть оговорка не бутафорская.
    """
    per_territory = uo.compute_all(model)
    plain = uo.region_value(model, per_territory, "social")
    weighted = uo.region_value(model, per_territory, "social", weighted_by_population=True)

    assert len(plain) == len(model.years)
    assert all(v is not None for v in plain)
    assert any(
        abs(p - w) > 0.005 for p, w in zip(plain, weighted)
    ), "взвешивание по населению обязано менять областное значение"


def test_normalization_clips_outside_bounds():
    """Значение за границами усекается, и факт усечения возвращается."""
    bounds = {"low": 0.0, "high": 20.0, "polarity": 1}
    value, clipped = uo.normalize(25.0, bounds)
    assert value == 1.0 and clipped is True

    value, clipped = uo.normalize(-5.0, bounds)
    assert value == 0.0 and clipped is True

    value, clipped = uo.normalize(10.0, bounds)
    assert value == pytest.approx(0.5) and clipped is False


def test_destimulator_normalization_inverts():
    bounds = {"low": 0.0, "high": 22.0, "polarity": -1}
    assert uo.normalize(0.0, bounds)[0] == pytest.approx(1.0)
    assert uo.normalize(22.0, bounds)[0] == pytest.approx(0.0)


def test_clipping_report_is_empty_on_published_window(model):
    """На опубликованных данных усечений быть не должно.

    Границы подбирались с запасом по фактическому диапазону, поэтому усечение
    здесь означало бы либо ошибку разбора границ, либо данные вне диапазона —
    и то и другое требует разбирательства.
    """
    report = uo.clipping_report(model)
    assert report == [], f"усечения на опубликованных данных: {report[:3]}"
