# etl/mapper.py
"""
Нечёткий маппинг названий районов к KATO-кодам.
Установка: pip install rapidfuzz
"""
from rapidfuzz import process, fuzz
from etl.kato_map import KATO_MAP, normalize

_KEYS = list(KATO_MAP.keys())


def fuzzy_lookup_kato(region_name: str, threshold: int = 75):
    """
    Нечёткий поиск. Возвращает kato_code если score >= threshold.
    """
    norm = normalize(region_name)

    # Сначала точный поиск
    if norm in KATO_MAP:
        return KATO_MAP[norm][0]

    # Нечёткий поиск
    result = process.extractOne(norm, _KEYS, scorer=fuzz.token_sort_ratio)
    if result and result[1] >= threshold:
        return KATO_MAP[result[0]][0]

    return None


def batch_map_kato(series, threshold: int = 75):
    """
    Применить fuzzy_lookup_kato к pandas Series.
    Возвращает Series с kato_code (None если не найдено).
    """
    return series.apply(
        lambda x: fuzzy_lookup_kato(str(x), threshold) if x else None
    )
