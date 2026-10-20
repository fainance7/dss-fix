"""Тесты алгоритма ЗБ-УР (земельный баланс → устойчивое развитие).

Проверяется расчётное ядро без обращения к БД: витрина читается из эталонного
файла, который поставляется вместе с модулем.
"""
import numpy as np
import pytest

from app.services.landscape_impact import agro, economy, sd_index, transitions, uncertainty, verdict
from app.services.landscape_impact.assumptions import get_assumption, list_assumptions, unverified_keys
from app.services.landscape_impact.calibration import benjamini_hochberg, calibrate, estimate_link
from app.services.landscape_impact.datamart import LAND_CATEGORIES, load_reference_datamart
from app.services.landscape_impact.pipeline import ScenarioInput, estimate_express, run_full_assessment


@pytest.fixture(scope="module")
def mart():
    return load_reference_datamart()


@pytest.fixture(scope="module")
def calib(mart):
    return calibrate(mart)


# ─────────────────────────────── витрина ───────────────────────────────

def test_datamart_window_is_2014_2024(mart):
    assert mart.years == list(range(2014, 2025))
    assert mart.n_years == 11


def test_land_balance_is_exactly_constant(mart):
    """Ключевое ограничение алгоритма: сумма семи категорий постоянна."""
    assert mart.check_balance() == []
    for i, _year in enumerate(mart.years):
        total = sum(mart.land_balance[c][i] for c in LAND_CATEGORIES)
        assert total == pytest.approx(9804.3, abs=0.05)


def test_2015_correction_is_recorded(mart):
    """Опечатка 2015 года восстановлена балансом, и это зафиксировано."""
    assert mart.land_balance["agricultural"][1] == pytest.approx(7228.5)
    corrections = [i for i in mart.issues if i["kind"] == "correction"]
    assert any(c["year"] == 2015 for c in corrections)


def test_arbitration_flags_are_surfaced(mart):
    """Ряды с расхождением источников помечены, а не подменены молча."""
    kinds = {i["kind"] for i in mart.issues}
    assert "arbitration_required" in kinds


# ─────────────────────────────── калибровка ───────────────────────────────

def test_sd_aggregation_rule_recovered(calib):
    """Итоговый индекс УР = среднее арифметическое трёх блоков."""
    agg = calib.sd_aggregation
    assert agg["rule"] == "arithmetic_mean"
    assert agg["max_abs_deviation_arithmetic"] <= 0.001
    assert agg["compensatory"] is True


def test_direct_land_links_are_not_significant(calib):
    """Прямая связь «площади категорий → показатель» на 11 точках не существует.

    Это отрицательный контроль: если бы такая связь нашлась, структурная цепь
    была бы не нужна. Тест закрепляет установленный факт — при изменении данных
    он должен упасть и заставить пересмотреть конструкцию алгоритма.
    """
    links = calib.direct_land_links
    assert links["n_tests"] >= 30
    assert links["survived_fdr"] == 0


def test_oblast_yield_link_is_downgraded_to_expert(mart):
    """На областном ряду из 10 разностей связь урожайности с климатом гейты не проходит."""
    link = calibrate(mart, use_district_panel=False).yield_link
    assert link is not None
    assert link.link_type == "Э"
    assert not link.gates["G6_significance"]
    lo, base, hi = link.expert_range
    assert lo <= base <= hi


def test_district_panel_yield_link_is_statistical(calib):
    """На районной панели (11.10.2026) связь с ГТК проходит G4-G7 и получает метку [С].

    Тест закрепляет установленный факт: эластичность урожайности по ГТК около
    0,24, устойчива к выбросу района и года. При пересборке панели он должен
    упасть, если связь перестанет проходить проверку.
    """
    link = calib.yield_link
    assert link.link_type == "С"
    assert link.source["kind"] == "district_panel"
    assert link.regressor == "gtk_5_8"
    assert 0.15 <= link.slope <= 0.35
    assert link.jackknife_max_deviation <= 0.5
    assert link.p_adjusted < 0.05
    assert link.mase < 1.0
    assert calib.yield_candidates_oblast, "областной контроль должен оставаться в отчёте"


