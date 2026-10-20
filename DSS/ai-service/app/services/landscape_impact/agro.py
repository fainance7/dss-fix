"""Шаги 4-5 алгоритма ЗБ-УР: земельный баланс → угодья → урожайность → продукция.

Цепь построена так, чтобы максимум переносился на балансовые тождества [Б] и
минимум — на оцениваемые коэффициенты [С]: каждый оцениваемый параметр стоит
наблюдений, которых при 11 годовых точках нет.

    [Б]  пашня      = земли с/х назначения × доля пашни
    [Б]  посев      = пашня × доля посева
    [Б]  посев_c    = посев × доля культуры c
    [С/Э] урожайность: Δln Y = a + b · Δln(климат) + поправка на пригодность
    [Б]  сбор_c     = посев_c × урожайность_c
    [Б]  ВП СХ      = растениеводство(сборы, веса выручки) + животноводство(пастбища)

Абсолютные цены в расчёт не входят: оценка строится на относительном изменении
относительно базового сценария того же года, где уровень цен сокращается. Это
снимает зависимость от дефляторов, которых заказчик не передал.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from app.services.landscape_impact.datamart import LAND_CATEGORIES, DataMart

CROPS: tuple[str, ...] = ("grain", "oil", "fodder")


@dataclass
class LandUseState:
    """Угодья и посевные площади, тыс. га."""

    arable: float
    pasture: float
    sown_total: float
    sown: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "arable": round(self.arable, 2), "pasture": round(self.pasture, 2),
            "sown_total": round(self.sown_total, 2),
            "sown": {k: round(v, 2) for k, v in self.sown.items()},
        }


@dataclass
class AgroState:
    """Урожайности, валовые сборы и валовая продукция сельского хозяйства."""

    land_use: LandUseState
    yields: dict[str, float]
    harvest: dict[str, float]          # тыс. тонн
    crop_revenue_index: float          # выручка растениеводства в зерновом эквиваленте, тыс. тонн
    livestock_index: float             # объёмный показатель животноводства, безразмерный

    def as_dict(self) -> dict[str, Any]:
        return {
            "land_use": self.land_use.as_dict(),
            "yields": {k: round(v, 3) for k, v in self.yields.items()},
            "harvest": {k: round(v, 1) for k, v in self.harvest.items()},
            "crop_revenue_index": round(self.crop_revenue_index, 4),
            "livestock_index": round(self.livestock_index, 6),
        }


def land_use_from_balance(land_vector: np.ndarray, params: dict[str, float],
                          land_use_shift: Optional[dict[str, float]] = None) -> LandUseState:
    """[Б] Перевод категорий земель в угодья и посевные площади.

    `land_use_shift` — [Э] сдвиг угодий внутри земель с/х назначения, тыс. га
    ({"arable": Δ, "pasture": Δ}), уже умноженный на долю реализации. Нужен для
    вариантов ЛП, которые меняют использование земель, не меняя их категорию:
    распашка пастбищ и залежей, вывод полей в регулируемое использование.
    """
    agricultural = float(land_vector[LAND_CATEGORIES.index("agricultural")])
    shift = land_use_shift or {}
    arable = max(0.0, agricultural * params["arable_share_of_agricultural"] + float(shift.get("arable", 0.0)))
    pasture = max(0.0, agricultural * params["pasture_share_of_agricultural"] + float(shift.get("pasture", 0.0)))
    sown_total = arable * params["sown_share_of_arable"]

    shares = {
        "grain": params["grain_share_of_sown"],
        "oil": params["oil_share_of_sown"],
        "fodder": params["fodder_share_of_sown"],
    }
    # Доли культур нормируются: их сумма — оценка по окну и не обязана быть точно 1.
    ssum = sum(shares.values())
    if ssum <= 0:
        raise ValueError("Сумма долей культур в посевной площади неположительна")
    sown = {c: sown_total * shares[c] / ssum for c in CROPS}
    return LandUseState(arable=arable, pasture=pasture, sown_total=sown_total, sown=sown)


def yields_from_climate(base_yields: dict[str, float], climate_ratio: float,
                        elasticity: float, suitability_shift: float,
                        k_suitability: float) -> dict[str, float]:
    """[С]/[Э] Урожайность от климата и от сдвига класса пригодности пашни.

    `climate_ratio` — отношение климатического регрессора сценарного года к
    базовому. При равенстве климата (сравнение сценария с базовым в одном и том
    же году) отношение равно 1 и климатический член обращается в ноль: климат
    сокращается, и остаётся только эффект землепользования. Это принципиально —
    иначе климатическая изменчивость перекрыла бы эффект варианта.
    """
    factor = float(climate_ratio) ** float(elasticity)
    suit = 1.0 + float(k_suitability) * float(suitability_shift)
    return {c: float(y) * factor * suit for c, y in base_yields.items()}


def compute_agro(land_vector: np.ndarray, base_yields: dict[str, float],
                 params: dict[str, float], *, climate_ratio: float = 1.0,
                 suitability_shift: float = 0.0,
                 land_use_shift: Optional[dict[str, float]] = None) -> AgroState:
    """Полный агроконтур для одного состояния земельного баланса."""
    lu = land_use_from_balance(land_vector, params, land_use_shift)
    ys = yields_from_climate(
        base_yields, climate_ratio,
        elasticity=params["yield_climate_elasticity_grain"],
        suitability_shift=suitability_shift,
        k_suitability=params["k_suitability_grain"],
    )
    # [Б] сбор = площадь (тыс. га) × урожайность (ц/га) / 10 = тыс. тонн
    harvest = {c: lu.sown[c] * ys[c] / 10.0 for c in CROPS}

    # Веса выручки внутри растениеводства через относительные цены: абсолютный
    # уровень цен сокращается при переходе к отношению сценарий/база.
    price = {"grain": 1.0,
             "oil": params["price_ratio_oil_to_grain"],
             "fodder": params["price_ratio_fodder_to_grain"]}
    crop_revenue = sum(harvest[c] * price[c] for c in CROPS)

    # Животноводство привязано к пастбищам с эластичностью < 1: площадь пастбищ
    # в СКО не является связывающим ограничением поголовья.
    livestock = lu.pasture ** params["livestock_pasture_elasticity"]

    # Взвешенного сложения растениеводства и животноводства здесь НЕТ намеренно:
    # выручка растениеводства измеряется в зерновом эквиваленте (тысячи тонн), а
    # показатель животноводства безразмерен, и их сумма не имеет смысла. Веса
    # применяются к ОТНОСИТЕЛЬНЫМ изменениям обеих частей — см. economy.py.
    return AgroState(
        land_use=lu, yields=ys, harvest=harvest,
        crop_revenue_index=crop_revenue, livestock_index=livestock,
    )


def calibrate_base_yields(mart: DataMart, year: int) -> dict[str, float]:
    """Базовые урожайности года: факт по зерновым и масличным, кормовые — оценка.

    Ряда урожайности кормовых культур заказчик не передал. Кормовые входят в
    расчёт только через вес выручки (наименьший из трёх), поэтому берётся
    консервативная привязка к урожайности зерновых с явной пометкой.
    """
    i = mart.year_index(year)
    grain = float(mart.yields["grain"][i])
    oil = float(mart.yields["oil"][i])
    return {"grain": grain, "oil": oil, "fodder": grain}
