"""b1. Область, время: изменения земельного баланса ~ изменения показателей УР.

Две семьи гипотез, каждая со своей поправкой Бенджамини–Хохберга:
  L (длинная, 2004–2023): 8 земельных рядов × 8 показателей с длинной историей × лаг 0/1;
  S (короткая, 2014–2023): 8 земельных рядов × 14 индикаторов методики + 4 индекса
    (областное значение = невзвешенное среднее 14 территорий, как в методике).
Все связи — в первых разностях (уровни трендовых рядов дают ложную корреляцию).
Статус [С]: q_BH < 0,05 и знак стабилен в jackknife по годам и MASE < 1 при прогнозе с
нарастающим окном. Вывод: /wlb/b1_links.csv.
"""
import sys

sys.path.insert(0, "/app")
import numpy as np
import pandas as pd

from app.services.landscape_impact.calibration import _jackknife_slopes, _rolling_origin_mase
from common import LABELS, W, bh, load_panel, oblast_series, perm_p_corr, r_crit, ur_indicator_panel, ur_model

d = load_panel()
LAND = {
    "Пашня": ("Площадь пашни", None),
    "Пастбища": ("Площадь пастбищ", None),
    "Сенокосы": ("Сенокосы", None),
    "Залежь": ("Площадь залежи", None),
    "Земли с/х назначения": ("Площадь земель сельскохозяйственного назначения", None),
    "Земли запаса": ("Площадь земель запаса", None),
    "С/х угодья": ("Площадь сельскохозяйственных угодий", None),
    "Посевная площадь": ("Общая посевная площадь", "Сельское хозяйство"),
}
X = pd.DataFrame({k: oblast_series(d, n, dr) for k, (n, dr) in LAND.items()}).loc[2004:2023]

pop = oblast_series(d, "Среднегодовая численность населения")
LONG = pd.DataFrame({
    "Рождаемость, ‰": oblast_series(d, "Общий коэффициент рождаемости"),
    "Смертность, ‰": oblast_series(d, "Общий коэффициент смертности"),
    "Сальдо миграции, ‰": oblast_series(d, "Сальдо (прирост) миграции") / pop * 1000,
    "Безработица, %": oblast_series(d, "Уровень безработицы", "Трудовые ресурсы"),
    "Преступность на 10 тыс.": oblast_series(d, "Число зарегистрированных преступлений") / pop * 1e4,
    "ЗП / прожиточный минимум": oblast_series(d, "Среднемесячная номинальная заработная плата", "Трудовые ресурсы")
    / oblast_series(d, "Величина прожиточного минимума", "Социально-демографическое"),
    "ВРП СХ на душу (лог)": np.log(oblast_series(d, "Валовой региональный продукт СХ") / pop),
    "Доля занятых в СХ, %": 100 * oblast_series(
        d, "Занятость населения по группам видов экономической деятельности — Сельское, лесное и рыбное хозяйство")
    / oblast_series(d, "Занятое население", "Трудовые ресурсы"),
}).loc[2004:2023]

model = ur_model()
up = ur_indicator_panel(model)
SHORT = up.groupby(["year", "indicator"]).value.mean().unstack()
SHORT = SHORT.rename(columns=LABELS).loc[2014:2023]

print("Покрытие земельных рядов (лет):", X.notna().sum().to_dict())
print("Покрытие длинных показателей (лет):", LONG.notna().sum().to_dict())
print("Изменчивость земель 2004–2023, размах к среднему, %:",
      {k: round(100 * (v.max() - v.min()) / v.mean(), 1) for k, v in X.items()})
print("То же 2014–2023, %:", {k: round(100 * (v.max() - v.min()) / v.mean(), 1) for k, v in X.loc[2014:].items()})

dX = np.log(X).diff()


def family(Y, name, lags):
    rows = []
    dY = Y.diff()
    for lag in lags:
        for xc in dX:
            for yc in dY:
                xs = dX[xc].shift(lag)
                pair = pd.concat([xs, dY[yc]], axis=1).dropna()
                if len(pair) < 6:
                    continue
                x, y = pair.iloc[:, 0].values, pair.iloc[:, 1].values
                r, p, n = perm_p_corr(x, y)
                jk = _jackknife_slopes(x, y)
                slope = np.polyfit(x, y, 1)[0]
                mase, _ = _rolling_origin_mase(x, y)
                rows.append({"семья": name, "лаг": lag, "земля": xc, "показатель": yc, "n": n, "r": r, "p": p,
                             "наклон": slope, "jk_знак": bool(np.all(np.sign(jk) == np.sign(slope))), "MASE": mase})
    out = pd.DataFrame(rows)
    out["q"] = bh(out.p.values)
    out["статус"] = np.where((out.q < 0.05) & out.jk_знак & (out.MASE < 1), "[С]", "отклонена")
    return out


L = family(LONG, "L 2004–2023", [0, 1])
S = family(SHORT, "S 2014–2023", [0])
res = pd.concat([L, S])
res.to_csv(f"{W}/b1_links.csv", index=False)

pd.set_option("display.width", 250)
for name, f in res.groupby("семья"):
    n = int(f.n.median())
    print(f"\n=== {name}: пар {len(f)}, медиана n = {n}")
    print(f"  |r| для p<0,05 без поправки: {r_crit(n, 0.05):.2f}; с поправкой на {len(f)} проверок (Бонферрони): "
          f"{r_crit(n, 0.05 / len(f)):.2f}")
    print(f"  p<0,05 без поправки: {(f.p < 0.05).sum()} (ожидаемо случайно {0.05 * len(f):.1f}); "
          f"q<0,05: {(f.q < 0.05).sum()}; статус [С]: {(f['статус'] == '[С]').sum()}")
    print(f.sort_values("p").head(8)[["лаг", "земля", "показатель", "n", "r", "p", "q", "jk_знак", "MASE", "статус"]]
          .round(3).to_string(index=False))

# Устойчивость лучших кандидатов длинной семьи: держится ли связь в обоих подокнах
# (2005–2013 — период распашки, 2014–2023 — стабильная пашня) и после удаления линейного
# тренда приращений (общий фактор «бум 2000-х»).
print("\n=== Подокна для кандидатов L с q < 0,15")
dLONG = LONG.diff()
for _, row in L[L.q < 0.15].sort_values("p").iterrows():
    xs = dX[row["земля"]].shift(int(row["лаг"]))
    pair = pd.concat([xs, dLONG[row["показатель"]]], axis=1).dropna()
    a = pair.loc[:2013]
    b = pair.loc[2014:]
    t = np.arange(len(pair))
    dt = pair.apply(lambda s: s - np.polyval(np.polyfit(t, s.values, 1), t))
    ra, pa, na = perm_p_corr(a.iloc[:, 0], a.iloc[:, 1])
    rb, pb, nb = perm_p_corr(b.iloc[:, 0], b.iloc[:, 1])
    rd, pd_, _ = perm_p_corr(dt.iloc[:, 0], dt.iloc[:, 1])
    print(f"  {row['земля']:22s} → {row['показатель']:24s} 2005–13: r={ra:+.2f} (n={na}, p={pa:.3f}); "
          f"2014–23: r={rb:+.2f} (n={nb}, p={pb:.3f}); без тренда: r={rd:+.2f} p={pd_:.3f}")