def test_panel_yield_reconciled_with_harvest_and_sown():
    """Урожайность с убранной площади (засуха 2010) заменяется урожайностью с посевной."""
    from app.services.landscape_impact.district_panel import build_panel_json
    rows = [
        ("Урожайность зерновых культур", "agriculture", "Р", 2010, 14.7),
        ("Валовой сбор зерновых культур", "agriculture", "Р", 2010, 162800.0),
        ("Посевная площадь зерновых культур", "agriculture", "Р", 2010, 221.2),
        ("Урожайность зерновых культур", "agriculture", "Р", 2011, 20.0),
        ("Валовой сбор зерновых культур", "agriculture", "Р", 2011, 440000.0),
        ("Посевная площадь зерновых культур", "agriculture", "Р", 2011, 220.0),
    ]
    ref = build_panel_json(rows, version="t", source="t")
    assert ref["series"]["yield_grain"]["Р"] == {"2010": 7.36, "2011": 20.0}
    assert ref["meta"]["yield_replaced"] == [
        {"region": "Р", "year": 2010, "reported": 14.7, "per_sown": 7.36}]


def test_panel_link_detects_pure_noise():
    """Случайный «климат» на той же панели гейт значимости не проходит."""
    from app.services.landscape_impact.district_panel import (
        DistrictPanel, estimate_panel_link, load_district_panel,
    )
    real = load_district_panel()
    rng = np.random.default_rng(1)
    noise = np.exp(rng.normal(size=real.series["gtk_5_8"].shape))
    fake = DistrictPanel(regions=real.regions, years=real.years,
                         series={**real.series, "noise": noise}, meta=real.meta)
    est = estimate_panel_link(fake, "noise", prior_sign=+1, permutations=500)
    assert not est.gates["G6_significance"]


def test_mde_matches_historical_noise(calib):
    """MDE = половина σ годовых приращений индекса за окно.

    Сравнение с точностью до округления: оба значения публикуются с пятью знаками.
    """
    assert calib.mde["total"] == pytest.approx(0.5 * calib.mde["total_sigma"], abs=1e-5)
    assert 0.005 <= calib.mde["total"] <= 0.008


def test_structural_shares_are_plausible(calib):
    shares = calib.structural_shares
    assert 0.60 <= shares["arable_share_of_agricultural"]["mean"] <= 0.75
    assert 0.80 <= shares["sown_share_of_arable"]["mean"] <= 0.95
    total = sum(shares[k]["mean"] for k in
                ("grain_share_of_sown", "oil_share_of_sown", "fodder_share_of_sown"))
    assert total == pytest.approx(1.0, abs=0.05)


def test_benjamini_hochberg_monotone():
    adjusted = benjamini_hochberg([0.001, 0.02, 0.04, 0.5])
    assert adjusted == sorted(adjusted)
    assert all(a >= p for a, p in zip(adjusted, [0.001, 0.02, 0.04, 0.5]))


def test_estimate_link_detects_pure_noise():
    """На случайных рядах связь не должна проходить гейт значимости."""
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=40), rng.normal(size=40)
    est = estimate_link("шум", "x", x, y)
    assert est.p_permutation > 0.05
    assert not est.gates["G6_significance"]


# ─────────────────────────── матрица переходов ───────────────────────────

def test_transition_is_zero_sum(mart):
    base = mart.land_vector(2024)
    tm = transitions.from_target_areas(base, {"agricultural": 7363.1, "reserve": 485.6})
    assert tm.zero_sum_error < 1e-9
    assert tm.net.sum() == pytest.approx(0.0, abs=1e-9)
    assert tm.sources_of("agricultural") == {"reserve": 200.0}


def test_forbidden_transitions_are_blocked(mart):
    """Перевод земель ООПТ и лесного фонда в с/х назначение блокируется."""
    base = mart.land_vector(2024)
    tm = transitions.from_target_areas(
        base, {"protected": 34.9, "forest": 445.1, "agricultural": 7363.1})
    i_pro = LAND_CATEGORIES.index("protected")
    i_agr = LAND_CATEGORIES.index("agricultural")
    i_for = LAND_CATEGORIES.index("forest")
    assert tm.matrix[i_pro, i_agr] == pytest.approx(0.0, abs=1e-9)
    assert tm.matrix[i_for, i_agr] == pytest.approx(0.0, abs=1e-9)
    assert not [v for v in tm.violations if v.get("severity") == "blocking"]


def test_unbalanced_input_is_flagged(mart):
    base = mart.land_vector(2024)
    tm = transitions.from_target_areas(base, {"forest": 645.1})
    assert any(v["rule"] == "zero_sum_rebalance" for v in tm.violations)


