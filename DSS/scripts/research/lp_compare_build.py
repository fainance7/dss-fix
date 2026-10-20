"""Критерии многокритериального сравнения вариантов ЛП (V1–V3) → backend/app/data/lp_compare.json.

Источники (оба — по полному разрешению растров, общая сетка 30,92 м):
- data/research/lp_compare_crosstab.json — пересечение вариантов с интегральной природной
  значимостью, интегральной уязвимостью и пригодностью для богарного земледелия
  (scripts/research/lp_compare_crosstab.py);
- data/research/lp_overlay_land_masks.json — наложение вариантов на маски категорий земель
  от смежной команды, взаимоисключающий учёт (scripts/research/lp_overlay_land_masks.py).

Значения — сырые площади, тыс. га, по области и 14 территориям. Нормирование, веса и
выбор система делает на экране сравнения — прозрачно для пользователя; здесь только факты.

Запуск: python3 scripts/research/lp_compare_build.py
"""

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CROSS = ROOT / "data/research/lp_compare_crosstab.json"
MASKS = ROOT / "data/research/lp_overlay_land_masks.json"
OUT = ROOT / "backend/app/data/lp_compare.json"

VARIANTS = ["V1", "V2", "V3"]
INTENSIVE = ("2", "4")  # орошение, пашенное земледелие
ECONOMIC = ("2", "3", "4", "5")  # орошение, талые воды, пашня, пастбища
# Максимальный годовой оборот земель СКО по балансу 2014–2024 — 3,2 % площади области
# (ai-service/app/services/landscape_impact/transitions.py).
HIST_MAX_SHARE = 0.032
# Площадь области по земельному балансу (сумма 7 категорий, неизменна 2014–2024), тыс. га.
OBLAST_AREA_THA = 9804.3

CRITERIA = [
    {"id": "nature_value", "block": "nature", "better": "lower",
     "label": "Земли высокой и очень высокой природной значимости под пашней и орошением",
     "source": "Интегральная природная значимость (классы 4–5) × варианты ЛП, классы 2 и 4"},
    {"id": "vulnerable", "block": "nature", "better": "lower",
     "label": "Земли наибольшей уязвимости под пашней и орошением",
     "source": "Интегральная уязвимость, класс 3 — наивысший в слое (шкала 1–5) × варианты ЛП, классы 2 и 4"},
    {"id": "oopt", "block": "nature", "better": "lower",
     "label": "Особо охраняемые природные территории под пашней и орошением",
     "source": "Маска ООПТ (смежная команда, 01.10.2026) × варианты ЛП, классы 2 и 4"},
    {"id": "forest", "block": "nature", "better": "lower",
     "label": "Земли лесного фонда под пашней и орошением",
     "source": "Маска лесного фонда (смежная команда, 01.10.2026) × варианты ЛП, классы 2 и 4"},
    {"id": "suitable_cropland", "block": "economy", "better": "higher",
     "label": "Пашня и орошение на землях высокой и очень высокой пригодности для богарного земледелия",
     "source": "Пригодность для богарного земледелия (классы 4–5) × варианты ЛП, классы 2 и 4"},
    {"id": "economic_area", "block": "economy", "better": "higher",
     "label": "Площадь хозяйственного использования (орошение, талые воды, пашня, пастбища)",
     "source": "Варианты ЛП, классы 2–5"},
    {"id": "agri_withdrawn", "block": "economy", "better": "lower",
     "label": "Сельхозугодья, выводимые из оборота под регулируемое природопользование",
     "source": "Маска сельхозугодий 2025 г. × варианты ЛП, класс 1"},
    {"id": "land_change", "block": "feasibility", "better": "lower",
     "label": "Объём изменения землепользования",
     "source": ("Сельхозугодья под регулируемое природопользование + распашка и орошение земель, "
                "не являющихся сельхозугодьями (прочие, ООПТ, лес)")},
]
BLOCKS = {
    "nature": "Сохранение природы",
    "economy": "Хозяйственный эффект",
    "feasibility": "Реализуемость",
}


def kato(key: str) -> str | None:
    m = re.search(r"\((\d{9})\)\s*$", key)
    return m.group(1) if m else None


def name(key: str) -> str:
    return re.sub(r"\s*\(\d{9}\)\s*$", "", key)


