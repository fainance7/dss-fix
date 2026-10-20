"""Тесты вывода ИИ по вкладке дашборда: факты текстом, отпечаток, числовой контроль."""
from unittest.mock import AsyncMock, patch

from app.services import dashboard_summary as ds
from app.services.llm_client import LLMUnavailableError


def _fact(**kw):
    base = dict(
        name="Объем инвестиций", unit="млрд тенге", basis="по области", year=2024,
        value=565.4, prev=443.7, base_year=2019, base_value=234.5,
        top=("г. Петропавловск", 266.4), bottom=("Тимирязевский район", 9.65),
    )
    base.update(kw)
    return ds.IndicatorFact(**base)


def test_cap_to_data_year_redates_later_value_only_without_own():
    assert ds._cap_to_data_year({2024: 1.0, 2026: 3.0}) == {2024: 1.0, 2025: 3.0}
    assert ds._cap_to_data_year({2025: 2.0, 2026: 3.0}) == {2025: 2.0}


def test_render_facts_has_values_changes_and_districts():
    text = ds.render_facts([_fact()])
    assert "2024 г. — 565,4" in text
    assert "изменение за год +27,4 %" in text
    assert "изменение с 2019 г. +141,1 %" in text
    assert "максимум — г. Петропавловск (266,4)" in text


def test_rate_change_is_difference_not_percent():
    """Доли и уровни (в %) сравниваются в процентных пунктах, индексы — разницей."""
    text = ds.render_facts([_fact(name="Уровень безработицы", unit="%", value=4.8, prev=4.4,
                                  base_year=None, base_value=None, top=None, bottom=None)])
    assert "изменение за год +0,4 п.п." in text
    assert ds.change_text("Индекс производительности труда", None, 98.0, 104.0) == "−6"


def test_change_text_skips_meaningless_percent():
    assert ds.change_text("Среднегодовая температура воздуха", "°C", 5.56, 3.75) == "+1,81"
    assert ds.change_text("Миграционный прирост", "чел.", -6946.0, -3656.0) == "−3 290"
    assert ds.change_text("Общий коэффициент рождаемости", "‰", 90.0, 37.71) == "+52,29"
    # Скачок в разы — разрыв ряда, а не динамика.
    assert ds.change_text("Сумма активных температур", "°C·сут", 2768.0, 475.0) is not None
    assert ds.change_text("Число врачей", "ед.", 333.2, 50.0) is None


def test_fingerprint_changes_with_data_only():
    a = ds.fingerprint("Экономика: Ресурсы", ds.render_facts([_fact()]))
    assert a == ds.fingerprint("Экономика: Ресурсы", ds.render_facts([_fact()]))
    assert a != ds.fingerprint("Экономика: Ресурсы", ds.render_facts([_fact(value=570.0)]))


def test_foreign_numbers_allows_rounding_and_flags_invented():
    facts = ds.render_facts([_fact()])
    assert ds.foreign_numbers("В 2024 году инвестиции — 565,4, рост на 27,4 %, у 3 районов.", facts) == []
    assert ds.foreign_numbers("Инвестиции выросли до 600 млрд тенге.", facts) == [600.0]


async def test_generate_falls_back_to_template_on_invented_numbers():
    with patch.object(ds, "chat_completion", AsyncMock(return_value="Рост до 999 млрд тенге.")) as llm:
        text, source = await ds.generate_summary("Экономика", "Ресурсы", [_fact()])
    assert llm.await_count == 2
    assert source == "template"
    assert "565,4" in text


async def test_generate_uses_llm_text_and_template_when_unavailable():
    ok = "<think>x</think>В 2024 году инвестиции составили **565,4** млрд тенге."
    with patch.object(ds, "chat_completion", AsyncMock(return_value=ok)):
        assert await ds.generate_summary("Экономика", "Ресурсы", [_fact()]) == (
            "В 2024 году инвестиции составили 565,4 млрд тенге.", "llm",
        )
    with patch.object(ds, "chat_completion", AsyncMock(side_effect=LLMUnavailableError("down"))):
        assert (await ds.generate_summary("Экономика", "Ресурсы", [_fact()]))[1] == "template"
