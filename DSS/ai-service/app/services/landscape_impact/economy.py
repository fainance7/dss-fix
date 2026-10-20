"""Шаг 5 (продолжение) алгоритма ЗБ-УР: продукция сельского хозяйства → ВРП.

    [Б] ΔВП СХ / ВП СХ  = индекс сценария / индекс базы − 1
    [Э] ΔВДС            = ΔВП СХ × доля ВДС
    [Э] ΔВРП            = ΔВДС × (1 + m), где m — мультипликатор смежных отраслей

Расчёт ведётся в относительных величинах. Абсолютные значения выводятся
умножением на фактическую базу последнего года, и это единственное место, где
появляются деньги: точечный прогноз абсолютного ВРП на дальний горизонт при
11 наблюдениях не декларируется.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.landscape_impact.agro import AgroState


@dataclass
class EconomyDelta:
    """Относительные и абсолютные изменения экономических показателей."""

    agri_output_rel: float
    agri_output_abs: float
    crop_rel: float
    livestock_rel: float
    vds_abs: float
    grp_rel: float
    grp_abs: float
    base_agri_output: float
    base_grp: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "agri_output": {
                "base": round(self.base_agri_output, 3),
                "delta_abs": round(self.agri_output_abs, 3),
                "delta_rel": round(self.agri_output_rel, 6),
                "unit": "млрд тенге",
            },
            "decomposition": {
                "crop_rel": round(self.crop_rel, 6),
                "livestock_rel": round(self.livestock_rel, 6),
            },
            "vds_agri": {"delta_abs": round(self.vds_abs, 3), "unit": "млрд тенге"},
            "grp": {
                "base": round(self.base_grp, 3),
                "delta_abs": round(self.grp_abs, 3),
                "delta_rel": round(self.grp_rel, 6),
                "unit": "млрд тенге",
            },
        }


def compute_economy(scenario: AgroState, baseline: AgroState, params: dict[str, float],
                    base_agri_output: float, base_grp: float,
                    lag_factor: float = 1.0) -> EconomyDelta:
    """Экономический контур: относительное изменение продукции → ВРП.

    Валовая продукция сельского хозяйства раскладывается на растениеводство и
    животноводство, и веса применяются к их ОТНОСИТЕЛЬНЫМ изменениям, а не к
    самим величинам: выручка растениеводства выражена в зерновом эквиваленте,
    показатель животноводства безразмерен, и складывать их напрямую нельзя.
    В относительных изменениях уровень цен сокращается — поэтому расчёт не
    зависит от дефляторов, которых заказчик не передал.
    """
    if baseline.crop_revenue_index == 0 or baseline.livestock_index == 0:
        raise ValueError("Базовые показатели валовой продукции равны нулю")
    crop_rel = scenario.crop_revenue_index / baseline.crop_revenue_index - 1.0
    live_rel = scenario.livestock_index / baseline.livestock_index - 1.0
    w_crop = params["crop_share_of_agri_output"]
    rel = (w_crop * crop_rel + (1.0 - w_crop) * live_rel) * float(lag_factor)
    agri_abs = rel * base_agri_output
    vds_abs = agri_abs * params["vds_share_agri"]
    grp_abs = vds_abs * (1.0 + params["multiplier_m"])
    grp_rel = grp_abs / base_grp if base_grp else 0.0
    return EconomyDelta(
        agri_output_rel=rel, agri_output_abs=agri_abs,
        crop_rel=crop_rel, livestock_rel=live_rel, vds_abs=vds_abs,
        grp_rel=grp_rel, grp_abs=grp_abs,
        base_agri_output=base_agri_output, base_grp=base_grp,
    )
