# etl/universal_detector.py
"""
Универсальный детектор структуры "широких" Excel-таблиц для DSS.
 
Назначение: заменить ручные конфиги (region_col, years_row, data_start_row)
из convert_to_dss.py автоматическим распознаванием:
  1. Где в листе находятся "блоки" показателей (один лист может содержать
     несколько показателей друг под другом, разделённых строкой с годами).
  2. Где строка с годами и где начинаются/заканчиваются данные.
  3. Какому эталонному показателю DSS соответствует текст блока
     (fuzzy-match по справочнику indicator_catalog.csv).
  4. Какому KATO-коду соответствует название района в каждой строке
     (fuzzy-match по etl/kato_map.py, с порогом).
 
Известные ограничения (детектор НЕ умеет и не пытается):
  - объединённые (merged) ячейки Excel — pandas разворачивает их в NaN,
    что и так покрывается обычной идущей вперёд логикой (года не дублируются).
  - таблицы где регион в строке заголовка, а года — в столбце (транспонированные).
  - файлы-справочники без числовых данных (детектируются и пропускаются).
  - множественные шапки (2+ строки заголовков) — берётся только последняя
    строка перед данными как "года".
"""
from __future__ import annotations
 
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
 
import pandas as pd
 
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "etl"))
from kato_map import KATO_MAP as _KATO_MAP_CANON, normalize as kato_normalize  # noqa: E402
 
try:
    from rapidfuzz import process, fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
 
 
YEAR_MIN, YEAR_MAX = 1990, 2035
MIN_YEAR_HITS = 2          # минимум чисел-годов в строке, чтобы считать её "строкой годов"
# Было 3. Таблицы с двумя годами ("2023 | 2024" — сравнение прошлого и
# текущего) встречаются часто, и при пороге 3 строка годов не находилась
# вовсе: данные сохранялись категориальным путём как снимок на текущий год,
# то есть цифры за 2023 и 2024 попадали в базу под 2026-м. Ошибка тихая —
# значения верные, период неверный.
#
# Порог 2 сам по себе слишком слабый (строка данных вида "2020 | 2021" из
# двух чисел прошла бы), поэтому для ровно двух попаданий включается
# дополнительная проверка _row_is_years: годы должны быть соседними и
# занимать почти всю числовую часть строки.
KATO_FUZZY_THRESHOLD = 82  # порог схожести для нечёткого матчинга районов
INDICATOR_FUZZY_THRESHOLD = 78
# Минимальная схожесть РОДОВОЙ части составного имени. Замеры на реальном
# каталоге: "Занятое население по группам..." vs "Занятость населения по
# группам..." = 95 (одно и то же, должно совпасть), тогда как "Урожайность
# с/х культур" vs "Валовой сбор с/х культур" = 69 и "Занятое население" vs
# "Среднемесячная заработная плата" = 35 (разные показатели, должны быть
# отклонены). Порог 80 разделяет эти случаи с запасом.
COMPOSITE_HEAD_MIN = 85
 
# Служебные подписи колонки с районами — встречаются в той же строке, что и
# года, но это НЕ название показателя/подкатегории, а просто заголовок
# первого столбца (как "Наименование районов", "Регион", "Район" и т.п.).
# Если текст в этой ячейке совпадает с одной из этих фраз (после нормализации),
# он игнорируется как кандидат на title, и берётся родовой заголовок сверху.
_COLUMN_LABEL_STOPLIST = {
    "наименование районов",
    "наименование района",
    "наименование региона",
    "район",
    "регион",
    "район/город",
    "наименование",
    "административный район",
    "административно-территориальная единица",
}
 
 
def _is_generic_column_label(text: str) -> bool:
    norm = " ".join(str(text).lower().strip().split())
    return norm in _COLUMN_LABEL_STOPLIST
 
 
# ---------------------------------------------------------------------------
# Низкоуровневые хелперы распознавания
# ---------------------------------------------------------------------------
 
def _is_year(value) -> bool:
    try:
        f = float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        return False
    if f != f or f in (float("inf"), float("-inf")):  # NaN/inf -> не год
        return False
    if f != int(f):          # дробное (напр. 2027,1 тыс.га) -> это значение, не год
        return False
    return YEAR_MIN <= int(f) <= YEAR_MAX
 
 
def _year_hits(row: pd.Series) -> int:
    return sum(1 for v in row if _is_year(v))
 
 
def _row_is_years(row: pd.Series) -> bool:
    hits = _year_hits(row)
    if hits >= 3:
        return True
    if hits < MIN_YEAR_HITS:
        return False

    # Ровно два кандидата — проверяем строже, чтобы не принять строку данных
    # за шапку. Требования: (а) почти все числа строки являются годами,
    # (б) годы идут по возрастанию и различаются не более чем на 10 лет.
    years, numbers = [], 0
    for v in row:
        if pd.isna(v):
            continue
        s = str(v).strip()
        if not s:
            continue
        if _is_year(v):
            years.append(int(float(s.replace(",", "."))))
            numbers += 1
        elif _parse_number(v) is not None:
            numbers += 1

    if numbers == 0 or len(years) / numbers < 0.8:
        return False
    return years == sorted(years) and 0 < years[-1] - years[0] <= 10
 
 
