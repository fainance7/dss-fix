"""Сдвиг угодий для вариантов ЛП V1–V3 из наложения на маски категорий земель.

Вход — data/research/lp_overlay_land_masks.json (scripts/research/lp_overlay_land_masks.py).
Выход — ai-service/app/services/landscape_impact/reference/lp_variant_presets.json,
справочник значений по умолчанию для входа `land_use_shift` алгоритма ЗБ-УР.

Правило [Э] (экспертное допущение, явное и параметризуемое):
- поля (маска сельхозугодий 2025) в классе 1 «регулируемое» выбывают из пашни;
- поля в классе 5 «пастбищное» переходят из пашни в пастбища;
- прочие земли вне масок в классах 4 «пашня» и 2 «орошение» распахиваются (из пастбищ);
- поля в классах 2 и 4 остаются пашней; прочие земли в классах 1 и 5 — без изменений;
- вода, населённые пункты, дороги, лес, ООПТ не меняются: перевод между категориями
  земель требует отдельного решения и в правило не входит; NoData вариантов — без изменений.

Δпашни = прочие{2,4} − поля{1,5};  Δпастбищ = поля{5} − прочие{2,4}.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "research" / "lp_overlay_land_masks.json"
OUT = ROOT / "ai-service" / "app" / "services" / "landscape_impact" / "reference" / "lp_variant_presets.json"

VARIANTS = {"V1": "lp-v1-conservative", "V2": "lp-v2-balanced", "V3": "lp-v3-development"}


def shift(fields: dict, other: dict) -> dict:
    g = lambda d, k: float(d.get(k, 0.0)) / 1000  # га → тыс. га
    plowed = g(other, "2") + g(other, "4")
    return {
        "arable": round(plowed - g(fields, "1") - g(fields, "5"), 1),
        "pasture": round(g(fields, "5") - plowed, 1),
        "components": {
            "fields_to_regulated": round(g(fields, "1"), 1),
            "fields_to_pasture": round(g(fields, "5"), 1),
            "other_to_arable": round(g(other, "4"), 1),
            "other_to_irrigated": round(g(other, "2"), 1),
            "fields_unzoned": round(g(fields, "nodata"), 1),
            "other_unzoned": round(g(other, "nodata"), 1),
        },
    }


def main() -> None:
    d = json.loads(SRC.read_text(encoding="utf-8"))
    districts = [n for n in d["districts"] if n != "вне районов"]
    out = {
        "version": "2026-10-01",
        "source": ("Наложение растров вариантов ЛП (результаты 11.09.2026, полное разрешение) на маски "
                   "категорий земель смежной команды (партия 2026-10-02_lp): сельхозугодья 2025, вода, "
                   "населённые пункты, дороги, лес, ООПТ. docs/Наложение_ЛП_на_маски_категорий_земель_2026-10-01.md"),
        "rule": ("[Э] Поля в классе «регулируемое» выбывают из пашни, поля в классе «пастбищное» переходят "
                 "в пастбища; прочие земли вне масок в классах «пашня» и «орошение» распахиваются из пастбищ. "
                 "Вода, населённые пункты, дороги, лес и ООПТ не меняются; незонированные земли — без изменений. "
                 "Значения — потенциал при полной реализации зонирования, а не план. Урожайность на распаханных "
                 "землях принята средней по области, что завышает эффект: это пастбища, залежи и солончаки, "
                 "их пригодность ниже — учитывается параметром «сдвиг класса пригодности»."),
        "variants": {},
    }
    for v, code in VARIANTS.items():
        def agg(cat):
            s = {}
            for n in districts:
                for k, a in d["exclusive"][n][cat][v].items():
                    s[k] = s.get(k, 0.0) + a
            return s
        oblast = shift(agg("agri"), agg("other"))
        out["variants"][code] = {
            **oblast,
            "districts": {
                n.split(" (")[0]: shift(d["exclusive"][n]["agri"][v], d["exclusive"][n]["other"][v])
                for n in districts
            },
        }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for code, x in out["variants"].items():
        print(code, "пашня", x["arable"], "пастбища", x["pasture"], x["components"])


if __name__ == "__main__":
    main()