def main() -> None:
    cross = json.loads(CROSS.read_text(encoding="utf-8"))
    masks = json.loads(MASKS.read_text(encoding="utf-8"))
    ex = masks["exclusive"]

    def cross_sum(layer: str, dist: str, v: str, vclasses, lclasses) -> float:
        cells = cross["cross"][layer][dist][v]
        return sum(ha for k, ha in cells.items()
                   if k.split("-")[0] in vclasses and k.split("-")[1] in lclasses)

    def mask_sum(dist: str, cats, v: str, vclasses) -> float:
        return sum(ex.get(dist, {}).get(c, {}).get(v, {}).get(cl, 0.0) for c in cats for cl in vclasses)

    mask_key = {kato(k): k for k in ex if kato(k)}
    territories = []
    for dist in cross["districts"]:
        code = kato(dist)
        mk = mask_key.get(code)
        if mk is None:
            raise SystemExit(f"Нет района {dist} в наложении на маски")
        vals = {}
        for v in VARIANTS:
            withdrawn = mask_sum(mk, ["agri"], v, ["1"])
            vals[v] = {
                "nature_value": cross_sum("lp-z-integrated", dist, v, INTENSIVE, ("4", "5")),
                "vulnerable": cross_sum("lp-u-integrated", dist, v, INTENSIVE, ("3",)),
                "oopt": mask_sum(mk, ["oopt"], v, INTENSIVE),
                "forest": mask_sum(mk, ["forest"], v, INTENSIVE),
                "suitable_cropland": cross_sum("lp-p-crop", dist, v, INTENSIVE, ("4", "5")),
                "economic_area": sum(cross["variant_total"][dist][v].get(c, 0.0) for c in ECONOMIC),
                "agri_withdrawn": withdrawn,
                "land_change": withdrawn + mask_sum(mk, ["other", "oopt", "forest"], v, INTENSIVE),
            }
        territories.append({"kato": code, "name": name(dist), "values": vals})

    oblast = {v: {c["id"]: sum(t["values"][v][c["id"]] for t in territories) for c in CRITERIA}
              for v in VARIANTS}

    def k(x: float) -> float:  # га → тыс. га
        return round(x / 1000, 1)

    out = {
        "built_at": date.today().isoformat(),
        "unit": "тыс. га",
        "variants": {
            "V1": {"group": "lp-v1-conservative", "label": "V1 — природоохранный",
                   "dataset_id": cross["variants"]["V1"]},
            "V2": {"group": "lp-v2-balanced", "label": "V2 — сбалансированный",
                   "dataset_id": cross["variants"]["V2"]},
            "V3": {"group": "lp-v3-development", "label": "V3 — хозяйственно-ориентированный",
                   "dataset_id": cross["variants"]["V3"]},
        },
        "blocks": BLOCKS,
        "criteria": CRITERIA,
        "historical_max_change_per_year": round(OBLAST_AREA_THA * HIST_MAX_SHARE, 1),
        "oblast": {v: {c: k(x) for c, x in vals.items()} for v, vals in oblast.items()},
        "territories": [
            {"kato": t["kato"], "name": t["name"],
             "values": {v: {c: k(x) for c, x in vals.items()} for v, vals in t["values"].items()}}
            for t in territories
        ],
        "sources": [
            "Варианты функционального зонирования V1–V3 (ГИС-контур, расчёт ЛП от 11.09.2026), полное разрешение 30,9 м",
            "Интегральная природная значимость, интегральная уязвимость, пригодность для богарного земледелия — слои того же расчёта ЛП",
            "Маски категорий земель (сельхозугодья 2025, лесной фонд, ООПТ, вода, населённые пункты, дороги) — смежная команда, 01.10.2026",
            "Максимальный годовой оборот земель — земельный баланс СКО 2014–2024 гг. (3,2 % площади области)",
        ],
        "limitations": [
            "Классы вариантов — приоритетные направления использования, а не категории земель: площади показывают, что вариант предлагает, а не что будет сделано.",
            "24 % области (вода, населённые пункты, часть сельхозугодий и прочих земель) в вариантах не зонирована и в критерии не входит.",
            "Интегральная уязвимость принимает значения 1–3 из шкалы 1–5; «наибольшая уязвимость» — класс 3.",
            "Критерии частично пересекаются (например, ООПТ входит и в природный блок, и в объём изменений) — это учитывается при назначении весов.",
            "Экологический блок индекса устойчивого развития по районам и стоимость перевода земель отсутствуют — эти критерии не учтены.",
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{OUT}: {len(territories)} территорий")
    for v in VARIANTS:
        print(v, out["oblast"][v])
    print("исторический максимум оборота, тыс. га/год:", out["historical_max_change_per_year"])


if __name__ == "__main__":
    main()