def _looks_like_region(value) -> bool:
    """Эвристика: строка-кандидат на название района/области (не показатель)."""
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none"):
        return False
    # названия районов почти всегда короче 60 символов
    return len(s) <= 60
 
 
# ---------------------------------------------------------------------------
# Поиск КАТО (точный + нечёткий, с эталонным словарём как единым источником)
# ---------------------------------------------------------------------------
 
_KATO_FLAT = {k: v[0] for k, v in _KATO_MAP_CANON.items()}

# Автосправочник из kato_cache (800 кодов КАТО, уровни 1-5: область, районы,
# сельские округа, сёла). Генерируется gen_kato.py. Ручные алиасы из
# kato_map.py имеют приоритет — они содержат нетривиальные случаи ("ско",
# "барлығы"), которых нет в официальных названиях.
try:
    from kato_map_auto import AUTO_KATO_MAP as _AUTO, FUZZY_LEVELS as _FUZZY
    for _k, _v in _AUTO.items():
        _KATO_FLAT.setdefault(_k, _v[0])
except ImportError:
    _AUTO, _FUZZY = {}, []

# Fuzzy-поиск ТОЛЬКО по уровням 1-2 (область и районы). Сёла и с.о. ищутся
# лишь точным совпадением: названия вроде "с.Целинное" встречаются в 4 разных
# районах, и нечёткий матч записал бы данные не туда. Лучше пропустить строку
# с явным логом, чем тихо исказить привязку к территории.
_KATO_KEYS = [k for k, v in _KATO_MAP_CANON.items()
              if v[0] != 590000000 and len(k) >= 5]
_KATO_KEYS += [k for k in _FUZZY if k in _KATO_FLAT and k not in _KATO_MAP_CANON]
 
 
# Ячейки вида "М. Жумабаева и Кызылжарский районы" перечисляют ДВА района в
# одной строке с одним общим значением. Fuzzy-поиск раньше отдавал код первого
# попавшегося (WRatio находит подстроку "жумабаева" и возвращает 593600000), и
# площадь 134100 га целиком приписывалась одному району из двух — тихое
# искажение, которое в отчёте выглядит как обычные данные.
#
# Разделить значение между районами нельзя: пропорция неизвестна. Поэтому
# такие строки пропускаются и попадают в лог skipped_regions, где их видно.
_MULTI_REGION_CONJ = re.compile(r"\bи\b|\bжәне\b", re.I)
_MULTI_REGION_PLURAL = re.compile(r"районы\b|районов\b|районах\b|аудандар\w*\b", re.I)


def _is_ambiguous_region(text) -> bool:
    """True, если в ячейке перечислено несколько районов сразу."""
    if text is None:
        return False
    s = " ".join(str(text).split())
    if not s:
        return False
    return bool(_MULTI_REGION_CONJ.search(s) and _MULTI_REGION_PLURAL.search(s))


def lookup_kato(region_name: str, threshold: int = KATO_FUZZY_THRESHOLD):
    """Точный, затем нечёткий поиск KATO-кода по названию района."""
    if _is_ambiguous_region(region_name):
        return None
    norm = kato_normalize(region_name)
    if not norm:
        return None
    if norm in _KATO_FLAT:
        return _KATO_FLAT[norm]
 
    if not _HAS_RAPIDFUZZ:
        return None
 
    result = process.extractOne(norm, _KATO_KEYS, scorer=fuzz.WRatio)
    if result and result[1] >= threshold:
        return _KATO_FLAT[result[0]]
    return None
 
# ---------------------------------------------------------------------------
# Справочник показателей DSS (для авто-сопоставления indicator -> direction/unit)
# ---------------------------------------------------------------------------
 
