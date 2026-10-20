# etl/weather_calculator.py
import pandas as pd
import numpy as np


def parse_daily(raw_json: dict) -> pd.DataFrame:
    daily = raw_json.get("daily", {})
    df = pd.DataFrame({
        "date":   pd.to_datetime(daily["time"]),
        "tmax":   daily["temperature_2m_max"],
        "tmin":   daily["temperature_2m_min"],
        "precip": daily["precipitation_sum"],
    })
    df["tmean"] = (df["tmax"] + df["tmin"]) / 2
    return df


def sum_active_temperatures(df: pd.DataFrame, threshold: float = 10.0) -> float:
    return round(df.loc[df["tmean"] > threshold, "tmean"].sum(), 1)


def gtc_selyaninov(df: pd.DataFrame, start_month: int = 5, end_month: int = 9) -> float:
    veg        = df[df["date"].dt.month.between(start_month, end_month)]
    sum_precip = veg["precip"].sum()
    sum_temp   = veg.loc[veg["tmean"] > 10, "tmean"].sum()
    if sum_temp == 0:
        return np.nan
    return round((sum_precip * 10) / sum_temp, 2)


def frost_transition_date(df: pd.DataFrame, threshold: float = 0.0, spring: bool = True):
    if spring:
        sub = df[(df["date"].dt.month.between(3, 6)) & (df["tmean"] > threshold)]
    else:
        sub = df[(df["date"].dt.month.between(8, 11)) & (df["tmean"] < threshold)]
    if sub.empty:
        return None
    return sub["date"].iloc[0].strftime("%Y-%m-%d")


def vegetation_period_days(df: pd.DataFrame, threshold: float = 10.0) -> int:
    return int((df["tmean"] > threshold).sum())


def calculate_all(raw_json: dict, kato_code: int, year: int) -> list:
    df = parse_daily(raw_json)

    base = {
        "kato_code":      kato_code,
        "direction_code": "climate",
        "period_type":    "year",
        "period_start":   f"{year}-01-01",
        "period_end":     f"{year}-12-31",
    }

    results = []

    results.append({**base,
        "indicator_name": "Сумма активных температур >10°C (Σt)",
        "unit_name": "°C",
        "value": sum_active_temperatures(df)
    })

    results.append({**base,
        "indicator_name": "Гидротермический коэффициент (ГТК)",
        # Сверено с живым /data-intake/template с портала DSS: единица
        # называется "безразм.", а не "коэфф." (и не "безразмерная величина" —
        # это было ошибочное предположение из dss_indicators.json, который,
        # похоже, не тот справочник, с которым сверяет валидатор портала).
        "unit_name": "безразм.",
        "value": gtc_selyaninov(df)
    })

    results.append({**base,
        "indicator_name": "Продолжительность вегетационного периода",
        # Живой шаблон DSS: "сутки", не "дней".
        "unit_name": "сутки",
        "value": vegetation_period_days(df)
    })

    spring = frost_transition_date(df, spring=True)
    if spring:
        results.append({**base,
            "indicator_name": "Дата перехода температуры через 10°C весной",
            "unit_name": "дата",
            "value": spring
        })

    return results
