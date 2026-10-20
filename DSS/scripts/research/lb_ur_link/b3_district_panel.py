"""b3. Районная панель: изменение использования земель ~ изменение показателей УР.

Единственный дизайн, где видно изменение во времени по районам. Площадей угодий по годам
нет, поэтому прокси использования земель:
  ln(посевы зерновых + масличных) — использование пашни (годы 2014, 2015, 2018–2024);
  ln(поголовье КРС + овец и коз в условных головах 1 : 0,1) — нагрузка на пастбища.
Модель: двусторонние фиксированные эффекты (район и год). p — перестановка x по годам внутри
района (2000 раз). Статус [С]: q_BH < 0,05, знак стабилен в jackknife по районам и одинаков
в подокнах 2014–2019 и 2020–2024, 90%-й кластерный бутстрэп по районам не содержит ноль. Наклоны и кластерный бутстрэп по районам — в /wlb/b3_links.csv.
"""
import numpy as np
import pandas as pd

from common import LABELS, SEED, W, bh, district_series, fe_perm_p, fe_projector, fe_slope, load_panel, ur_indicator_panel, ur_model

d = load_panel()
AG = "Сельское хозяйство"
grain = district_series(d, "Посевная площадь зерновых культур", AG)
oil = district_series(d, "Посевная площадь масличных культур", AG)
krs = district_series(d, "Поголовье КРС", AG)
sheep = district_series(d, "Поголовье овец и коз", AG)

sown = (grain + oil).stack().rename("sown")
herd = (krs + 0.1 * sheep).stack().rename("herd")
X = pd.concat([sown, herd], axis=1)
X.index.names = ["k", "year"]
X = X.reset_index()
X = X[X.year.between(2014, 2024) & (X.k != "петропавл")]

# Санити по единицам: скачки >2 раз год к году внутри района — признак смешения единиц
for c in ("sown", "herd"):
    w = X.pivot(index="k", columns="year", values=c)
    jumps = (w / w.shift(axis=1)).stack()
    bad = jumps[(jumps > 2) | (jumps < 0.5)]
    print(f"{c}: подозрительных скачков ×2 — {len(bad)}", bad.round(2).to_dict() if len(bad) else "")

X["ln_sown"] = np.log(X.sown)
X["ln_herd"] = np.log(X.herd)

model = ur_model()
up = ur_indicator_panel(model)
Y = up.pivot_table(index=["k", "year"], columns="indicator", values="value").reset_index()
P = X.merge(Y, on=["k", "year"])
YC = [c for c in Y.columns if c not in ("k", "year")]
print("Наблюдений в панели:", P.dropna(subset=["ln_sown"]).shape[0], "районов:", P.k.nunique(),
      "годы:", sorted(P.dropna(subset=["ln_sown"]).year.unique()))

rng = np.random.default_rng(SEED)
rows = []
for xc in ("ln_sown", "ln_herd"):
    for yc in YC:
        df = P[["k", "year", xc, yc]].dropna().rename(columns={xc: "x", yc: "y"}).reset_index(drop=True)
        if len(df) < 40:
            continue
        M = fe_projector(df)
        b, xd, yd = fe_slope(df, "x", "y", M)
        r = float(np.corrcoef(xd, yd)[0, 1])
        p = fe_perm_p(df, "x", "y", n=2000, M=M)
        jk = [fe_slope(df[df.k != g].reset_index(drop=True), "x", "y")[0] for g in df.k.unique()]
        early = fe_slope(df[df.year <= 2019].reset_index(drop=True), "x", "y")[0]
        late = fe_slope(df[df.year >= 2020].reset_index(drop=True), "x", "y")[0]
        boots = []
        ks = df.k.unique()
        for _ in range(200):
            pick = rng.choice(ks, len(ks), replace=True)
            bd = pd.concat([df[df.k == g].assign(k=f"{g}_{i}") for i, g in enumerate(pick)], ignore_index=True)
            boots.append(fe_slope(bd, "x", "y")[0])
        rows.append({"x": xc, "показатель": LABELS.get(yc, yc), "код": yc, "n": len(df), "наклон": b, "r_внутри": r,
                     "p": p, "jk_знак": bool(np.all(np.sign(jk) == np.sign(b))),
                     "подокна_знак": bool(np.sign(early) == np.sign(late) == np.sign(b)),
                     "наклон_2014_19": early, "наклон_2020_24": late,
                     "boot_lo": float(np.percentile(boots, 5)), "boot_hi": float(np.percentile(boots, 95))})

res = pd.DataFrame(rows)
res["q"] = bh(res.p.values)
# Перестановка внутри района не учитывает собственных трендов районов (p занижено), поэтому
# дополнительно требуется, чтобы 90%-й кластерный бутстрэп по районам не накрывал ноль.
res["boot_без_нуля"] = (res.boot_lo > 0) | (res.boot_hi < 0)
res["статус"] = np.where((res.q < 0.05) & res.jk_знак & res.подокна_знак & res.boot_без_нуля, "[С]", "отклонена")
res.to_csv(f"{W}/b3_links.csv", index=False)
pd.set_option("display.width", 250)
print(f"\nПар {len(res)}; p<0,05: {(res.p < 0.05).sum()} (случайно {0.05 * len(res):.1f}); q<0,05: {(res.q < 0.05).sum()}; "
      f"[С]: {(res['статус'] == '[С]').sum()}")
print(res.sort_values("p").head(12)[["x", "показатель", "n", "наклон", "r_внутри", "p", "q", "jk_знак", "подокна_знак",
                                     "boot_lo", "boot_hi", "статус"]].round(4).to_string(index=False))