@dataclass
class IndicatorCatalog:
    """
    Справочник эталонных показателей DSS, используемый для нечёткого
    сопоставления текста из исходных файлов с каноническим indicator_name.
 
    Две части:
      - entries: канонические показатели DSS (из каталога indicator_name).
      - aliases: альтернативные/разговорные названия, встречающиеся в
        конкретных исходных файлах, которые должны резолвиться в один
        из канонических показателей (а не создавать новый indicator_name).
 
    Ожидаемый CSV для entries: indicator_name,direction_code,unit_name
    Ожидаемый CSV для aliases: alias_name,canonical_name
    """
    entries: dict = field(default_factory=dict)   # normalized_name -> (canon_name, direction, unit)
    _keys: list = field(default_factory=list)
    alias_map: dict = field(default_factory=dict)  # normalized_alias -> normalized_canon
    # direction_code -> {normalized_name: (canon_name, direction, unit)}.
    # В каталоге DSS есть ОДНОИМЁННЫЕ показатели в разных направлениях с
    # РАЗНЫМИ единицами (напр. "Валовая продукция сельского хозяйства":
    # agriculture/млн.тенге и economy/млрд.тенге). Без разделения по
    # направлению одна версия затирала бы другую.
    by_direction: dict = field(default_factory=dict)
    # направление, выбранное пользователем на форме загрузки
    preferred_direction: str | None = None
 
    @classmethod
    def from_csv(cls, path: str | Path, aliases_path: str | Path | None = None) -> "IndicatorCatalog":
        df = pd.read_csv(path)
        cat = cls()
        for _, row in df.iterrows():
            norm = cls._normalize(row["indicator_name"])
            rec = (row["indicator_name"], row["direction_code"], row["unit_name"])
            cat.entries.setdefault(norm, rec)
            cat.by_direction.setdefault(row["direction_code"], {})[norm] = rec
 
        if aliases_path and Path(aliases_path).exists():
            adf = pd.read_csv(aliases_path)
            for _, row in adf.iterrows():
                alias_norm = cls._normalize(row["alias_name"])
                canon_norm = cls._normalize(row["canonical_name"])
                if canon_norm in cat.entries:
                    cat.alias_map[alias_norm] = canon_norm
                # если канона ещё нет в entries — алиас молча игнорируется;
                # это сознательное поведение (лучше unmapped, чем сослаться
                # на показатель которого нет в справочнике DSS)
 
        cat._keys = list(cat.entries.keys())
        return cat
 
    # Мусор, который источники добавляют вокруг сути показателя. Без очистки
    # "Таблица 8 - Поголовье скота и птицы в Северо-Казахстанской области за
    # 2004-2024 гг., голов — КРС" даёт 43% схожести с каталожным "Поголовье
    # КРС" при пороге 78 — то есть не матчится никогда, хотя это один и тот же
    # показатель. Чистка применяется ТОЛЬКО при сравнении; исходное имя
    # сохраняется в записи как есть, чтобы не терять контекст источника.
    _RE_TBL = re.compile(r"^\s*(таблиц[аы]|табл\.?)\s*\d+\s*(\(продолжение\))?\s*[-—–.:]*\s*", re.I)
    _RE_INCL = re.compile(r"^\s*(в\s+том\s+числе|из\s+них|в\s*т\.?\s*ч\.?)\s*[-—–:]*\s*", re.I)
    _RE_REGION = re.compile(r"\s*(в|по)?\s*(северо-?казахстанск\w*\s*(области|область)|ско)\b", re.I)
    _RE_YEARS = re.compile(r"\s*(за|в\s+среднем\s+за)?\s*(19|20)\d\d\s*[-—–]\s*(19|20)?\d\d\s*(гг?\.?)?", re.I)
    _RE_UNIT = re.compile(
        r",\s*(в\s+)?(тыс\.?|млн\.?|млрд\.?)?\s*"
        r"(га|гектар\w*|тонн\w*|кг|шт\.?|штук|ц/га|голов|гол\.?|человек|чел\.?|"
        r"тенге|тг|%|ед\.?|единиц|м3|м²|тыс)\.?\s*$", re.I)

    @classmethod
    def _strip_noise(cls, part: str) -> str:
        s = cls._RE_TBL.sub("", part.strip())
        s = cls._RE_INCL.sub("", s)
        s = cls._RE_YEARS.sub("", s)
        s = cls._RE_REGION.sub("", s)
        for _ in range(2):
            s = cls._RE_UNIT.sub("", s)
        return " ".join(s.replace("()", " ").split()).strip(" ,-*.")

    @classmethod
    def _normalize(cls, text: str) -> str:
        # ё/е: в каталоге DSS "Ёмкость номерного фонда", в источниках почти
        # всегда "Емкость" — без этой замены показатель не находится вообще.
        raw = " ".join(str(text).lower().replace("ё", "е").strip().split())
        parts = [cls._strip_noise(x) for x in raw.split("—")]
        parts = [x for x in parts if x]
        return " — ".join(parts) if parts else raw
 
    @staticmethod
    def _split_composite(name: str) -> tuple[str, str] | None:
        """
        Если имя — составное ("Родовая часть — Специфическая часть",
        как строят block_to_rows/match_block и document_adapters), возвращает
        (родовая_часть, специфическая_часть). Иначе None.
        Разделитель — long dash "—", который весь код проекта использует
        именно для составных имён (короткий дефис "-" внутри обычных слов
        вроде "2004-2023" не разделяется).
        """
        if " — " not in name:
            return None
        head, _, tail = name.rpartition(" — ")
        return head.strip(), tail.strip()
 
    def _fuzzy_best(self, norm: str, keys: list[str], threshold: int):
        """
        Нечёткий поиск с защитой от длинных общих префиксов.
 
        Проблема, которую это решает: для составных имён вида "Длинный
        родовой заголовок — Конкретный показатель" обычный
        token_sort_ratio(a, b) даёт высокую оценку (90%+) для ЛЮБОГО b с
        тем же родовым заголовком, даже если специфическая часть после
        тире совершенно другая — потому что 90% текста строки совпадает.
        Из-за этого "...— Сенокосы" может неверно сматчиться с алиасом
        "...— Пашня" просто потому, что оба длинные и оба про землю.
 
        Решение: если ОБА сравниваемых имени — составные (есть " — "),
        требуем, чтобы специфическая часть (после тире) сама была похожа
        не ниже порога — родовая часть может совпадать сколько угодно,
        это не даёт права считать специфические части эквивалентными.
        """
        composite_query = self._split_composite(norm)
 
        if composite_query is None:
            result = process.extractOne(norm, keys, scorer=fuzz.token_sort_ratio)
            if result and result[1] >= threshold:
                return result[0]
            return None
 
        query_head, query_tail = composite_query
        best_key = None
        best_score = -1.0
 
        for key in keys:
            composite_key = self._split_composite(key)
            if composite_key is None:
                # сравниваем хвост запроса с целым ключом — короткий ключ
                # типа простого алиаса без тире
                score = fuzz.token_sort_ratio(query_tail, key)
            else:
                key_head, key_tail = composite_key
                # специфическая часть должна сама быть похожа — это и есть
                # защита от ложных совпадений по общему родовому префиксу
                # Обе части составного имени должны совпасть, берётся ХУДШАЯ
                # из двух оценок. Раньше сравнивался только хвост, а в
                # каталоге DSS десятки показателей делят один хвост:
                # "Занятость населения по группам ... — Сельское, лесное и
                # рыбное хозяйство" и "Среднемесячная номинальная заработная
                # плата — Сельское, лесное и рыбное хозяйство". Хвост давал
                # 100%% обоим, выигрывал тот, что раньше стоит в CSV — и
                # численность занятых (181800 чел.) сохранялась под именем
                # зарплаты (31066 тг). Ошибка неотличима от нормальных данных.
                #
                # min() строг намеренно: лучше оставить показатель unmapped
                # и показать человеку, чем подписать чужим именем.
                # token_set_ratio, а НЕ token_sort_ratio: источники пишут
                # родовую часть длиннее каталожной ("Среднемесячная номинальная
                # заработная плата одного работника по секторам экономики,
                # тенге" против каталожного "Среднемесячная номинальная
                # заработная плата"). token_sort штрафует за лишние слова и
                # даёт 65 — ниже любого разумного порога, хотя это один и тот
                # же показатель. token_set сравнивает по множеству слов и даёт
                # 100, при этом чужие пары остаются низкими: "Урожайность с/х
                # культур" vs "Валовой сбор с/х культур" = 82, "Занятое
                # население..." vs "Среднемесячная заработная плата" = 34.
                # Порог 85 проходит ровно между этими группами.
                _head = fuzz.token_set_ratio(query_head, key_head)
                if _head < COMPOSITE_HEAD_MIN:
                    continue
                score = fuzz.token_sort_ratio(query_tail, key_tail)
 
            if score > best_score:
                best_score = score
                best_key = key
 
        if best_key is not None and best_score >= threshold:
            return best_key
        return None
 
    def match(self, raw_name: str, threshold: int = INDICATOR_FUZZY_THRESHOLD):
        """
        Возвращает (canon_indicator_name, direction_code, unit_name) или None.
        Порядок попыток: точный alias -> точный canon -> нечёткий alias ->
        нечёткий canon.
        """
        norm = self._normalize(raw_name)
 
        pref = self.by_direction.get(self.preferred_direction or "", {})
        if pref:
            if norm in self.alias_map and self.alias_map[norm] in pref:
                return pref[self.alias_map[norm]]
            if norm in pref:
                return pref[norm]
        if norm in self.alias_map:
            return self.entries[self.alias_map[norm]]
        if norm in self.entries:
            return self.entries[norm]
 
        if not _HAS_RAPIDFUZZ:
            return None
 
        if self.alias_map:
            alias_keys = list(self.alias_map.keys())
            best = self._fuzzy_best(norm, alias_keys, threshold)
            if best:
                return self.entries[self.alias_map[best]]
 
        if pref:
            best = self._fuzzy_best(norm, list(pref.keys()), threshold)
            if best:
                return pref[best]
        if self._keys:
            best = self._fuzzy_best(norm, self._keys, threshold)
            if best:
                return self.entries[best]
 
        return None
 