def test_realized_fraction_schedule():
    assert transitions.realized_fraction(0, 10) == 0.0
    assert transitions.realized_fraction(5, 10) == pytest.approx(0.5)
    assert transitions.realized_fraction(20, 10) == 1.0
    assert 0.0 <= transitions.realized_fraction(5, 10, "s_curve") <= 1.0


# ─────────────────────────── агро и экономика ───────────────────────────

def _params(calib):
    from app.services.landscape_impact.pipeline import _assumption_ranges
    return {k: v[1] for k, v in _assumption_ranges(calib).items()}


def test_harvest_is_area_times_yield(mart, calib):
    params = _params(calib)
    state = agro.compute_agro(mart.land_vector(2024),
                              agro.calibrate_base_yields(mart, 2024), params)
    assert state.harvest["grain"] == pytest.approx(
        state.land_use.sown["grain"] * state.yields["grain"] / 10.0)


def test_agri_output_relative_change_matches_hand_calculation(mart, calib):
    """+2,79 % земель с/х назначения даёт ожидаемый прирост продукции.

    Ручной счёт: растениеводство растёт пропорционально площади (+2,79 %),
    животноводство — с эластичностью по пастбищам, и веса применяются к
    относительным изменениям.
    """
    params = _params(calib)
    base_v = mart.land_vector(2024)
    scen_v = base_v.copy()
    delta = 200.0
    scen_v[LAND_CATEGORIES.index("agricultural")] += delta
    scen_v[LAND_CATEGORIES.index("reserve")] -= delta

    ys = agro.calibrate_base_yields(mart, 2024)
    base_s = agro.compute_agro(base_v, ys, params)
    scen_s = agro.compute_agro(scen_v, ys, params)
    eco = economy.compute_economy(scen_s, base_s, params, 939.1626, 2184.489)

    area_growth = delta / base_v[LAND_CATEGORIES.index("agricultural")]
    expected = (params["crop_share_of_agri_output"] * area_growth
                + (1 - params["crop_share_of_agri_output"])
                * ((1 + area_growth) ** params["livestock_pasture_elasticity"] - 1))
    assert eco.agri_output_rel == pytest.approx(expected, rel=1e-6)
    assert eco.grp_abs == pytest.approx(
        eco.agri_output_abs * params["vds_share_agri"] * (1 + params["multiplier_m"]))


def test_climate_cancels_when_equal(mart, calib):
    """При равном климате климатический член не влияет на результат."""
    params = _params(calib)
    ys = agro.calibrate_base_yields(mart, 2024)
    a = agro.compute_agro(mart.land_vector(2024), ys, params, climate_ratio=1.0)
    assert a.yields["grain"] == pytest.approx(ys["grain"])


# ─────────────────────────── индекс УР ───────────────────────────

def test_dss_index_uses_normative_bounds(mart):
    """Границы нормировки не зависят от фактического диапазона выборки."""
    model = sd_index.build_model(mart, 2024)
    bounds = model.reference_bounds
    assert bounds["arable"]["low"] == 0.0
    assert bounds["arable"]["high"] == pytest.approx(0.70 * 9804.3)
    assert "reserve" not in bounds
    assert bounds["yield_grain"]["high"] == 30.0


def test_dss_index_is_non_compensatory(mart):
    """Провал одного блока не компенсируется ростом другого."""
    model = sd_index.build_model(mart, 2024)
    bounds = model.reference_bounds
    good = {"agri_output": 939.0, "grp": 2184.0, "yield_grain": 20.0,
            "arable": 5000.0, "pasture": 2000.0, "protected": 135.0,
            "forest": 545.0, "reserve": 686.0}
    collapsed = dict(good, protected=0.0, forest=0.0)
    assert sd_index.dss_index(collapsed, bounds)["total"] < \
           sd_index.dss_index(good, bounds)["total"]
    # Геометрическая свёртка строго ниже арифметической при неравных блоках.
    blocks = sd_index.dss_index(good, bounds)
    arithmetic = np.mean([blocks[b] for b in sd_index.BLOCKS])
    assert blocks["total"] <= arithmetic + 1e-12


