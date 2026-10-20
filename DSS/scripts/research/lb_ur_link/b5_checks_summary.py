"""b5. Санити-проверки и сводка для оценки применимости метода.

1. Воспроизведение опубликованного индекса (verify_against_published) — MAE должен быть 0.
2. Калибровка перестановочного теста на нулевых данных: районы в срезе b2 перемешиваются
   200 раз, доля p < 0,05 должна быть около 5 %, а доля q < 0,05 — около нуля.
3. Сводка по семьям гипотез b1–b3 и каналам b4.
"""
import json

import numpy as np
import pandas as pd

from common import W, bh, perm_p_corr, ur_model
from app.services.landscape_impact import ur_official as uo

model = ur_model()
ver = uo.verify_against_published(model)
print("1. Сверка с книгой заказчика:", {b: (v["n"], round(v["mae"], 6)) for b, v in ver.items()})

# 2. Нулевая калибровка на реальной структуре среза: одна пара x (доля пашни) против всех y.
import b2_district_cross as b2m  # пересчитывает срез; печать b2 подавляется ниже

SH = b2m.SH
Y = b2m.level(2022, 2024).loc[SH.index]
x = SH["Доля пашни"].values.astype(float)
rng = np.random.default_rng(7)
share_p, share_q = [], []
for _ in range(200):
    perm = rng.permutation(len(x))
    ps = [perm_p_corr(x[perm], Y[c].values.astype(float), n=999)[1] for c in Y if np.isfinite(Y[c].values).all()]
    ps = np.array(ps)
    share_p.append((ps < 0.05).mean())
    share_q.append((bh(ps) < 0.05).any())
print(f"2. Нулевые данные: доля p<0,05 = {np.mean(share_p):.3f} (ожидание 0,05); "
      f"доля прогонов хотя бы с одним q<0,05 = {np.mean(share_q):.3f} (FWER-подобная величина, ожидание ≤ 0,05)")

# 3. Сводка
b1 = pd.read_csv(f"{W}/b1_links.csv")
b2 = pd.read_csv(f"{W}/b2_links.csv")
b3 = pd.read_csv(f"{W}/b3_links.csv")
b4 = json.load(open(f"{W}/b4_results.json"))
print("3. Сводка семей гипотез:")
for name, f, qcol in (("b1 область L", b1[b1["семья"].str.startswith("L")], "q"),
                      ("b1 область S", b1[b1["семья"].str.startswith("S")], "q"),
                      ("b2 срез 2022–24", b2[b2["окно"] == "2022–2024"], "q"),
                      ("b2 срез 2014–24", b2[b2["окно"] == "2014–2024"], "q"),
                      ("b3 панель", b3, "q")):
    print(f"  {name:16s} пар {len(f):4d}; p<0,05 {int((f.p < 0.05).sum()):3d} (ожид. {0.05 * len(f):4.1f}); "
          f"q<0,05 {int((f[qcol] < 0.05).sum()):2d}; [С] {int((f['статус'] == '[С]').sum())}")
mde = b4["mde"]
print(f"  Порог различимости MDE = {mde:.4f}")
for ch in ("A", "B", "B_hi", "C"):
    dd = b4["results"]["V3"][ch]["total"] - b4["results"]["V1"][ch]["total"]
    print(f"  канал {ch:5s}: V3−V1 = {dd:+.5f} ({abs(dd) / mde:.2f} MDE)")
mx = {v: max(abs(p) for p in d.values()) for v, d in b4["arable_pct"].items()}
print("  Макс. изменение пашни района, %:", mx, "— исторический размах области 3,5 %")