# ---------------------------------------------------------------------------
# Детектор блоков (показатель + года + данные) на одном листе
# ---------------------------------------------------------------------------
 
@dataclass
class Block:
    title: str            # человекочитаемое название показателя для этого блока
    years_row: int         # индекс строки с годами
    data_start: int        # первая строка данных
    data_end: int          # последняя строка данных (exclusive)
    year_cols: dict        # {col_idx: year}
    region_col: int = 0
    parent_header: str | None = None  # родовой заголовок листа, если есть (для составных имён)
 
 
def find_blocks(df: pd.DataFrame, region_col: int = 0) -> list[Block]:
    """
    Сканирует лист и находит все блоки "показатель + таблица по годам".
 
    Поддерживает листы с одним показателем (1 блок) и листы с несколькими
    показателями друг под другом, разделёнными новой строкой годов
    (см. занятость_по_сферам.xlsx: 3 блока на одном листе, у каждого общий
    родовой заголовок "Занятое население по группам ... деятельности" и
    своя подкатегория в строке с годами).
    """
    n = len(df)
    blocks: list[Block] = []
    i = 0
    last_seen_header: str | None = None
 
    while i < n:
        years_idx = None
        for j in range(i, min(i + 4, n)):
            if _row_is_years(df.iloc[j]):
                years_idx = j
                break
        if years_idx is None:
            # запоминаем текст как потенциальный родовой заголовок
            cell = df.iloc[i, 0] if i < n else None
            if pd.notna(cell) and str(cell).strip() and len(str(cell).strip()) > 15:
                last_seen_header = str(cell).strip()
            i += 1
            continue
 
        year_cols = {
            col: int(float(str(v)))
            for col, v in enumerate(df.iloc[years_idx])
            if col != region_col and _is_year(v)
        }
 
        # Название показателя для этого блока: предпочитаем текст в region_col
        # самой строки годов (паттерн "подкатегория в одном ряду с годами"),
        # НО только если это не служебная подпись колонки вида "Наименование
        # районов" — такие подписи игнорируются в пользу родового заголовка.
        same_row_label = df.iloc[years_idx, region_col]
 
        # Ищем родовой заголовок, поднимаясь вверх от строки годов и
        # пропуская пустые строки-разделители (см. "Величина прожиточного
        # минимума": между заголовком и годами есть одна пустая строка).
        above_label = None
        for up in range(years_idx - 1, max(years_idx - 4, -1), -1):
            candidate = df.iloc[up, 0]
            if pd.notna(candidate) and str(candidate).strip():
                above_label = candidate
                break
 
        same_row_is_real_subcategory = (
            pd.notna(same_row_label)
            and str(same_row_label).strip()
            and not _is_generic_column_label(same_row_label)
        )
 
        parent_header = None
        if same_row_is_real_subcategory:
            title = str(same_row_label).strip()
            # если непосредственно над строкой годов есть длинный текст —
            # это родовой заголовок ("Занятое население по группам... деятельности")
            if pd.notna(above_label) and len(str(above_label).strip()) > 15:
                parent_header = str(above_label).strip()
            elif last_seen_header:
                parent_header = last_seen_header
        elif pd.notna(above_label) and str(above_label).strip():
            title = str(above_label).strip()
        elif last_seen_header:
            title = last_seen_header
        else:
            title = "unknown"
 
        data_start = years_idx + 1
        data_end = n
        k = data_start
        while k < n:
            row = df.iloc[k]
            row_all_empty = row.isna().all()
            if row_all_empty:
                # пустая строка-разделитель между блоками — не данные, но и не конец
                # блока сама по себе; реальный конец определяется следующей
                # содержательной строкой (заголовком или новой строкой годов)
                k += 1
                continue
 
            rest_empty = row.drop(index=region_col).isna().all() if region_col in row.index else row.iloc[1:].isna().all()
            first_val = row.iloc[region_col] if region_col < len(row) else None
            if rest_empty and pd.notna(first_val) and not _looks_like_region(first_val):
                data_end = k
                break
            # новая строка годов = новый блок начинается прямо здесь
            if _row_is_years(row):
                data_end = k
                break
            k += 1
 
        blocks.append(Block(
            title=title,
            years_row=years_idx,
            data_start=data_start,
            data_end=data_end,
            year_cols=year_cols,
            region_col=region_col,
            parent_header=parent_header,
        ))
        i = data_end
 
    return blocks
 
