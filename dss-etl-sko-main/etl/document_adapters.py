# etl/document_adapters.py
"""
Адаптеры для PDF/DOCX -> dict[str, DataFrame] в том же формате, что
pd.read_excel(..., sheet_name=None, header=None) отдаёт для .xlsx/.xls.

Назначение: чтобы universal_detector.convert_sheets() — наш единственный
движок распознавания структуры (район×года, fuzzy-matching КАТО, каталог
показателей) — работал одинаково независимо от того, пришёл файл как
Excel, Word или PDF. Сам universal_detector.py не меняется и не знает
про PDF/DOCX вообще — он просто получает уже готовый dict с DataFrame.

Ключевая сложность по сравнению с Excel: в Excel-файлах название
показателя обычно сидит первой строкой листа, прямо над строкой с годами.
В Word и PDF название показателя почти всегда идёт отдельным текстовым
параграфом/строкой ПЕРЕД таблицей, а не внутри самой таблицы. Поэтому
оба адаптера явно "пришивают" этот текст как первую строку получившегося
DataFrame — это воспроизводит Excel-паттерн, на который рассчитан
find_blocks() в universal_detector.py, без необходимости туда что-то
добавлять.

Ограничения (то, что эти адаптеры заведомо не покрывают):
  - Таблицы, где сам текст показателя разбит на несколько параграфов
    или вынесен в верхний колонтитул документа — заголовок не подхватится,
    блок попадёт в universal_detector с title=ближайший_предыдущий_текст,
    что может оказаться неподходящим. Решается так же, как и для Excel:
    несматченный показатель не теряется, помечается unmapped, виден в
    /upload/detect и в финальном отчёте конвертации.
  - PDF со сканированными (не текстовыми) таблицами — pdfplumber не
    распознаёт растровый текст; для этого нужен OCR (pytesseract), не
    входит в эту реализацию.
  - Многостраничные таблицы, где заголовок повторяется на каждой
    странице PDF — каждая страница обрабатывается как отдельная
    "псевдо-таблица"; если заголовок показателя есть только на первой
    странице, для последующих страниц используется тот же заголовок
    (если он не повторяется на странице, берётся текст последнего
    известного заголовка — см. _split_pdf_tables_by_page).

ВЕРТИКАЛЬНЫЕ ТАБЛИЦЫ (года в столбце, показатели в колонках): в отличие
от наших обычных Excel-источников (район в строках, года в столбцах,
один показатель на блок), документы Word/PDF из госстатистики нередко
оформлены наоборот — год идёт первым столбцом, а каждая следующая
колонка — отдельный показатель (например: "Год | Общая площадь | Пашня |
Залежь | ..."), без разбивки по районам (одна строка = СКО целиком за год).
find_blocks() из universal_detector.py на такую структуру не рассчитан —
он ищет года вдоль строки, не вдоль столбца. Чтобы не плодить вторую
ветку логики распознавания внутри universal_detector.py, такие таблицы
здесь же, в адаптере, перестраиваются (pivot) в привычный формат: каждая
колонка-показатель становится отдельным псевдо-листом вида "регион
(всегда один — СКО) х года", после чего find_blocks() обрабатывает их
точно так же, как обычный Excel-блок. См. _detect_and_pivot_vertical_table.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import re

import pandas as pd


# ---------------------------------------------------------------------------
# Вертикальные таблицы (год в столбце 0, показатели в колонках)
# ---------------------------------------------------------------------------

_YEAR_MIN, _YEAR_MAX = 1990, 2035
DEFAULT_REGION_NAME = "Северо-Казахстанская область"


def _is_year_like(value) -> bool:
    try:
        f = float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        return False
    if f != f or f in (float("inf"), float("-inf")):  # NaN/inf -> не год
        return False
    if f != int(f):          # дробное -> значение показателя, не год
        return False
    return _YEAR_MIN <= int(f) <= _YEAR_MAX


def _to_number(value):
    """Поддержка казахстанского формата чисел — запятая как разделитель."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "").replace(",", ".")
    if s in ("", "-", "nan", "none", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def detect_and_pivot_vertical_table(
    df: pd.DataFrame,
    region_name: str = DEFAULT_REGION_NAME,
) -> Optional[dict[str, pd.DataFrame]]:
    """
    Распознаёт таблицу вида "Год | Показатель1 | Показатель2 | ..." (года
    вертикально в первом столбце) и разворачивает каждую колонку-показатель
    в отдельный псевдо-лист формата "регион х года" — совместимого с
    find_blocks() из universal_detector.py.

    Возвращает None, если таблица не похожа на такую структуру (тогда
    вызывающий код должен передать df в find_blocks() как есть — это может
    быть обычная "регион х года" таблица).

    region_name используется как единственная "строка-регион" псевдо-листа,
    т.к. подобные документы обычно агрегируют данные по всей области целиком,
    без разбивки по районам.
    """
    header_row_idx = None
    for i in range(min(3, len(df))):
        val = str(df.iloc[i, 0]).strip().lower()
        if val in ("год", "годы", "year", "years"):
            header_row_idx = i
            break
    if header_row_idx is None:
        return None

    title = str(df.iloc[0, 0]).strip() if header_row_idx > 0 else None
    header_row = df.iloc[header_row_idx].tolist()
    data_start = header_row_idx + 1

    year_rows = []
    for i in range(data_start, len(df)):
        v = df.iloc[i, 0]
        if _is_year_like(v):
            year_rows.append((i, int(float(str(v)))))
    if len(year_rows) < 3:
        # меньше 3 лет — слишком мало, чтобы быть уверенным, что это
        # действительно временной ряд, а не случайное совпадение чисел
        return None

    years = [year for _, year in year_rows]

    # ТРАНСПОНИРОВАННАЯ таблица: годы в первом столбце, а в шапке — РАЙОНЫ,
    # а не показатели ("Год | Айыртауский | Акжарский | ..."). Раньше такая
    # таблица шла общим путём, и каждый район становился частью имени
    # показателя ("Поголовье КРС — Айыртауский район"), а КАТО всем строкам
    # проставлялся областной. Данные выглядели нормально, но были привязаны
    # не к тем территориям — ошибка, незаметная в отчёте.
    #
    # Проверяем шапку по справочнику КАТО: если половина и более колонок
    # опознаётся как районы, таблица перестраивается в обычный вид
    # "район в строках, годы в столбцах" и дальше обрабатывается штатно.
    from universal_detector import lookup_kato

    _cols = [c for c in range(1, len(header_row))
             if header_row[c] is not None
             and str(header_row[c]).strip().lower() not in ("", "nan", "none", "год")]
    _region_cols = [c for c in _cols if lookup_kato(str(header_row[c]).strip()) is not None]

    if _cols and len(_region_cols) >= max(2, len(_cols) * 0.5):
        _title = title or "Показатель"
        _rows = [[_title] + [None] * len(years),
                 ["Район"] + years]
        for c in _region_cols:
            _vals = [_to_number(df.iloc[ri, c]) for ri, _ in year_rows]
            if all(v is None for v in _vals):
                continue
            _rows.append([str(header_row[c]).strip()] + _vals)
        if len(_rows) > 2:
            return {"транспонированная": pd.DataFrame(_rows)}

    result: dict[str, pd.DataFrame] = {}

    for col_idx in range(1, len(header_row)):
        col_name = header_row[col_idx]
        if col_name is None or str(col_name).strip().lower() in ("год", "nan", "none", ""):
            continue
        col_name = str(col_name).strip()
        full_title = f"{title} — {col_name}" if title else col_name

        values = [_to_number(df.iloc[row_i, col_idx]) for row_i, _ in year_rows]
        if all(v is None for v in values):
            continue  # колонка целиком пустая — пропускаем, не плодим мусорные блоки

        pseudo_rows = [
            [full_title] + [None] * len(years),
            [None] + years,
            [region_name] + values,
        ]
        result[col_name] = pd.DataFrame(pseudo_rows)

    return result or None


# ---------------------------------------------------------------------------
# Категориальные таблицы без временного измерения (район х категория,
# напр. расстояния до объектов инфраструктуры — не меняются по годам)
# ---------------------------------------------------------------------------

# DSS-протокол (data_intake_template.xlsx, README) требует period_type
# строго из {year, quarter, month, day} — формата "без периода" там не
# предусмотрено. Поэтому статические характеристики (расстояние до
# аэропорта, до ж/д станции и т.п.) записываются как period_type='year' с
# датой "снимка" — годом, когда эти данные были актуальны/собраны. Это
# самый правильный способ в рамках существующего шаблона DSS: данные не
# теряются и не искажаются, просто получают период = момент актуальности,
# а не временной ряд из нескольких лет.
_DISTANCE_RE = re.compile(r"([\d.,]+)\s*км")
_METERS_RE = re.compile(r"([\d.,]+)\s*м(?!ин)")  # "м", но не "минут"


def _extract_distance_km(text) -> Optional[float]:
    """
    Извлекает расстояние в км из текста вида "224 км (3 часа 5 минут)"
    или "930 м" (метры конвертируются в км). Возвращает None для
    непарсящихся значений ("-", пустая строка, чисто текстовое описание).
    """
    if text is None:
        return None
    text = str(text).strip()
    if not text or text in ("-", "—"):
        return None

    m = _DISTANCE_RE.search(text)
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None

    m = _METERS_RE.search(text)
    if m:
        try:
            return float(m.group(1).replace(",", ".")) / 1000
        except ValueError:
            return None

    return None


def _clean_region_cell(text: str) -> str:
    """
    Убирает уточнение в скобках после названия района — в этих документах
    район указан как "Айыртауский\\n(село Саумалколь)", а kato_map.py знает
    только "Айыртауский". Без очистки fuzzy-matching мог бы случайно
    смэтчить не туда из-за лишнего текста в скобках.
    """
    return re.sub(r"\s*\(.*?\)\s*$", "", text.strip()).strip()


_REGION_HEADER_SET = (
    "район", "районы", "region", "район/город",
    "районы ско", "район ско", "наименование района", "наименование районов",
)

# Раньше заголовок колонки с районами искался ТОЧНЫМ совпадением со списком
# выше. Реальные документы пишут его десятком способов ("Административный
# район", "Наименование района", "Район/город"), и любой незнакомый вариант
# means таблица вообще не распознавалась — 0 блоков, 0 строк.
#
# Регулярка требует, чтобы ячейка состояла ТОЛЬКО из служебных слов: это
# отличает заголовок "Административный район" от данных "Айыртауский район"
# (собственное имя не входит в разрешённый набор слов, поэтому строка данных
# заголовком не станет и не собьёт границу шапки).
_REGION_HEADER_RE = re.compile(
    # ветка 1: "...район/регион" с любыми служебными определениями
    r"^(№\s*)?((наименование|название|административн\w+|адм\.?)\s+)*"
    r"(район\w*|регион\w*|region)"
    r"([\s/-]*(город\w*|ско|области|область))?$"
    r"|"
    # ветка 2: "Наименование адм единицы", "Административная единица" —
    # так подписаны таблицы биоёмкости и экологического следа. Слово
    # "единица" само по себе слишком общее (легко спутать с колонкой
    # единиц измерения), поэтому требуется определение "адм"/
    # "административн" перед ним — без него ветка не срабатывает.
    r"^(№\s*)?(наименование\s+|название\s+)?"
    r"(административн\w*|адм\.?)\s*(единиц\w*|территор\w*|образован\w*)$", re.I)


def _is_region_header(text) -> bool:
    if text is None:
        return False
    t = " ".join(str(text).split())
    if not t or t.lower() in ("nan", "none"):
        return False
    return t.lower() in _REGION_HEADER_SET or bool(_REGION_HEADER_RE.match(t))

_PERIOD_RANGE_RE = re.compile(r"(19|20)(\d\d)\s*[-\u2013\u2014]\s*(?:(19|20))?(\d\d)")


def _parse_period_range(text):
    """'2004-2014гг.' -> (2004, 2014). None, если диапазон не найден ИЛИ их
    несколько (например 'Рост 2015-2024 к 2004-2014' — период неоднозначен)."""
    if text is None:
        return None
    s = " ".join(str(text).split())
    if not s:
        return None
    matches = list(_PERIOD_RANGE_RE.finditer(s))
    if len(matches) != 1:
        return None
    m = matches[0]
    y1 = int(m.group(1) + m.group(2))
    y2 = int((m.group(3) or m.group(1)) + m.group(4))
    if y2 < y1 or y2 - y1 > 40:
        return None
    if not (_YEAR_MIN <= y1 <= _YEAR_MAX and _YEAR_MIN <= y2 <= _YEAR_MAX):
        return None
    return (y1, y2)


def _has_ambiguous_period(text):
    """True, если в заголовке 2+ диапазонов (сравнительная колонка)."""
    if text is None:
        return False
    return len(list(_PERIOD_RANGE_RE.finditer(" ".join(str(text).split())))) > 1


def _is_total_label(text):
    n = " ".join(str(text).lower().split())
    return (n in ("всего", "итого", "итог", "total", "всего по области")
            or n.startswith("итого") or n.startswith("всего"))


def _is_numbering_row(df, row_idx):
    """Строка вида 1,2,3,4... — нумерация колонок, не данные."""
    vals = []
    for c in range(df.shape[1]):
        v = df.iloc[row_idx, c]
        try:
            f = float(str(v).strip().replace(",", "."))
        except (ValueError, TypeError):
            continue
        if f == f:
            vals.append(f)
    return len(vals) >= 3 and vals == [float(i) for i in range(1, len(vals) + 1)]


def detect_categorical_table(
    df: pd.DataFrame,
    catalog,
    snapshot_year: int,
    indicator_title: Optional[str] = None,
) -> list[dict]:
    """Район x категория -> DSS-записи.

    Поддерживает:
      * многоэтажную шапку (метка региона повторяется в объединённых ячейках);
      * периоды-диапазоны в шапке ('2004-2014гг.', 'В среднем за 2015-2024'):
        значение разворачивается на каждый год периода (period_type='year',
        как требует спецификация DSS), в comment пишется 'среднее за ...';
      * колонки без периода -> снимок на snapshot_year (прежнее поведение);
      * неоднозначные колонки ('Рост 2015-2024 к 2004-2014') -> снимок +
        явная пометка в comment, период не выдумывается;
      * пропуск итоговых колонок ('Всего'/'Итого') и строк нумерации.
    """
    from universal_detector import match_block, lookup_kato

    if df.empty or len(df) < 2:
        return []

    header_rows = []
    region_col = None
    for hr in range(min(6, len(df))):
        for c in range(min(4, df.shape[1])):
            cell = df.iloc[hr, c]
            cell = " ".join(str(cell).split()).lower() if cell is not None else ""
            if _is_region_header(cell):
                header_rows.append(hr)
                if region_col is None:
                    region_col = c
                break
    if region_col is None or not header_rows:
        return []

    # Второй этаж шапки. Первый этаж находится по метке региона ("Районы"),
    # но в таблицах вида
    #     № | Районы | Культурно-исторические факторы (объединено) | ...
    #       |        | Кол-во музеев | Кол-во театров | Кол-во памятников
    # ячейка региона на втором этаже ПУСТАЯ (вертикальное объединение), и
    # прежний код его не видел. Из-за этого все колонки получали одно имя
    # родовой группы, музеи/театры/памятники сливались в один показатель, а
    # их разные значения конфликтовали по одному ключу и терялись при дедупе.
    #
    # Признак второго этажа: ячейка региона пуста, а в остальных колонках
    # преобладают текстовые подписи, а не числа (числа = уже строка данных).
    _hr = max(header_rows) + 1
    while _hr < len(df):
        _reg = df.iloc[_hr, region_col]
        _reg_s = "" if _reg is None else str(_reg).strip()
        if _reg_s and _reg_s.lower() not in ("nan", "none"):
            break
        _texts = _nums = 0
        for _c in range(region_col + 1, df.shape[1]):
            _v = df.iloc[_hr, _c]
            _s = "" if _v is None else str(_v).strip()
            if not _s or _s.lower() in ("nan", "none"):
                continue
            try:
                float(_s.replace("\u00a0", "").replace(" ", "").replace(",", "."))
                _nums += 1
            except ValueError:
                _texts += 1
        if _texts >= 2 and _texts > _nums:
            header_rows.append(_hr)
            _hr += 1
        else:
            break

    last_header = max(header_rows)
    data_start = last_header + 1
    if data_start < len(df) and _is_numbering_row(df, data_start):
        data_start += 1

    all_rows = []
    # Объединённые ячейки шапки python-docx размножает: одна логическая
    # колонка "Пашни" превращается в несколько физических с одинаковым
    # текстом, и шапка съезжает относительно данных. В итоге РАЗНЫЕ столбцы
    # ("Пашни — Суммарная БЕ" = 3878992.72 и "Пастбища — Суммарная БЕ" =
    # 494463.08) получали одно имя и схлопывались при дедупликации.
    #
    # _seen_cols хранит имя -> (первый столбец, его вектор значений):
    #   вектор совпал  -> это дубль от объединения, столбец пропускается;
    #   вектор другой  -> к имени добавляется номер, данные сохраняются.
    _seen_cols = {}
    # Показатели, уже занятые в ЭТОЙ таблице. Нужны потому, что каталог DSS
    # не всегда так же детален, как источник: у "Удельной биоёмкости" там
    # один показатель, а в таблице шесть столбцов (Пашни, Пастбища, Леса,
    # Водоёмы, Застроенные, Итого). Все шесть матчились в одно имя и пять
    # значений из шести терялись при дедупликации. Теперь первый столбец
    # забирает каноническое имя, остальные остаются под своими названиями
    # как unmapped — данные видны, ничего не затирается.
    _used_canon = set()

    for col_idx in range(region_col + 1, df.shape[1]):
        parts = []
        period = None
        ambiguous = False
        for hr in header_rows:
            raw = df.iloc[hr, col_idx]
            txt = " ".join(str(raw).split()) if raw is not None else ""
            if not txt or txt.lower() in ("nan", "none"):
                continue
            if _has_ambiguous_period(txt):
                ambiguous = True
            rng = _parse_period_range(txt)
            if rng and not ambiguous:
                period = rng
                continue
            if _is_region_header(txt):
                continue
            if txt not in parts:
                parts.append(txt)

        col_name = " — ".join(parts).strip()
        if not col_name:
            # Шапка состоит ТОЛЬКО из периода ("2004-2014гг. | 2015-2024гг.")
            # без названия показателя — оно вынесено в заголовок таблицы.
            # Раньше такая колонка отбрасывалась: _parse_period_range
            # забирал текст себе, parts оставался пустым, и вся таблица
            # давала ноль строк, хотя данные полностью читаемы.
            if period and indicator_title:
                col_name = ""
            else:
                continue
        if _is_total_label(col_name):
            continue

        _vec = tuple(
            "" if df.iloc[_r, col_idx] is None else str(df.iloc[_r, col_idx]).strip()
            for _r in range(data_start, len(df))
        )
        # Столбец-призрак от объединённой ячейки. python-docx дублирует
        # содержимое merged-ячейки во все столбцы, которые она перекрывает,
        # из-за чего шапка съезжает относительно данных:
        #     col4 подписан "Пашни — Суммарная БЕ",   данные = Пастбищ
        #     col5 подписан "Пастбища — Суммарная БЕ", данные = Пастбищ
        # Векторы значений при этом совпадают побайтно, а ВЕРНАЯ подпись — у
        # последнего столбца пары. Поэтому ранний столбец пропускается: иначе
        # одно и то же число попадало в базу дважды, причём один раз под
        # именем чужой категории угодий.
        if col_idx + 1 < df.shape[1]:
            _next_vec = tuple(
                "" if df.iloc[_r, col_idx + 1] is None
                else str(df.iloc[_r, col_idx + 1]).strip()
                for _r in range(data_start, len(df))
            )
            if _next_vec == _vec and any(x for x in _vec):
                continue

        # Ключ включает период: "КРС за 2004-2014" и "КРС за 2015-2024" —
        # одно имя, но разные периоды, в базе они не сталкиваются, и
        # переименовывать их нельзя (иначе теряется алиас на показатель).
        _key = (col_name, period)
        if _key in _seen_cols:
            _prev_col, _prev_vec = _seen_cols[_key]
            if _vec == _prev_vec:
                continue
            _n = 2
            while (f"{col_name} ({_n})", period) in _seen_cols:
                _n += 1
            col_name = f"{col_name} ({_n})"
            _key = (col_name, period)
        _seen_cols[_key] = (col_idx, _vec)

        if indicator_title and col_name:
            block_title = f"{indicator_title} — {col_name}"
        else:
            block_title = indicator_title or col_name

        class _FakeBlock:
            # Если у колонки нет собственного имени (в шапке был только
            # период), показатель ищется по заголовку таблицы.
            title = col_name or indicator_title or ""
            parent_header = indicator_title if col_name else None

        canon_name, direction_code, unit_name, matched = match_block(_FakeBlock(), catalog)
        # Ключ включает период: один и тот же показатель законно встречается
        # в таблице дважды для разных периодов ("Поголовье КРС" за 2004-2014
        # и за 2015-2024). Без периода второй считался бы дублем и уходил в
        # unmapped, хотя в базе эти строки не сталкиваются — у них разные
        # period_start.
        if matched and (canon_name, period) in _used_canon:
            matched = False
        if matched:
            _used_canon.add((canon_name, period))
        if not matched:
            canon_name = block_title
            direction_code = "unmapped"
            unit_name = "ед."

        if period:
            years = list(range(period[0], period[1] + 1))
            base_comment = f"Среднее за {period[0]}-{period[1]} гг. (значение периода отнесено к каждому году)"
        else:
            years = [snapshot_year]
            if ambiguous:
                base_comment = ("Период в источнике не распознан (сравнительный показатель); "
                                f"записано как снимок на {snapshot_year} год — требует проверки")
            else:
                base_comment = (f"Снимок на {snapshot_year} год (показатель статический, "
                                "не имеет временного ряда в источнике)")

        for row_idx in range(data_start, len(df)):
            region_raw = df.iloc[row_idx, region_col]
            if region_raw is None or not str(region_raw).strip():
                continue
            region_clean = _clean_region_cell(str(region_raw))
            if _is_total_label(region_clean):
                continue

            raw_value = df.iloc[row_idx, col_idx]
            value = _extract_distance_km(raw_value)
            if value is None:
                try:
                    # \u00a0/\u202f — неразрывные пробелы, которыми Word и Excel
                    # разделяют разряды ("60\u00a0202,6"). Без их удаления float()
                    # падал и строка молча терялась.
                    value = float(str(raw_value).strip()
                                  .replace("\u00a0", "").replace("\u202f", "")
                                  .replace(" ", "").replace(",", "."))
                except (ValueError, TypeError):
                    continue
            if value is None or (isinstance(value, float) and (value != value or value in (float("inf"), float("-inf")))):
                continue

            kato = lookup_kato(region_clean)
            if kato is None:
                continue

            for y in years:
                all_rows.append({
                    "indicator_name": canon_name,
                    "direction_code": direction_code,
                    "unit_name":      unit_name,
                    "kato_code":      kato,
                    "period_type":    "year",
                    "period_start":   f"{y}-01-01",
                    "period_end":     f"{y}-12-31",
                    "value":          value,
                    "_scope":         indicator_title or "",
                    "comment":        base_comment if (period or ambiguous or not matched) else "",
                })

    return all_rows


def _disambiguate_categorical(all_rows, log_fn=print):
    """Развести строки, где разные таблицы дали один показатель."""
    from universal_detector import disambiguate_by_source
    return disambiguate_by_source(all_rows, log_fn=log_fn)


def extract_tables_from_docx(path_or_bytes) -> dict[str, pd.DataFrame]:
    """
    Извлекает все таблицы документа Word в формате, совместимом с
    universal_detector.convert_sheets().

    Для каждой таблицы ищет ближайший непустой текстовый параграф ПЕРЕД
    ней в порядке следования элементов документа и использует его как
    заголовок-показатель (имитирует первую строку Excel-листа).

    Возвращает {"Таблица_1": df, "Таблица_2": df, ...} — имена синтетические,
    т.к. .docx не имеет понятия "лист", в отличие от .xlsx.

    Распознаёт и склеивает "разорванные" таблицы — Word нередко переносит
    одну логическую таблицу на новую страницу, и она физически становится
    ОТДЕЛЬНОЙ <w:tbl> без собственной строки заголовка (первая строка такого
    продолжения уже содержит данные, а не названия колонок). Такие
    продолжения распознаются по количеству столбцов, совпадающему с
    предыдущей таблицей, и отсутствию текста в первой ячейке первой строки,
    похожего на заголовок ("Район", "№" и т.п.) — и приклеиваются к
    предыдущей таблице вместо того, чтобы стать самостоятельным блоком без
    заголовка показателя.
    """
    import docx

    doc = docx.Document(path_or_bytes)

    # Проходим по документу в порядке следования элементов (параграфы и
    # таблицы вперемешку), чтобы для каждой таблицы взять именно
    # ПРЕДШЕСТВУЮЩИЙ ей текст, а не первый параграф документа.
    body = doc.element.body
    last_paragraph_text: Optional[str] = None
    table_idx = 0
    sheets: dict[str, pd.DataFrame] = {}

    # python-docx не даёt прямого доступа "следующая таблица после параграфа X"
    # по элементам тела документа, поэтому идём по document.element.body и
    # сопоставляем встреченные <w:tbl> с doc.tables по порядку (тот же порядок,
    # в котором они физически идут в документе).
    docx_tables_iter = iter(doc.tables)

    # для склейки разорванных таблиц-продолжений
    prev_rows: Optional[list] = None
    prev_title: Optional[str] = None
    prev_key: Optional[str] = None

    def _looks_like_header_cell(value) -> bool:
        v = str(value).strip().lower() if value is not None else ""
        # _is_region_header ОБЯЗАТЕЛЕН здесь: без него "Наименование адм
        # единицы" не опознавалось как шапка, и Таблица 16 ("...на душу
        # населения") приклеивалась к Таблице 15 ("...на единицу площади")
        # как продолжение. В результате население (522171) и площадь
        # (9804316) оказывались под одним именем показателя и схлопывались
        # при дедупликации — из двух чисел в базу попадало одно.
        return (v in ("район", "районы", "№", "год", "годы")
                or "район" in v
                or _is_region_header(value))

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = "".join(node.text or "" for node in child.iter()
                          if node.tag.endswith("}t")).strip()
            if text:
                last_paragraph_text = text
        elif tag == "tbl":
            table = next(docx_tables_iter, None)
            if table is None:
                continue
            table_idx += 1

            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            if not rows:
                continue

            is_continuation = (
                prev_rows is not None
                and len(rows[0]) == len(prev_rows[0])
                and not _looks_like_header_cell(rows[0][0])
            )

            if is_continuation:
                prev_rows.extend(rows)
                continue

            # завершить предыдущую накопленную таблицу перед началом новой
            if prev_rows is not None:
                _flush_docx_table(sheets, prev_key, prev_title, prev_rows)

            title = last_paragraph_text or f"Таблица {table_idx} (без заголовка)"
            prev_rows = rows
            prev_title = title
            prev_key = f"Таблица_{table_idx}"

    if prev_rows is not None:
        _flush_docx_table(sheets, prev_key, prev_title, prev_rows)

    return sheets


def _flush_docx_table(sheets: dict, key: str, title: str, rows: list) -> None:
    """
    Финализирует накопленные строки одной (возможно склеенной из нескольких
    физических <w:tbl>) логической таблицы: пробует категориальный паттерн
    (район х категория, без годов), затем вертикальный (год в столбце),
    и только если ни один не подошёл — кладёт как обычный df для find_blocks.
    """
    n_cols = max(len(r) for r in rows)
    title_row = [title] + [None] * (n_cols - 1)
    padded_rows = [r + [None] * (n_cols - len(r)) for r in rows]
    df = pd.DataFrame([title_row] + padded_rows)

    # Категориальный паттерн проверяется на самих данных (без título-строки),
    # т.к. он ожидает "Район" в первой строке (возможно не в самом первом
    # столбце — перед ним может идти "№" с порядковым номером, как в
    # таблице расстояний до аэропортов).
    data_df = pd.DataFrame(padded_rows)
    if not data_df.empty:
        header_row = data_df.iloc[0].tolist()
        has_region_col = any(
            _is_region_header(header_row[i])
            for i in range(min(4, len(header_row)))
        )
        if has_region_col:
            # категориальная таблица обрабатывается отдельно в
            # extract_tables_from_docx через detect_categorical_table,
            # но ей нужен каталог и snapshot_year, которых здесь нет —
            # поэтому помечаем специальным ключом, который вызывающий код
            # (extract_sheets) распознает и обработает по-другому. Чтобы не
            # усложнять интерфейс, сохраняем df как есть, с пометкой в имени
            # листа — universal_detector всё равно не найдёт там блоков
            # (нет строки годов) и тихо пропустит, после чего
            # detect_categorical_table обработает его на уровне extract_sheets.
            # Заголовок-показатель ("Таблица 6 - Урожайность сельскохозяйственных
            # культур...") раньше здесь терялся: категориальная ветка клала только
            # data_df, а extract_sheets восстанавливал подсказку из имени ключа
            # ("Таблица 6"), из-за чего РАЗНЫЕ показатели с одинаковыми названиями
            # колонок ("Картофель" в таблицах урожайности и валового сбора)
            # получали неразличимые имена и не матчились с каталогом.
            data_df.attrs["indicator_title"] = title
            sheets[f"{key}__categorical"] = data_df
            return

    pivoted = detect_and_pivot_vertical_table(df)
    if pivoted:
        for col_name, pseudo_df in pivoted.items():
            sheets[f"{key}_{col_name}"] = pseudo_df
    else:
        sheets[key] = df





# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def extract_tables_from_pdf(path_or_bytes) -> dict[str, pd.DataFrame]:
    """
    Извлекает таблицы из PDF через pdfplumber, в том же формате, что и
    extract_tables_from_docx.

    Для каждой страницы: ищет текст над найденной таблицей (по координате Y)
    как кандидата на заголовок-показатель — pdfplumber отдаёт и текст, и
    таблицы с их bbox (координатами), что позволяет взять текст, физически
    расположенный непосредственно над таблицей на странице.

    Возвращает {"Страница_1_Таблица_1": df, ...}.
    """
    import pdfplumber

    sheets: dict[str, pd.DataFrame] = {}

    with pdfplumber.open(path_or_bytes) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            tables = page.find_tables()
            if not tables:
                continue

            words = page.extract_words()  # для поиска текста-заголовка над таблицей

            for t_idx, table in enumerate(tables, start=1):
                rows = table.extract()
                if not rows:
                    continue

                title = _find_title_above_table(words, table.bbox, page.height)

                n_cols = max(len(r) for r in rows)
                title_row = [title] + [None] * (n_cols - 1)
                padded_rows = [
                    [(c or "").strip() if isinstance(c, str) else c for c in r]
                    + [None] * (n_cols - len(r))
                    for r in rows
                ]
                df = pd.DataFrame([title_row] + padded_rows)

                pivoted = detect_and_pivot_vertical_table(df)
                if pivoted:
                    for col_name, pseudo_df in pivoted.items():
                        sheets[f"Страница_{page_num}_Таблица_{t_idx}_{col_name}"] = pseudo_df
                else:
                    sheets[f"Страница_{page_num}_Таблица_{t_idx}"] = df

    return sheets


def _find_title_above_table(words: list[dict], table_bbox: tuple,
                            page_height: float, max_lines: int = 2) -> str:
    """
    Среди слов страницы (из page.extract_words()) находит текст,
    расположенный непосредственно над таблицей по вертикальной координате,
    и собирает из него заголовок-кандидат (до max_lines строк текста выше
    верхней границы таблицы, ближайших к ней).

    table_bbox — (x0, top, x1, bottom) в координатах pdfplumber.
    Если над таблицей ничего не нашлось (например, таблица в самом верху
    страницы), возвращает дженерик-заглушку — блок попадёт в
    universal_detector как unmapped, что безопаснее, чем угадывать.
    """
    table_top = table_bbox[1]

    # слова выше таблицы, отсортированные по убыванию Y (ближе к таблице — первые)
    candidates = [w for w in words if w["bottom"] <= table_top]
    if not candidates:
        return "Таблица без заголовка (нужна ручная проверка)"

    candidates.sort(key=lambda w: -w["bottom"])

    # группируем по строкам (близкие значения top считаем одной строкой)
    lines: list[list[dict]] = []
    current_line: list[dict] = []
    current_top = None
    for w in candidates:
        if current_top is None or abs(w["top"] - current_top) < 3:
            current_line.append(w)
            current_top = w["top"] if current_top is None else current_top
        else:
            lines.append(current_line)
            current_line = [w]
            current_top = w["top"]
        if len(lines) >= max_lines:
            break
    if current_line and len(lines) < max_lines:
        lines.append(current_line)

    # восстанавливаем порядок слева-направо в каждой строке, строки — сверху вниз
    lines.sort(key=lambda line: min(w["top"] for w in line))
    text_lines = []
    for line in lines:
        line_sorted = sorted(line, key=lambda w: w["x0"])
        text_lines.append(" ".join(w["text"] for w in line_sorted))

    title = " ".join(text_lines).strip()
    return title or "Таблица без заголовка (нужна ручная проверка)"


# ---------------------------------------------------------------------------
# DOC (старый формат Word 97-2003) — конвертация в DOCX через LibreOffice
# ---------------------------------------------------------------------------

# LibreOffice headless — единственный надёжный способ читать .doc без
# собственного парсера бинарного формата Composite Document File V2.
# python-docx умеет только .docx (Office Open XML), не старый бинарный .doc.
#
# Ограничения:
#   - Требует установленный libreoffice/soffice в окружении (см. требование
#     ниже в _convert_doc_to_docx — явная проверка с понятной ошибкой,
#     если бинарника нет, вместо непонятного FileNotFoundError из subprocess).
#   - Конвертация занимает заметное время (наблюдалось до нескольких секунд
#     на разовый запуск) — для ZIP-архивов с большим количеством .doc файлов
#     это может стать узким местом; таймаут ограничивает каждую конвертацию.
#   - LibreOffice запускает отдельный процесс — на сервере с малым объёмом
#     RAM (наблюдалось ~300 МБ MemAvailable в этом проекте) параллельные
#     конвертации нескольких .doc файлов МОГУТ исчерпать память. Поэтому
#     конвертация — синхронная, один файл за раз, как и обработка ZIP-архивов.
DOC_CONVERT_TIMEOUT_SECONDS = 60


def _convert_doc_to_docx(content: bytes) -> bytes:
    """
    Конвертирует .doc (бинарный Word 97-2003) в .docx через LibreOffice
    headless, возвращает байты получившегося .docx.

    Бросает RuntimeError с понятным сообщением, если LibreOffice не
    установлен или конвертация не успела за DOC_CONVERT_TIMEOUT_SECONDS —
    тогда вызывающий код (upload_parser.py) вернёт это как HTTPException
    с понятным текстом, а не уронит процесс с трудно читаемой ошибкой
    subprocess.
    """
    import shutil
    import subprocess
    import tempfile

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) не найден в окружении контейнера — "
            "конвертация .doc в .docx невозможна. Установи пакет "
            "libreoffice (apt-get install libreoffice-writer) в Dockerfile "
            "fastapi-контейнера, либо сконвертируй файл в .docx вручную "
            "перед загрузкой."
        )

    with tempfile.TemporaryDirectory(prefix="doc_convert_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        src_path = tmp_path / "input.doc"
        src_path.write_bytes(content)

        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "docx",
                 "--outdir", str(tmp_path), str(src_path)],
                timeout=DOC_CONVERT_TIMEOUT_SECONDS,
                capture_output=True,
                check=True,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Конвертация .doc в .docx превысила лимит "
                f"{DOC_CONVERT_TIMEOUT_SECONDS} секунд — файл слишком "
                "большой/сложный или LibreOffice зависает."
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"LibreOffice не смог сконвертировать файл: "
                f"{e.stderr.decode('utf-8', errors='replace')[:300]}"
            )

        converted_path = tmp_path / "input.docx"
        if not converted_path.exists():
            raise RuntimeError(
                "LibreOffice завершился без ошибки, но .docx файл не "
                "появился — возможно файл повреждён или не является "
                "настоящим Word-документом."
            )

        return converted_path.read_bytes()