def test_mode_a_when_official_methodology_available(mart):
    """Методика получена — режим A включается по справочнику, а не подбором.

    Тест заменил `test_mode_b_when_methodology_missing`, который закреплял
    состояние «методики нет». До 22.09.2026 единственным способом получить
    режим A была реконструкция — подбор гипотезы под опубликованные значения;
    ни одна не подходила, и режим оставался B. Теперь расчётная книга получена,
    индекс воспроизводится точно.
    """
    model = sd_index.build_model(mart, 2024)
    assert model.mode == "A"
    assert "методике заказчика" in model.note


def test_reconstruction_still_fails_by_guessing(mart):
    """Подбором конструкцию было не угадать — диагностика это показывает.

    Проверка сохраняет смысл прежнего теста: гипотезы реконструкции по-прежнему
    не воспроизводят официальные значения. Это и есть довод в пользу того, что
    методику нужно было получать, а не восстанавливать.
    """
    results = sd_index.try_reconstruct(mart)
    block_level = [r for r in results if "блок =" in r.hypothesis and r.passed]
    assert not block_level, "подбор не должен воспроизводить конструкцию блоков"


def test_verdict_uses_dss_index_even_in_mode_a(mart):
    """Вердикт строится на индексе DSS независимо от режима.

    Официальный индекс к землепользованию почти нечувствителен: эффект варианта
    в семнадцать раз ниже его порога различимости. Если бы вердикт переключался
    на него в режиме A, ответом всегда было бы «различие не подтверждено» —
    ровно то, о чём мы писали в замечании 5.
    """
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=500, seed=1).as_dict()
    assert out["verdict"]["details"]["index_source"] == "индекс DSS"
    # Порог различимости тоже должен быть от индекса DSS, а не от официального:
    # у официального он 0,0061, и эффект варианта под него не проходит никогда.
    # У индекса DSS 1.1.0 (без земель запаса) — 0,0096.
    assert out["verdict"]["details"]["mde"] > 0.008


def test_official_delta_uses_customer_methodology(mart):
    """Официальный индекс считается точно, независимо от режима реконструкции.

    Тест заменил прежний `test_official_delta_not_applicable_in_mode_b`, который
    закреплял ограничение «в режиме B официальный индекс посчитать нельзя». Оно
    было верно, пока методики не было: конструкция блоков неизвестна — считать
    нечего. С 22.09.2026 расчётная книга заказчика получена, индекс
    воспроизводится точно (см. test_ur_official.py), и ограничение снято.
    """
    out = sd_index.official_index_delta(mart, 2024, 0.05, 0.05, "B")
    assert out["applicable"] == 1.0
    assert out["total"] > 0, "рост продукции обязан повышать индекс"
    assert out["bounds_edition"] == "2014-2024"
    # Эффект приходит только через экономический блок: землепользование
    # затрагивает ровно один показатель из четырнадцати.
    assert out["social"] == 0.0 and out["ecological"] == 0.0
    assert out["economic"] == pytest.approx(out["total"] * 3, rel=1e-9)


def test_official_delta_rejects_year_outside_window(mart):
    """Год вне окна 2014–2024 не считается: границы закреплены за редакцией."""
    out = sd_index.official_index_delta(mart, 2030, 0.05, 0.05, "B")
    assert out["applicable"] == 0.0
    assert "вне окна" in out.get("reason", "")


def test_land_use_effect_stays_below_detectability(mart):
    """Эффект землепользования ниже порога различимости — теперь на точном расчёте.

    Это проверка нашего ключевого замечания, ради которого заказчик и разделил
    индексы. Раньше оценка делалась на неполных данных и реконструкции; теперь
    официальный индекс считается точно, а вывод не изменился.
    """
    from app.services.landscape_impact import ur_official as uo

    model = uo.load_model()
    effect = uo.scenario_delta(model, 2024, 0.022)["total"]
    mde = uo.mde_official(model)["total"]
    assert 0 < effect < mde / 10, (
        f"эффект {effect:.6f} против порога {mde:.6f} — ожидался на порядок ниже"
    )


# ─────────────────────────── неопределённость и вердикт ───────────────────────────

def test_triangular_respects_bounds():
    rng = np.random.default_rng(0)
    s = uncertainty.triangular_sample(rng, 0.2, 0.3, 0.5, 5000)
    assert s.min() >= 0.2 and s.max() <= 0.5


def test_verdict_inconclusive_below_mde():
    rng = np.random.default_rng(0)
    d = uncertainty.Distribution("t", rng.normal(0.001, 0.0005, 5000))
    v = verdict.decide(d, None, mde=0.006, mde_sigma=0.012, blocking_gates_passed=True)
    assert v.code == verdict.VERDICT_INCONCLUSIVE
    assert "меньше минимального содержательного эффекта" in v.statement


