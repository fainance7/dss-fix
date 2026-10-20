# etl/cleaner.py
"""
Очистка и трансформация исторических данных СКО.
Логика взята из Clean_data.py и add_kato.py.
"""
import pandas as pd
import warnings
from etl.kato_map import KATO_MAP, INDICATOR_MAP, normalize, lookup_kato
from etl.mapper import batch_map_kato

POPULATION_JUMP_KATO = 591000000
POPULATION_JUMP_YEAR = 2017

UNIT_NORMALIZATION = {
    "чел.": "человек",
    "ед.":  "единиц",
}


def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Убрать пустые Unnamed колонки."""
    unnamed = [c for c in df.columns if str(c).startswith("Unnamed")]
    for col in unnamed:
        if df[col].dropna().empty:
            df = df.drop(columns=[col])
            print(f"[clean_columns] Удалена пустая колонка '{col}'")
        else:
            df = df.rename(columns={col: "transport"})
            print(f"[clean_columns] '{col}' → 'transport'")
    return df


def normalize_units(df: pd.DataFrame) -> pd.DataFrame:
    """Нормализовать единицы измерения."""
    df["unit_name"] = df["unit_name"].replace(UNIT_NORMALIZATION)
    return df


def remove_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Удалить дубли по ключевым полям."""
    key_cols = ["kato_code", "indicator_name", "period_start", "period_end"]
    before   = len(df)
    df       = df.drop_duplicates(subset=key_cols).reset_index(drop=True)
    removed  = before - len(df)
    if removed:
        print(f"[remove_duplicates] Удалено {removed} дублей")
    return df


def fix_population_jump(df: pd.DataFrame) -> pd.DataFrame:
    """
    Петропавловск (kato 591000000): скачок x250 между 2016 и 2017
    из-за смены единиц (тысячи → штуки).
    Приводим 2000-2016 к масштабу 2017+.
    """
    mask_pop  = df["indicator_name"].str.contains(
        "Численность населения", case=False, na=False)
    mask_kato = df["kato_code"] == POPULATION_JUMP_KATO
    sub       = df.loc[mask_pop & mask_kato].copy()

    if sub.empty:
        return df

    sub["year"] = pd.to_datetime(sub["period_start"]).dt.year

    mean_before = sub.loc[sub["year"].between(2014, 2016), "value"].mean()
    mean_after  = sub.loc[sub["year"].between(2017, 2019), "value"].mean()

    if mean_before == 0 or pd.isna(mean_before):
        warnings.warn("fix_population_jump: не удалось вычислить коэффициент")
        return df

    k       = mean_after / mean_before
    idx_old = sub.loc[sub["year"] < POPULATION_JUMP_YEAR].index
    df.loc[idx_old, "value"] = (df.loc[idx_old, "value"] * k).round(0)

    print(f"[fix_population_jump] k={k:.4f}, "
          f"скорректировано {len(idx_old)} строк (до {POPULATION_JUMP_YEAR})")
    return df


def unpivot_wide_table(df: pd.DataFrame,
                        indicator_col: str,
                        region_col: str,
                        year_cols: list) -> pd.DataFrame:
    """
    Перевод широкой таблицы (года в колонках) в длинный формат DSS.

    indicator_col — название колонки с именем показателя
    region_col    — название колонки с районом
    year_cols     — список колонок-годов, например [2015, 2016, 2017]
    """
    rows = []
    for _, row in df.iterrows():
        indicator_raw = str(row.get(indicator_col, ""))
        region_raw    = str(row.get(region_col, ""))
        kato_code     = lookup_kato(region_raw)

        if not kato_code:
            kato_code = batch_map_kato(
                pd.Series([region_raw])
            ).iloc[0]

        if not kato_code:
            print(f"  [ПРОПУСК] Не найден KATO для: '{region_raw}'")
            continue

        # Маппинг показателя
        ind_info = INDICATOR_MAP.get(indicator_raw)
        if ind_info:
            indicator_name, direction_code, unit_name = ind_info
        else:
            indicator_name = indicator_raw
            direction_code = "other"
            unit_name      = ""

        for year in year_cols:
            val = row.get(year)
            if pd.isna(val):
                continue
            rows.append({
                "kato_code":      kato_code,
                "indicator_name": indicator_name,
                "direction_code": direction_code,
                "unit_name":      unit_name,
                "period_type":    "year",
                "period_start":   f"{int(year)}-01-01",
                "period_end":     f"{int(year)}-12-31",
                "value":          val,
            })

    return pd.DataFrame(rows)


def process_historical_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Полный цикл очистки исторического DataFrame.
    Вызывается из DAG-а для каждой таблицы из reports_db.
    """
    df = clean_columns(df)
    df = normalize_units(df)
    df = remove_duplicates(df)
    df = fix_population_jump(df)

    df["period_start"] = pd.to_datetime(
        df["period_start"]).dt.strftime("%Y-%m-%d")
    df["period_end"]   = pd.to_datetime(
        df["period_end"]).dt.strftime("%Y-%m-%d")

    return df
