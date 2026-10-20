"""b4. Применение найденных связей к вариантам ЛП V1–V3 и расчёт индекса УР по методике НИР.

Вход вариантов — сдвиг пашни и пастбищ по районам из наложения на маски земель
(`reference/lp_variant_presets.json`, [Э]: потенциал при полной реализации зонирования).
Базовый год — 2024. Индекс пересчитывается по п. 12 методики: фиксированные границы,
усечение 0–1, по каждой территории, затем невзвешенное среднее по 14 (`index_with_overrides`).

Каналы:
  A. Конструкция методики: пашня → посевы → ВП СХ на душу (вес 0,11 в экономическом блоке).
     Отдача новых земель = средней по району (φ = 1) — верхняя граница.
  B. Эмпирические связи со статусом [С] из b3 (районная панель), с интервалом по кластерному бутстрэпу.
  C. «Наивный» вариант метода: все точечные наклоны среза b2 (пашня / (пашня+пастбища) → 14
     индикаторов) без отбора по значимости и без контроля состава населения. Показывает, что дал
     бы метод при некритичном применении.
Вывод: таблица по вариантам и /wlb/b4_results.json.
"""
import json

import numpy as np
import pandas as pd

from common import LABELS, W, district_series, index_with_overrides, key, load_panel, ur_model
from app.services.landscape_impact import ur_official as uo

d = load_panel()
model = ur_model()
BASE = 2024
yi = model.years.index(BASE)
MDE = uo.mde_official(model)["total"]

pres = json.load(open("/app/app/services/landscape_impact/reference/lp_variant_presets.json"))["variants"]
LR, AG = "Земельные ресурсы", "Сельское хозяйство"
arable = district_series(d, "Площадь пашни", LR).iloc[:, -1]
pasture = district_series(d, "Площадь пастбищ", LR).iloc[:, -1]
sown_all = district_series(d, "Посевная площадь", AG)[BASE]
sown_main = (district_series(d, "Посевная площадь зерновых культур", AG)[BASE]
             + district_series(d, "Посевная площадь масличных культур", AG)[BASE])
vp_crop = district_series(d, "Валовая продукция растениеводства", AG)[BASE]
vp_live = district_series(d, "Валовая продукция животноводства", AG)[BASE]
crop_share = vp_crop / (vp_crop + vp_live)
sown_ratio = (sown_all / arable).clip(upper=1.0)

b2 = pd.read_csv(f"{W}/b2_links.csv")
b3 = pd.read_csv(f"{W}/b3_links.csv")
CODE = {v: k for k, v in LABELS.items()}


def transformed(ind, k):
    t = next(t for t in model.territories if key(t) == k)
    block = next(b for b, s in model.blocks.items() if ind in s["indicators"])
    return uo.compute_indicator(model, block, ind, t)["transformed"][yi]


_, base = index_with_overrides(model, BASE)
assert abs(base["total"] - np.mean([uo.compute_blocks(model, t)["total"][yi] for t in model.territories])) < 1e-12

results, extrap = {}, {}
for vid, short in (("lp-v1-conservative", "V1"), ("lp-v2-balanced", "V2"), ("lp-v3-development", "V3")):
    shift = {key(n): x for n, x in pres[vid]["districts"].items() if key(n) and key(n) != "петропавл"}
    dx = {}
    for k, x in shift.items():
        d_sown = x["arable"] * float(sown_ratio.get(k, 0.9))
        dx[k] = {
            "d_ln_sown": float(np.log(max(1e-6, 1 + d_sown / float(sown_main[k])))),
            "d_ln_herd": float(np.log(max(1e-6, 1 + x["pasture"] / float(pasture[k])))),  # [Э] поголовье ∝ пастбищам
            "agri_rel": float(crop_share.get(k, 0.8)) * d_sown / float(sown_all[k]),
            "d_ratio": float((arable[k] + x["arable"]) / (arable[k] + x["arable"] + pasture[k] + x["pasture"])
                             - arable[k] / (arable[k] + pasture[k])),
            "arable_pct": 100 * x["arable"] / float(arable[k]),
        }
    extrap[short] = {k: round(v["arable_pct"], 1) for k, v in dx.items()}
    out = {}

    # A. Конструкция: ВП СХ на душу
    ov = {k: {"agri_pc": transformed("agri_pc", k) * (1 + v["agri_rel"])} for k, v in dx.items()}
    out["A"] = {b: index_with_overrides(model, BASE, ov)[1][b] - base[b] for b in base}

    # B. Связи [С] из b3: точка и границы бутстрэпа
    ok = b3[b3["статус"] == "[С]"]
    for tag, col in (("B", "наклон"), ("B_lo", "boot_lo"), ("B_hi", "boot_hi")):
        ov = {}
        for k, v in dx.items():
            for _, r in ok.iterrows():
                if r["код"] in base:      # индексы блоков напрямую не подменяем — только индикаторы
                    continue
                xshift = v["d_ln_sown"] if r.x == "ln_sown" else v["d_ln_herd"]
                cur = ov.get(k, {}).get(r["код"], transformed(r["код"], k))
                ov.setdefault(k, {})[r["код"]] = cur + r[col] * xshift
        out[tag] = {b: index_with_overrides(model, BASE, ov)[1][b] - base[b] for b in base}
    out["B_links"] = [f"{r.x} → {r['показатель']}" for _, r in ok.iterrows() if r["код"] not in base]

    # C. Наивный срез: пашня/(пашня+пастбища) → 14 индикаторов, окно 2022–2024, без отбора
    nv = b2[(b2["окно"] == "2022–2024") & (b2["земля"] == "Пашня / (пашня+пастбища)")]
    ov = {}
    for k, v in dx.items():
        for _, r in nv.iterrows():
            code = CODE.get(r["показатель"])
            if code in base or code is None:
                continue
            ov.setdefault(k, {})[code] = transformed(code, k) + r["наклон"] * v["d_ratio"]
    out["C"] = {b: index_with_overrides(model, BASE, ov)[1][b] - base[b] for b in base}
    results[short] = out

pd.set_option("display.width", 250)
print(f"База {BASE}: индекс УР области = {base['total']:.4f}; порог различимости (MDE) = {MDE:.4f}")
print("Изменение пашни района по вариантам, % к пашне 2026 (исторический размах области 2014–2023 — 3,5 %):")
print(pd.DataFrame(extrap).to_string())
names = {"A": "A. Конструкция (ВП СХ)", "B": "B. Связи [С] b3 (точка)", "B_lo": "B. бутстрэп 5 %",
         "B_hi": "B. бутстрэп 95 %", "C": "C. Наивный срез b2"}
rows = []
for ch, nm in names.items():
    for v in ("V1", "V2", "V3"):
        rows.append({"канал": nm, "вариант": v, **{b: results[v][ch][b] for b in ("social", "economic", "ecological", "total")}})
T = pd.DataFrame(rows)
print("\nΔ индекса к базе 2024:")
print(T.round(5).to_string(index=False))
print("\nРазница V3 − V1 по итоговому индексу и отношение к MDE:")
for ch, nm in names.items():
    dd = results["V3"][ch]["total"] - results["V1"][ch]["total"]
    print(f"  {nm:26s} {dd:+.5f}  ({abs(dd) / MDE:.2f} MDE)")
print("\nСвязи канала B:", results["V1"]["B_links"])
json.dump({"base": base, "mde": MDE, "results": results, "arable_pct": extrap}, open(f"{W}/b4_results.json", "w"),
          ensure_ascii=False, indent=1)