def test_verdict_supports_when_clear():
    rng = np.random.default_rng(0)
    d = uncertainty.Distribution("t", rng.normal(0.05, 0.005, 5000))
    eco = uncertainty.Distribution("e", rng.normal(0.01, 0.002, 5000))
    v = verdict.decide(d, eco, mde=0.006, mde_sigma=0.012, blocking_gates_passed=True)
    assert v.code == verdict.VERDICT_SUPPORTS


def test_eco_veto_blocks_positive_verdict():
    """Рост экономики ценой экологии не даёт ответа «способствует»."""
    rng = np.random.default_rng(0)
    d = uncertainty.Distribution("t", rng.normal(0.05, 0.005, 5000))
    eco = uncertainty.Distribution("e", rng.normal(-0.03, 0.005, 5000))
    v = verdict.decide(d, eco, mde=0.006, mde_sigma=0.012, blocking_gates_passed=True)
    assert v.eco_veto is True
    assert v.code == verdict.VERDICT_INCONCLUSIVE
    assert "ценой экологического блока" in v.reason


def test_verdict_inconclusive_when_gates_failed():
    rng = np.random.default_rng(0)
    d = uncertainty.Distribution("t", rng.normal(0.05, 0.005, 5000))
    v = verdict.decide(d, None, mde=0.006, mde_sigma=0.012, blocking_gates_passed=False)
    assert v.code == verdict.VERDICT_INCONCLUSIVE


# ─────────────────────────── сквозной прогон ───────────────────────────

def test_express_estimate_shape(mart):
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = estimate_express(sc, mart=mart)
    for key in ("agri_output", "grp", "agri_output_rel", "grp_rel"):
        assert out[key]["low"] <= out[key]["base"] <= out[key]["high"]
    assert out["land_balance_delta"]["agricultural"] == pytest.approx(200.0)
    assert out["unverified_assumptions"]


def test_full_assessment_is_reproducible(mart):
    """Гейт G12: тот же вход и seed дают тот же результат."""
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    a = run_full_assessment(sc, mart=mart, draws=800, seed=42).as_dict()
    b = run_full_assessment(sc, mart=mart, draws=800, seed=42).as_dict()
    assert a["sd_index"]["deltas"]["dss_total"] == b["sd_index"]["deltas"]["dss_total"]
    assert a["verdict"]["code"] == b["verdict"]["code"]


def test_full_assessment_passes_blocking_gates_today(mart):
    """С районной панелью блокирующие гейты пройдены, и вердикт выносится по существу.

    Тест фиксирует, что ответ больше не подменяется отказом из-за гейтов.
    """
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=800).as_dict()
    assert out["gates"]["failed_blocking"] == []
    assert "блокирующий гейт" not in out["verdict"]["reason"]


def test_reserve_to_agriculture_does_not_move_ecological_block(mart):
    """Земли запаса не входят в экоблок (dss_index 1.1.0).

    В 1.0.0 у запаса была полярность −1 без нормативного источника, и перевод
    200 тыс. га запаса в с/х назначение давал экоблоку +0,034, а сценарию —
    вердикт «способствует УР» (+0,020). Без запаса эффект сводится к
    хозяйственному (+0,004) и не превышает MDE.
    """
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=800).as_dict()
    assert out["distributions"]["dss_ecological"]["median"] == pytest.approx(0.0, abs=1e-9)
    assert out["verdict"]["code"] == "inconclusive"


def test_full_assessment_without_panel_is_research_only(mart, monkeypatch):
    """Без районной панели прогон остаётся RESEARCH_ONLY из-за G5/G6."""
    from app.services.landscape_impact import pipeline
    monkeypatch.setattr(pipeline, "calibrate",
                        lambda m: calibrate(m, use_district_panel=False))
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=800).as_dict()
    assert out["gates"]["status"] == "RESEARCH_ONLY"
    assert set(out["gates"]["failed_blocking"]) & {"G5", "G6"}