# ---------------------------------------------------------------------------
# Конвертация одного блока в строки DSS-формата
# ---------------------------------------------------------------------------
 
def match_block(block: "Block", catalog: IndicatorCatalog,
                fallback_direction: str = "unmapped",
                fallback_unit: str = "единиц"):
    """
    Сопоставляет один найденный блок с каноническим показателем каталога.
 
    Порядок попыток:
      1. Простое имя блока (block.title).
      2. Составное имя "Родовой заголовок — Подкатегория" — покрывает
         паттерн "Занятость населения по группам ... — Сельское хозяйство".
      3. То же самое, но с укороченным родовым заголовком (без хвоста
         после запятой) — на случай если в каталоге родовая часть короче.
 
    Возвращает (canon_name, direction_code, unit_name, matched: bool).
    matched=False означает, что вернулись fallback-значения, а не то, что
    реально нашлось в каталоге — вызывающий код должен это учитывать
    (например, помечать строки как unmapped или показывать предупреждение
    в /detect).
 
    Вынесена отдельно от block_to_rows, чтобы одна и та же логика
    сопоставления использовалась и при реальной конвертации, и в
    /upload/detect для превью — иначе превью могло бы показывать
    "не сматчено" для блока, который на самом деле успешно обработается.
    """
    match = catalog.match(block.title)
    if not match and block.parent_header:
        composite = f"{block.parent_header} — {block.title}"
        match = catalog.match(composite)
        if not match:
            short_parent = re.sub(r",.*$", "", block.parent_header).strip()
            match = catalog.match(f"{short_parent} — {block.title}")
 
    if match:
        return match[0], match[1], match[2], True
    return block.title, fallback_direction, fallback_unit, False
 
 
