"""Контекст заключений ИИ по климату (замечания эксперта 06.10.2026): нормы и
отклонения последнего года, справочные сценарные прогнозы, указания модели."""
import fnmatch

from app.services.geo_context import EXCLUDED_INDICATORS, render_climate_norms, render_context_as_text


def _norm():
    return {
        "label": "ГТК(5-8), май–август", "unit": "безразмерная величина", "digits": 2, "year": 2025,
        "oblast_norm": 0.9046, "oblast_last": 1.19,
        "norm_min": {"name": "Акжарский район", "norm": 0.76, "last": 1.21},
        "norm_max": {"name": "Айыртауский район", "norm": 1.07, "last": 1.11},
        "last_min": {"name": "Уалихановский район", "norm": 0.76, "last": 0.91},
        "last_max": {"name": "Жамбылский район", "norm": 1.0, "last": 1.35},
        "n_last": 14,
    }


def _ctx(code: str, norms=None):
    return {
        "direction": {"code": code, "name": "Климат"},
        "region": {"name": "СКО", "level": 1},
        "meta": {"child_count": 14},
        "latest": [],
        "norms": norms or [],
        "child_regions_geojson": {"features": []},
    }


def test_norms_line_compares_last_year_with_norm():
    text = render_climate_norms([_norm()])
    assert "норма по области 0,90" in text
    assert "2025 г. — 1,19 (+0,29 к норме)" in text
    assert "от 0,76 (Акжарский район) до 1,07 (Айыртауский район)" in text
    assert "безразмерная величина" not in text


def test_climate_context_has_projections_and_rules_only_for_climate():
    climate = render_context_as_text(_ctx("climate", [_norm()]))
    assert "Климатические нормы" in climate
    assert "климатические сценарные прогнозы до 2030" in climate.lower()
    assert "0,7 °C" in climate
    assert "не выводите из трендов" in climate
    other = render_context_as_text(_ctx("economy"))
    assert "сценарные прогнозы" not in other


def test_defective_climate_indicators_excluded():
    pats = [p.replace("%", "*") for p in EXCLUDED_INDICATORS["climate"]]
    hit = lambda n: any(fnmatch.fnmatchcase(n, p) for p in pats)  # noqa: E731
    assert hit("Доля метеостанций района с ГТК<0,80")
    assert hit("Сумма активных температур >10°C (Σt)")
    assert hit("Дата перехода температуры через 10°C весной")
    assert not hit("Сумма активных температур воздуха выше 10 °C за май–август, ΣT(5-8)")
    assert not hit("Гидротермический коэффициент (ГТК)")


def test_district_table_has_position_and_gtk_class():
    from app.services.climate_reference import gtk_class

    n = {**_norm(), "indicator": "Гидротермический коэффициент (ГТК)",
         "districts": [{"name": "Уалихановский район", "norm": 0.76, "last": 0.91},
                       {"name": "Айыртауский район", "norm": 1.07, "last": 1.11}]}
    text = render_climate_norms([n])
    assert "Уалихановский район (юго-восток, крайний восток): норма ГТК 0,76 — умеренно засушливо" in text
    assert "2025 г. — 0,91 — слабо засушливо" in text
    assert "Айыртауский район (юг, юго-запад): норма ГТК 1,07 — достаточное увлажнение" in text
    assert gtk_class(0.805) == "слабо засушливо" and gtk_class(0.5) == "сильно засушливо"