@pytest.mark.parametrize("code", ["lp-v1-conservative", "lp-v2-balanced", "lp-v3-development"])
def test_lp_variant_g3_is_warning_not_blocking(mart, code):
    """Решение 11.10.2026: для вариантов ЛП выход за историю — предупреждение, не блок."""
    from app.services.landscape_impact.pipeline import variant_presets
    v = variant_presets()["variants"][code]
    sc = ScenarioInput(name=code, variant_code=code,
                       land_use_shift={"arable": v["arable"], "pasture": v["pasture"]})
    out = run_full_assessment(sc, mart=mart, draws=800).as_dict()
    g3 = next(g for g in out["gates"]["gates"] if g["code"] == "G3")
    assert g3["blocking"] is False
    assert out["gates"]["status"] != "RESEARCH_ONLY"
    dc = out["transition"]["district_check"]
    assert dc["total"] == 13
    if dc["outside"]:
        assert not g3["passed"] and "экстраполяция" in g3["detail"]


def test_district_check_counts_match_research(mart):
    """Районная сверка вариантов: V1 почти целиком в истории, V2/V3 — нет."""
    from app.services.landscape_impact.district_panel import load_district_panel
    from app.services.landscape_impact.pipeline import district_shift_check, variant_presets
    panel = load_district_panel()
    inside = {}
    for code, v in variant_presets()["variants"].items():
        dc = district_shift_check(panel, code, {"arable": v["arable"]}, 1.0)
        inside[code] = dc["inside_count"]
    assert inside["lp-v1-conservative"] >= 12
    assert inside["lp-v2-balanced"] < inside["lp-v1-conservative"]
    assert inside["lp-v3-development"] <= inside["lp-v2-balanced"]


def test_non_lp_scenario_keeps_g3_blocking(mart):
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=500).as_dict()
    g3 = next(g for g in out["gates"]["gates"] if g["code"] == "G3")
    assert g3["blocking"] is True


def test_g11_passes_when_interval_inside_mde():
    """Эффект около нуля с узким интервалом внутри ±MDE — надёжный вывод «ниже порога»."""
    near_zero = uncertainty.Distribution("dss_total", np.random.default_rng(0).normal(0.0002, 0.001, 4000))
    assert verdict.width_ok(near_zero, mde=0.013)
    assert not verdict.width_ok(near_zero, mde=0.0005)


def test_every_number_carries_a_link_label(mart):
    sc = ScenarioInput(name="тест", target_areas={"agricultural": 7363.1, "reserve": 485.6})
    out = run_full_assessment(sc, mart=mart, draws=500).as_dict()
    assert out["trace"]
    for item in out["trace"]:
        assert item["labels"]
        assert set(item["labels"]) <= {"Б", "С", "Э"}


def test_scenario_without_input_is_rejected(mart):
    with pytest.raises(ValueError):
        estimate_express(ScenarioInput(name="пусто"), mart=mart)


def test_raster_variant_requires_class_map(mart):
    sc = ScenarioInput(name="растр", class_shares={"cls_1": 0.5, "cls_2": 0.5})
    with pytest.raises(ValueError, match="класс ЛП"):
        estimate_express(sc, mart=mart)


def test_implausible_class_mapping_is_blocked(mart):
    """Карта классов, перекраивающая четверть области, помечается блокирующе.

    Проверка на реальном материале: у растров ЛП пять содержательных классов
    покрывают 99,5-99,9 % области, а земли сельскохозяйственного назначения
    занимают 73,1 %. Если сопоставить классы категориям «в лоб», расчёт
    проходит и выдаёт правдоподобные числа, хотя под ними лежит перевод лесов и
    водного фонда в пашню. Ловится только по объёму перемещений.
    """
    base = mart.land_vector(mart.years[-1])
    shares = {"1": 0.31, "2": 0.08, "3": 0.06, "4": 0.51, "5": 0.04}
    naive_map = dict.fromkeys(shares, "agricultural")

    tm = transitions.from_class_shares(base, {}, shares, naive_map)
    blocking = [v for v in tm.violations if v["rule"] == "implausible_class_mapping"]
    assert blocking, "перекройка четверти области обязана блокироваться"
    assert blocking[0]["severity"] == "blocking"
    assert blocking[0]["moved_share"] > transitions.IMPLAUSIBLE_MOVE_SHARE


def test_gate_threshold_exceeds_observed_history(mart):
    """Порог гейта выше всего, что область переместила за годы наблюдений.

    Иначе гейт блокировал бы не ошибки отображения, а саму реальность.
    """
    years = mart.years
    total = float(mart.land_vector(years[0]).sum())
    moved = [
        float(np.abs(mart.land_vector(years[i]) - mart.land_vector(years[i - 1])).sum()) / 2
        for i in range(1, len(years))
    ]
    assert max(moved) / total < transitions.IMPLAUSIBLE_MOVE_SHARE

    net = float(np.abs(mart.land_vector(years[-1]) - mart.land_vector(years[0])).sum()) / 2
    assert net / total < transitions.IMPLAUSIBLE_MOVE_SHARE