# --- АВТО-ОРИЕНТАЦИЯ АНПИВОТА (форма A: показатели в строках, регион = СКО) ---
_SKO_KATO = 590000000
_TOTAL_LABELS = {"итого", "всего", "total", "по области", "по ско", "всего по области"}

def _parse_number(val):
    """Числа в экспортном формате: '102 632', '17,4', неразрывные пробелы, 'н/д'."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    s = s.replace("\u00a0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN/inf -> невалидный JSON
        return None
    return v

def _is_total_label(label) -> bool:
    n = " ".join(str(label).lower().split())
    return n in _TOTAL_LABELS or n.startswith("итого") or n.startswith("всего")

def _text_label_col(df, year_cols, data_start, data_end, default=0):
    """Первая НЕ-годовая колонка с текстовыми подписями. Пропускает столбец '№'."""
    yset = set(year_cols)
    for col in range(df.shape[1]):
        if col in yset:
            continue
        texts = total = 0
        for r in range(data_start, data_end):
            v = df.iloc[r, col]
            if pd.isna(v) or not str(v).strip():
                continue
            total += 1
            if _parse_number(v) is None:
                texts += 1
        if total and texts / total >= 0.5:
            return col
    return default

def _kato_hit_ratio(df, label_col, data_start, data_end):
    total = hits = 0
    for r in range(data_start, data_end):
        v = df.iloc[r, label_col]
        if pd.isna(v) or not str(v).strip():
            continue
        total += 1
        if lookup_kato(str(v).strip()) is not None:
            hits += 1
    return (hits / total if total else 0.0), total

def resolve_orientation(df, block):
    """('regions', col) — строки=районы; ('indicators', col) — строки=показатели, регион=СКО."""
    label_col = _text_label_col(df, block.year_cols, block.data_start, block.data_end, block.region_col)
    ratio, total = _kato_hit_ratio(df, label_col, block.data_start, block.data_end)
    if total == 0:
        return "indicators", label_col
    return ("regions" if ratio >= 0.5 else "indicators"), label_col


def _extra_label_cols(df, block, label_col, max_extra=2):
    """
    Дополнительные текстовые колонки-подписи слева от годов.

    Нужны там, где показатель определяется НЕ одной колонкой:
        Промышленные сбросы | Объем водоотведения, млн м3      | 13,72 | ...
        Промышленные сбросы | Объем загрязняющих веществ, тыс.т | 15,13 | ...
    Обе строки — разные показатели, но раньше имя бралось только из первой
    колонки, обе получали имя "Промышленные сбросы" и схлопывались при
    дедупликации: из двух значений в базу попадало одно, выбранное порядком.

    Берутся только колонки левее первого года, где текстовых значений не
    меньше половины (числовые "№" и колонки данных отсеиваются).
    """
    yset = set(block.year_cols)
    first_year = min(yset) if yset else df.shape[1]
    out = []
    for col in range(df.shape[1]):
        if col == label_col or col in yset or col >= first_year:
            continue
        texts = total = 0
        for r in range(block.data_start, block.data_end):
            v = df.iloc[r, col]
            if pd.isna(v) or not str(v).strip():
                continue
            total += 1
            if _parse_number(v) is None:
                texts += 1
        if total and texts / total >= 0.5:
            out.append(col)
        if len(out) >= max_extra:
            break
    return out


def block_to_rows(
    df: pd.DataFrame,
    block: Block,
    catalog: IndicatorCatalog,
    fallback_direction: str = "unmapped",
    fallback_unit: str = "единиц",
    source_file: str = "",
    log_fn=print,
):
    """Блок -> DSS-записи с авто-ориентацией (regions | indicators=СКО). '№' пропускается."""
    rows = []
    skipped_regions = []
    mode, label_col = resolve_orientation(df, block)
    _extra_cols = _extra_label_cols(df, block, label_col) if mode == "indicators" else []
    # Охват блока — заголовок таблицы. Нужен, чтобы отличить "Основные
    # показатели мест размещения СКО" от "...Имантау-Шалкарской курортной
    # зоны": оба дают одинаковые имена показателей и один kato (область),
    # но РАЗНЫЕ значения. Без различения дедупликация тихо оставляла одно.
    _scope = (block.parent_header or "").strip()
    if len(_scope) < 10:
        # parent_header часто пуст: find_blocks поднимается вверх от строки
        # годов и упирается в служебную "№", не доходя до строки-заголовка
        # листа. А именно там лежит охват ("...мест размещения СКО" против
        # "...Имантау-Шалкарской курортной зоны"), который единственный
        # отличает два блока с одинаковыми показателями и разными числами.
        try:
            _c0 = df.iloc[0, 0]
            if pd.notna(_c0) and len(str(_c0).strip()) > 15:
                _scope = str(_c0).strip()
        except Exception:
            pass
    if not _scope:
        _scope = (block.title or "").strip()

    if mode == "regions":
        canon_name, direction_code, unit_name, matched = match_block(
            block, catalog, fallback_direction, fallback_unit
        )

    for row_idx in range(block.data_start, block.data_end):
        row = df.iloc[row_idx]
        label_raw = row.iloc[label_col]
        if pd.isna(label_raw) or not str(label_raw).strip():
            continue
        label = str(label_raw).strip()
        if _extra_cols:
            _parts = [label]
            for _c in _extra_cols:
                _v = row.iloc[_c]
                if pd.notna(_v) and str(_v).strip():
                    _s = " ".join(str(_v).split())
                    if _s not in _parts:
                        _parts.append(_s)
            label = " — ".join(_parts)

        if mode == "regions":
            kato_code = lookup_kato(label)
            if kato_code is None:
                skipped_regions.append(label)
                continue
            r_name, r_dir, r_unit, r_matched = canon_name, direction_code, unit_name, matched
        else:
            if _is_total_label(label):
                continue
            kato_code = _SKO_KATO
            # Сначала пробуем ИМЯ С КОНТЕКСТОМ блока, и только потом голую
            # подпись строки. Подпись сама по себе часто неоднозначна: в
            # каталоге DSS "Сельское, лесное и рыбное хозяйство" существует
            # и как хвост "Среднемесячная номинальная заработная плата — ...",
            # и как хвост "Занятость населения по группам ... — ...". Без
            # контекста выбирался тот, что раньше стоит в CSV, из-за чего
            # численность занятых записывалась под именем зарплаты.
            #
            # Заголовок блока ("Занятое население по группам видов
            # экономической деятельности") эту неоднозначность снимает —
            # составной матчинг сверяет и родовую часть, и хвост.
            m = None
            if _scope:
                m = catalog.match(f"{_scope} — {label}")
            if not m:
                m = catalog.match(label)
            if m:
                r_name, r_dir, r_unit, r_matched = m[0], m[1], m[2], True
            else:
                r_name, r_dir, r_unit, r_matched = label, fallback_direction, fallback_unit, False

        for col_idx, year in block.year_cols.items():
            val = row.iloc[col_idx]
            if pd.isna(val) or str(val).strip() in ("-", "", "\u2014", "н/д", "нд", "n/a"):
                continue
            value = _parse_number(val)
            if value is None:
                continue
            rows.append({
                "indicator_name": r_name,
                "direction_code": r_dir,
                "unit_name": r_unit,
                "kato_code": kato_code,
                "period_type": "year",
                "period_start": f"{year}-01-01",
                "period_end": f"{year}-12-31",
                "value": value,
                "_scope": _scope,
                "comment": "" if r_matched else "АВТО: показатель не найден в каталоге, требует ручной проверки direction/unit",
            })

    if skipped_regions:
        uniq = sorted(set(skipped_regions))
        log_fn(f"    [ПРОПУСК] '{block.title}': нет КАТО для районов: {uniq}")
    return rows


# ---------------------------------------------------------------------------
# Точка входа для целого файла (все листы)
# ---------------------------------------------------------------------------
 
def convert_sheets(sheets: dict, catalog: IndicatorCatalog,
                   source_file: str = "", log_fn=print):
    """
    Принимает уже прочитанные листы (dict[sheet_name, DataFrame], как
    возвращает pd.read_excel(..., sheet_name=None)) и прогоняет их через
    детектор блоков + сопоставление с каталогом.
 
    Это основной движок, не зависящий от того, лежит ли исходный файл на
    диске или был получен прямо в память (например, из UploadFile во
    FastAPI без промежуточного сохранения).
 
    Возвращает (rows, stats) — то же самое, что и convert_file.
    """
    all_rows = []
    stats = []
 
    for sheet_name, df in sheets.items():
        if df is None or df.empty:
            log_fn(f"  [{sheet_name}] лист пустой — пропущен")
            continue
 
        blocks = find_blocks(df)
        if not blocks:
            log_fn(f"  [{sheet_name}] блоков с данными не найдено — лист пропущен "
                   f"(вероятно справочник/не табличные данные)")
            continue
 
        sheet_rows = 0
        for block in blocks:
            rows = block_to_rows(
                df, block, catalog,
                source_file=source_file, log_fn=lambda m: log_fn(f"  [{sheet_name}] {m}"),
            )
            all_rows.extend(rows)
            sheet_rows += len(rows)
            log_fn(f"  [{sheet_name}] блок '{block.title[:60]}': {len(rows)} строк")
 
        stats.append((sheet_name, len(blocks), sheet_rows))
    _n = _disambiguate_scopes(all_rows, log_fn=log_fn)
    if _n:
        log_fn(f"  [ОХВАТ] уточнено имён у {_n} строк (подтерритории внутри области)")

    return all_rows, stats


# Заголовок считается "основным" (вся область), если явно упоминает СКО или
# область как отдельное слово. \bско\b не срабатывает внутри "Шалкарской" —
# именно это отличает областной ряд от ряда по курортной зоне.
_SCOPE_WHOLE_REGION = re.compile(r"\bско\b|\bобласт\w*", re.I)


def _disambiguate_scopes(rows: list, log_fn=print) -> int:
    """
    Разводит строки, у которых совпал ключ (показатель, территория, период),
    но значения РАЗНЫЕ и пришли они из блоков с разным охватом.

    Такие строки нельзя оставлять одноимёнными: дальше по конвейеру
    дедупликация оставит одну из них, и какая именно — вопрос порядка.
    Строки более узкого охвата получают уточнённое имя и помечаются
    unmapped: в каталоге DSS нет показателей уровня "курортная зона", и
    выдумывать их нельзя — зато данные сохраняются и видны.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[(r["indicator_name"], r["kato_code"], r["period_start"])].append(r)

    renamed = 0
    for grp in groups.values():
        if len(grp) < 2:
            continue
        if len({r.get("_scope", "") for r in grp}) < 2:
            continue
        if len({r["value"] for r in grp}) < 2:
            continue
        for r in grp:
            sc = r.get("_scope", "")
            if not sc or _SCOPE_WHOLE_REGION.search(sc):
                continue
            r["indicator_name"] = f"{r['indicator_name']} — {sc}"
            r["direction_code"] = "unmapped"
            r["comment"] = ("АВТО: данные по подтерритории внутри области "
                            f"('{sc[:70]}'), в каталоге DSS такого разреза нет — "
                            "требует ручной проверки")
            renamed += 1

    for r in rows:
        r.pop("_scope", None)
    return renamed
 
 