# ---------------------------------------------------------------------------
# Единая точка входа по расширению файла
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Единая точка входа по расширению файла
# ---------------------------------------------------------------------------

def _decode_text(content: bytes) -> str:
    """
    Текст CSV в неизвестной кодировке.

    Казахстанские выгрузки приходят и в UTF-8, и в CP1251 (русский Excel
    сохраняет так по умолчанию). Раньше файл читался жёстко как UTF-8 и
    CP1251 просто падал с UnicodeDecodeError, не дойдя до парсера.

    Порядок важен: UTF-8 проверяется первым, потому что CP1251 —
    однобайтовая кодировка и "успешно" декодирует почти любые байты,
    превращая кириллицу UTF-8 в мусор.
    """
    for enc in ("utf-8-sig", "utf-8", "cp1251", "koi8-r"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(content).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return content.decode("utf-8", errors="replace")


def _sniff_delimiter(text: str) -> str:
    """
    Разделитель CSV. Русская локаль Excel пишет ';', международная ','.
    Без определения файл с ';' читался как ОДНА колонка — таблица
    существовала, но структуры в ней не было, и парсер возвращал ноль строк.
    """
    import csv as _csv
    head = "\n".join(text.splitlines()[:20])
    if not head.strip():
        return ","
    try:
        return _csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except Exception:
        counts = {d: head.count(d) for d in (",", ";", "\t", "|")}
        top = max(counts, key=counts.get)
        return top if counts[top] else ","


def _route_table(df, catalog, snapshot_year):
    """
    Куда отправить одну таблицу: в find_blocks (район x года) или в
    detect_categorical_table (район x категория без временного ряда).

    Та же логика, что и в ветке xlsx. Раньше CSV её не проходил вообще —
    возвращался как есть, и таблицы без строки годов давали ноль строк.
    Возвращает (sheets_dict, categorical_rows).
    """
    from universal_detector import find_blocks
    if df is None or df.empty:
        return {"Sheet1": df}, []
    if find_blocks(df):
        return {"Sheet1": df}, []
    cat = detect_categorical_table(df, catalog, snapshot_year)
    if cat:
        return {}, cat
    return {"Sheet1": df}, []


def extract_sheets(
    filename: str,
    content: bytes,
    catalog=None,
    snapshot_year: Optional[int] = None,
) -> tuple[dict[str, pd.DataFrame], list[dict]]:
    """
    Определяет формат по расширению имени файла и вызывает нужный адаптер.
    Для .xlsx/.xls/.csv просто использует pandas как обычно — это покрывает
    случай, когда вызывающий код не различает форматы заранее и просто
    хочет "дай мне dict листов для любого поддерживаемого файла".

    Возвращает (sheets, categorical_rows):
      - sheets — dict[str, DataFrame] для передачи в
        universal_detector.convert_sheets() как обычно (район х года).
      - categorical_rows — список уже готовых DSS-записей (район х категория,
        без временного ряда — например, расстояния до инфраструктуры),
        извлечённых через detect_categorical_table. Пустой список для
        Excel/CSV — этот паттерн пока распознаётся только в DOCX/DOC/PDF,
        где госстатистика чаще оформляет такие таблицы текстом, не Excel.

    catalog и snapshot_year обязательны, только если в документе реально
    есть категориальные таблицы (детектируются автоматически по строке
    "Район" в первой ячейке) — если их нет, оба параметра можно не передавать.
    Если категориальная таблица найдена, а catalog не передан — её строки
    просто не попадут в результат (см. лог через log_fn у вызывающего кода),
    а не вызовут исключение, чтобы не ломать уже работающий путь для файлов
    без такого паттерна.
    """
    import io

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("xlsx", "xls"):
        raw = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
        if catalog is None or snapshot_year is None:
            return raw, []
        from universal_detector import find_blocks
        sheets_out = {}
        cat_rows = []
        for _name, _df in raw.items():
            if _df is None or _df.empty:
                sheets_out[_name] = _df
                continue
            if find_blocks(_df):
                sheets_out[_name] = _df
                continue

            # Вертикальная таблица: год в первом столбце, показатели в
            # колонках ("Год | Поголовье КРС | Поголовье коров"). Функция
            # detect_and_pivot_vertical_table существовала, но вызывалась
            # только для docx и pdf — для Excel такие листы молча уходили
            # в ноль строк, хотя структура полностью распознаваема.
            _piv = detect_and_pivot_vertical_table(_df)
            if _piv:
                for _col, _pdf in _piv.items():
                    sheets_out[f"{_name}_{_col}"] = _pdf
                continue

            _cat = detect_categorical_table(_df, catalog, snapshot_year)
            if _cat:
                cat_rows.extend(_cat)
            else:
                sheets_out[_name] = _df
        return sheets_out, cat_rows
    if ext == "csv":
        text = _decode_text(content)
        sep = _sniff_delimiter(text)
        try:
            df = pd.read_csv(io.StringIO(text), header=None, sep=sep,
                             engine="python", skip_blank_lines=False)
        except Exception:
            df = pd.read_csv(io.StringIO(text), header=None, sep=None,
                             engine="python")
        if catalog is None or snapshot_year is None:
            return {"Sheet1": df}, []
        return _route_table(df, catalog, snapshot_year)

    if ext in ("docx", "doc"):
        if ext == "doc":
            content = _convert_doc_to_docx(content)
        raw_sheets = extract_tables_from_docx(io.BytesIO(content))
    elif ext == "pdf":
        raw_sheets = extract_tables_from_pdf(io.BytesIO(content))
    else:
        raise ValueError(
            f"Неподдерживаемый формат файла: .{ext}. "
            "Поддерживаются: xlsx, xls, csv, doc, docx, pdf"
        )

    sheets: dict[str, pd.DataFrame] = {}
    categorical_rows: list[dict] = []

    for name, df in raw_sheets.items():
        if name.endswith("__categorical"):
            if catalog is not None and snapshot_year is not None:
                # indicator_title для категориальной таблицы — последний
                # известный родовой заголовок не сохраняется отдельно при
                # склейке в _flush_docx_table, поэтому используем сам
                # синтетический ключ листа как минимально содержательную
                # подсказку (лучше, чем None — хотя бы заметно в comment
                # несматченных строк, что это конкретная категориальная
                # таблица из файла, а не голый "unmapped").
                title_hint = df.attrs.get("indicator_title") or \
                    name.replace("__categorical", "").replace("_", " ")
                categorical_rows.extend(
                    detect_categorical_table(df, catalog, snapshot_year, title_hint)
                )
            continue
        sheets[name] = df

    if categorical_rows:
        _n = _disambiguate_categorical(categorical_rows)
        if _n:
            print(f"[ОХВАТ] уточнено имён у {_n} строк (разные таблицы -> один показатель)")

    return sheets, categorical_rows