def test_plausible_class_mapping_passes(mart):
    """Умеренное перераспределение гейт не трогает — он не глушит работу."""
    base = mart.land_vector(mart.years[-1])
    total = float(base.sum())
    shares = {c: float(v) / total for c, v in zip(LAND_CATEGORIES, base)}
    shares["agricultural"] -= 0.01
    shares["forest"] += 0.01
    identity_map = {c: c for c in LAND_CATEGORIES}

    tm = transitions.from_class_shares(base, {}, shares, identity_map)
    assert not [v for v in tm.violations if v["rule"] == "implausible_class_mapping"]


# ─────────────────────────── реестр допущений ───────────────────────────

def test_all_assumptions_have_a_source():
    for key, a in list_assumptions().items():
        assert a.source and len(a.source) > 20, f"У коэффициента {key} нет содержательного источника"
        assert a.status in {"unsigned", "data_derived", "data_derived_unverified", "design_decision"}


def test_unsigned_assumptions_are_listed():
    assert set(unverified_keys()) <= set(list_assumptions())
    assert "multiplier_m" in unverified_keys()


def test_triples_are_ordered():
    for key, a in list_assumptions().items():
        if a.is_triple:
            assert a.low <= a.base <= a.high, f"Диапазон коэффициента {key} не упорядочен"


# ── Сдвиг угодий внутри земель с/х назначения (наложение ЛП на маски категорий) ──

def test_land_use_shift_changes_arable_not_balance(mart):
    """Сдвиг угодий меняет пашню и пастбища, но не категории земель: баланс прежний."""
    sc = ScenarioInput(name="сдвиг", land_use_shift={"arable": 300.0, "pasture": -300.0},
                       horizon_years=30)
    out = estimate_express(sc, mart=mart)
    assert all(abs(v) < 1e-9 for v in out["land_balance_delta"].values())
    assert out["agri_output"]["base"] > 0  # больше пашни — больше продукции
    check = out["land_use_shift_check"]
    assert check["applied"]["arable"] == pytest.approx(300.0 * check["realized_fraction"], abs=0.1)


def test_land_use_shift_sign_and_check_flags_history(mart):
    """Вывод полей из оборота уменьшает продукцию; сдвиг сверх исторического размаха помечается."""
    small = estimate_express(ScenarioInput(name="−", land_use_shift={"arable": -100.0}, horizon_years=30),
                             mart=mart)
    assert small["agri_output"]["base"] < 0
    big = estimate_express(ScenarioInput(name="+", land_use_shift={"arable": 1400.0, "pasture": -1400.0},
                                         horizon_years=30), mart=mart)
    assert big["land_use_shift_check"]["exceeds_history"] is True
    assert "потенциал зонирования" in big["land_use_shift_check"]["note"]


def test_pasture_never_negative(mart):
    params = {"arable_share_of_agricultural": 0.685, "pasture_share_of_agricultural": 0.273,
                   "sown_share_of_arable": 0.88, "grain_share_of_sown": 0.72,
                   "oil_share_of_sown": 0.2, "fodder_share_of_sown": 0.09}
    lu = agro.land_use_from_balance(mart.land_vector(2024), params, {"pasture": -1e6})
    assert lu.pasture == 0.0


def test_variant_presets_reference_is_consistent():
    """Справочник значений по умолчанию: сдвиг пашни = распашка − выбытие полей."""
    import json
    from pathlib import Path
    ref = json.loads((Path(__file__).resolve().parents[1] / "app" / "services" / "landscape_impact"
                      / "reference" / "lp_variant_presets.json").read_text(encoding="utf-8"))
    assert set(ref["variants"]) == {"lp-v1-conservative", "lp-v2-balanced", "lp-v3-development"}
    for v in ref["variants"].values():
        c = v["components"]
        expected = c["other_to_arable"] + c["other_to_irrigated"] - c["fields_to_regulated"] - c["fields_to_pasture"]
        assert v["arable"] == pytest.approx(expected, abs=0.2)
        assert len(v["districts"]) == 14
    assert "[Э]" in ref["rule"]