def convert_file(path: str | Path, catalog: IndicatorCatalog, log_fn=print):
    """
    Читает все листы файла с диска и делегирует обработку в convert_sheets.
    Используй convert_sheets напрямую, если файл уже прочитан в память
    (например, байты из загруженного через API файла).
    """
    path = Path(path)
 
    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None)
    except Exception as e:
        log_fn(f"[ERROR] Не могу открыть {path.name}: {e}")
        return [], []
 
    return convert_sheets(sheets, catalog, source_file=path.name, log_fn=log_fn)


def disambiguate_by_source(rows: list, log_fn=print) -> int:
    """
    Разводит категориальные строки, у которых совпал ключ (показатель,
    территория, период), но значения РАЗНЫЕ и пришли они из таблиц с разными
    заголовками.

    Случай из практики: "Таблица 1 - Валовая продукция сельского хозяйства,
    млн.тенге" и "Таблица 2 - Структура валовой продукции ..., в %" имеют
    ОДИНАКОВУЮ подпись колонки ("Валовая продукция (ВП) сельского
    хозяйства"), которая сама по себе совпадает с каталожным показателем.
    Обе таблицы получали одно имя, и 4721.7 млн.тенге затирались долей 2.2 %.

    Право на каноническое имя получает та таблица, чей заголовок ближе к
    самому показателю (token_set_ratio): у Таблицы 1 схожесть 100, у
    Таблицы 2 — 81, потому что "структура ... в %" — это уже другой
    показатель. Остальные строки сохраняются под собственным именем с
    пометкой unmapped: данные видны и не затирают друг друга.
    """
    from collections import defaultdict
    if not _HAS_RAPIDFUZZ:
        return 0
    groups = defaultdict(list)
    for r in rows:
        groups[(r["indicator_name"], r["kato_code"], r["period_start"])].append(r)

    renamed = 0
    for grp in groups.values():
        if len(grp) < 2:
            continue
        scopes = {r.get("_scope", "") for r in grp}
        if len(scopes) < 2 or len({r["value"] for r in grp}) < 2:
            continue
        canon = IndicatorCatalog._normalize(grp[0]["indicator_name"])
        best = max(grp, key=lambda r: fuzz.token_set_ratio(
            IndicatorCatalog._normalize(r.get("_scope", "")), canon))
        for r in grp:
            if r is best:
                continue
            sc = r.get("_scope", "")
            if not sc:
                continue
            r["indicator_name"] = f"{r['indicator_name']} — {sc}"
            r["direction_code"] = "unmapped"
            r["comment"] = ("АВТО: тот же показатель уже занят таблицей с более "
                            f"близким заголовком; источник этой строки — '{sc[:70]}' "
                            "— требует ручной проверки")
            renamed += 1

    for r in rows:
        r.pop("_scope", None)
    return renamed
