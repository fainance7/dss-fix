import pandas as pd
from datetime import datetime

REQUIRED_COLS = [
    "indicator_name", "direction_code", "unit_name",
    "kato_code", "period_type", "period_start", "period_end", "value"
]

UNIT_NORMALIZATION = {
    "чел.": "человек",
    "ед.":  "единиц",
}


def normalize_units(df: pd.DataFrame) -> pd.DataFrame:
    df["unit_name"] = df["unit_name"].replace(UNIT_NORMALIZATION)
    return df


def validate_df(df: pd.DataFrame) -> None:
    errors = []

    # 1. Обязательные колонки
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        errors.append(f"Отсутствуют колонки: {missing}")

    # 2. value — принудительно конвертируем в число
    if "value" in df.columns:
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        null_values = df["value"].isna().sum()
        if null_values:
            errors.append(f"{null_values} строк где value не удалось конвертировать в число")

    # 3. Логика дат
    if "period_start" in df.columns and "period_end" in df.columns:
        try:
            starts = pd.to_datetime(df["period_start"])
            ends   = pd.to_datetime(df["period_end"])
            bad_dates = (ends < starts).sum()
            if bad_dates:
                errors.append(f"{bad_dates} строк где period_end < period_start")
        except Exception as e:
            errors.append(f"Ошибка парсинга дат: {e}")

    # 4. Пустые kato_code
    if "kato_code" in df.columns:
        null_kato = df["kato_code"].isna().sum()
        if null_kato:
            errors.append(f"{null_kato} строк с пустым kato_code")

    if errors:
        raise ValueError("Валидация не прошла:\n" + "\n".join(errors))

    print(f"Валидация OK: {len(df)} строк, {df['indicator_name'].nunique()} показателей")
