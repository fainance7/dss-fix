"""b2. Районы, пространственный срез: структура земель района ~ уровень показателей УР.

Районной истории земельного баланса в DSS нет — по каждой категории один срез (2023 или 2026).
Поэтому это связь «где больше пашни — там выше/ниже показатель», а не эффект изменения.
13 сельских районов (Петропавловск исключён: 225 км², земельной структуры нет).
Показатели — среднее приведённых значений методики за 2022–2024 и за 2014–2024.
Контроль масштаба: частная корреляция при log(численности населения).
Статус [С]: q_BH < 0,05 и знак стабилен при исключении любого района.
Вывод: /wlb/b2_links.csv (наклоны — для верхней оценки в b4).
"""
import numpy as np
import pandas as pd

from common import LABELS, W, bh, district_series, load_panel, perm_p_corr, r_crit, residualize, ur_indicator_panel, ur_model

d = load_panel()
LR = "Земельные ресурсы"


def snap(name):
    s = district_series(d, name, LR)
    return s.iloc[:, -1]


total = snap("Общая площадь земель")
SH = pd.DataFrame({
    "Доля пашни": snap("Площадь пашни") / total,
    "Доля пастбищ": snap("Площадь пастбищ") / total,
    "Доля сенокосов": snap("Сенокосы") / total,
    "Доля залежи": snap("Залежь") / total,
    "Доля земель с/х назначения": snap("Площадь земель сельскохозяйственного назначения") / total,
    "Доля лесного фонда": snap("Площадь земель лесного фонда") / total,
    "Доля населённых пунктов": snap("Площадь земель населённых пунктов") / total,
    "Пашня / (пашня+пастбища)": snap("Площадь пашни") / (snap("Площадь пашни") + snap("Площадь пастбищ")),
}).drop(index="петропавл", errors="ignore")

model = ur_model()
up = ur_indicator_panel(model)
pop = pd.Series({k: np.mean([v for v in s if v]) for k, s in
                 ((__import__("common").key(t), model.population_thousands[t]) for t in model.territories)})


# Климатическая норма района (ГТК 2003–2025 по метеостанциям) — проверка географического градиента:
# если связь «структура земель → показатель» исчезает при её учёте, это различие юг/север, а не землепользование.
gtk = d[(d.level == 2) & (d.indicator == "Гидротермический коэффициент (ГТК)")].groupby("k").value.mean()

# Доля казахов в населении района (среднее за доступные годы 2014–2024) — проверка
# демографического состава как общей причины структуры угодий и рождаемости.
_kz = district_series(d, "Численность казахов")
_ru = district_series(d, "Численность русских")
_yrs = [y for y in _kz.columns if 2014 <= y <= 2024 and y in _ru.columns]
kaz = (_kz[_yrs] / (_kz[_yrs] + _ru[_yrs])).mean(axis=1)  # к сумме двух крупнейших групп — не зависит от единиц населения


def level(y0, y1):
    return up[up.year.between(y0, y1)].groupby(["k", "indicator"]).value.mean().unstack().rename(columns=LABELS)


rows = []
for window, Y in (("2022–2024", level(2022, 2024)), ("2014–2024", level(2014, 2024))):
    Y = Y.loc[SH.index]
    lp_all = np.log(pop.loc[SH.index].values)
    g_all = gtk.loc[SH.index].values.astype(float)
    kz_all = kaz.reindex(SH.index).values.astype(float)
    for xc in SH:
        for yc in Y:
            xr = SH[xc].values.astype(float)
            yv = Y[yc].values.astype(float)
            m = np.isfinite(xr) & np.isfinite(yv)
            if m.sum() < 10:
                continue
            xr, yv, lp, g, kz = xr[m], yv[m], lp_all[m], g_all[m], kz_all[m]
            x = residualize(xr, [lp])
            y = residualize(yv, [lp])
            r, p, n = perm_p_corr(x, y)
            # наклон без контроля — в натуральных единицах (Δпоказателя на +1 доли)
            slope = np.polyfit(xr, yv, 1)[0]
            slope_c = np.polyfit(x, y, 1)[0]
            loo = []
            for i in range(len(x)):
                k = np.arange(len(x)) != i
                loo.append(np.corrcoef(residualize(xr[k], [lp[k]]), residualize(yv[k], [lp[k]]))[0, 1])
            loo = np.array(loo)
            r_g, p_g, _ = perm_p_corr(residualize(xr, [lp, g]), residualize(yv, [lp, g]))
            rows.append({"окно": window, "земля": xc, "показатель": yc, "n": n, "r_частн": r, "p": p,
                         "r_климат": r_g, "p_климат": p_g,
                         **dict(zip(("r_состав", "p_состав"),
                                    perm_p_corr(residualize(xr, [lp, kz]), residualize(yv, [lp, kz]))[:2])),
                         "наклон": slope, "наклон_контроль": slope_c,
                         "loo_знак": bool(np.all(np.sign(loo) == np.sign(r))), "loo_min|r|": float(np.abs(loo).min())})

res = pd.DataFrame(rows)
res["q"] = np.nan
for w, idx in res.groupby("окно").groups.items():
    res.loc[idx, "q"] = bh(res.loc[idx, "p"].values)
res["статус"] = np.where((res.q < 0.05) & res.loo_знак, "[С]", "отклонена")
res.to_csv(f"{W}/b2_links.csv", index=False)

pd.set_option("display.width", 250)
print("Норма ГТК района ~ доля пашни в (пашня+пастбища): r =",
      round(float(np.corrcoef(gtk.loc[SH.index], SH["Пашня / (пашня+пастбища)"])[0, 1]), 2))
print("Доля казахов ~ пашня/(пашня+пастбища): r =",
      round(float(np.corrcoef(kaz.loc[SH.index], SH["Пашня / (пашня+пастбища)"])[0, 1]), 2), "; доля казахов по районам:",
      kaz.loc[SH.index].round(2).to_dict(), "; годы:", _yrs)
print("Структура земель районов, доли от общей площади:")
print(SH.round(3).to_string())
for w, f in res.groupby("окно"):
    print(f"\n=== Окно {w}: пар {len(f)}, n = 13; |r| для p<0,05: {r_crit(13, 0.05):.2f}, "
          f"с Бонферрони: {r_crit(13, 0.05 / len(f)):.2f}")
    print(f"  p<0,05: {(f.p < 0.05).sum()} (случайно ожидается {0.05 * len(f):.1f}); q<0,05: {(f.q < 0.05).sum()}; "
          f"[С]: {(f['статус'] == '[С]').sum()}")
    print(f"  при учёте нормы ГТК района p<0,05: {(f.p_климат < 0.05).sum()}; q<0,05: {(bh(f.p_климат.values) < 0.05).sum()}")
    print(f"  при учёте доли казахов p<0,05: {(f.p_состав < 0.05).sum()}; q<0,05: {(bh(f.p_состав.values) < 0.05).sum()}")
    print(f.sort_values("p").head(10)[["земля", "показатель", "r_частн", "p", "q", "loo_знак", "r_климат",
                                       "p_климат", "r_состав", "p_состав", "статус"]]
          .round(3).to_string(index=False))
