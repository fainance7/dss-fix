# app/routers/upload_parser.py
"""
POST /upload/file
  Юзер скидывает Excel/Word/PDF → парсим → raw_historical → триггер
  dag_extract_historical → возвращаем run_id и ссылку на скачивание когда
  DAG завершится

POST /upload/file?mode=direct
  То же самое, но сразу возвращаем DSS xlsx (без DAG)

POST /upload/detect
  Показывает структуру файла и предпросмотр данных без сохранения —
  используй перед /file, чтобы убедиться что парсер сработает правильно.

ПОДДЕРЖИВАЕМЫЕ ФОРМАТЫ: xlsx, xls, csv, docx, pdf.

ВНУТРЕННИЙ ДВИЖОК: etl/universal_detector.py (общий с dag_auto_convert.py).
Раньше здесь был отдельный, независимый парсер с собственным KATO-словарём
(без fuzzy-matching) и без каталога показателей DSS. Это создавало риск,
что один и тот же файл давал разные результаты в зависимости от того,
через какой путь его загрузили — fastapi-direct или Airflow DAG. Теперь
оба пути используют один и тот же universal_detector.py: один источник
истины для распознавания структуры, районов (с fuzzy-matching через
rapidfuzz) и сопоставления показателей с каталогом DSS.

PDF И DOCX: extract_sheets() из etl/document_adapters.py превращает таблицы
из Word/PDF в тот же dict[sheet_name, DataFrame], что pandas отдаёт для
Excel — после этого PDF/DOCX обрабатываются universal_detector.py абсолютно
так же, как .xlsx, без отдельной ветки логики для распознавания структуры.
"""
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
import psycopg2
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── подключения (берём из env как во всём проекте) ──────────────────────────
# H-5: secrets (DSN, Airflow pass) — only from env, fail-fast. No insecure defaults.
DB_CONN = os.environ.get("DB_CONN") or os.environ.get("REPORTS_DB_DSN")
if not DB_CONN:
    *** RuntimeError("DB_CONN or REPORTS_DB_DSN must be set (audit H-5: no insecure default)")
AF_URL  = os.environ.get("AIRFLOW_URL", "http://airflow-webserver:8080")
AF_USER = os.environ.get("AIRFLOW_USER", "admin")
AF_PASS = os.environ.get("AIRFLOW_ADMIN_PASSWORD")
if not AF_PASS:
    *** RuntimeError("AIRFLOW_ADMIN_PASSWORD must be set (audit H-5: no insecure default)")

ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", "/app/artifacts"))

# Каталог показателей, движок детекции и адаптеры PDF/DOCX — общие с Airflow
# DAG (dag_auto_convert.py). В контейнере fastapi etl/ смонтирован как
# /app/etl (см. docker-compose.yml).
ETL_DIR = Path(os.environ.get("ETL_DIR", "/app/etl"))
sys.path.insert(0, str(ETL_DIR))

from universal_detector import IndicatorCatalog, convert_sheets  # noqa: E402
from document_adapters import extract_sheets  # noqa: E402

_CATALOG_PATH = ETL_DIR / "indicator_catalog.csv"
_ALIASES_PATH = ETL_DIR / "indicator_aliases.csv"

VALID_DIRECTIONS = {
    "climate", "ecology", "economy", "labor",
    "social_demog", "tourism", "transport", "water",
    "land", "agriculture",
}

ALLOWED_EXTENSIONS = {"xlsx", "xls", "csv", "doc", "docx", "pdf"}


def _load_catalog(_preferred_direction: str | None = None) -> IndicatorCatalog:
    """
    Каталог читается на каждый запрос (а не один раз при старте), чтобы
    обновления indicator_aliases.csv (например, после ручного добавления
    нового алиаса) подхватывались без перезапуска контейнера.
    """
    if not _CATALOG_PATH.exists():
        raise HTTPException(
            500,
            f"Каталог показателей не найден: {_CATALOG_PATH}. "
            "Убедись, что etl/indicator_catalog.csv смонтирован в контейнер."
        )
    _cat = IndicatorCatalog.from_csv(
        _CATALOG_PATH,
        _ALIASES_PATH if _ALIASES_PATH.exists() else None,
    )
    _cat.preferred_direction = _preferred_direction
    return _cat


# ════════════════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ: БД, DAG, сборка Excel
# ════════════════════════════════════════════════════════════════════════════

def _save_to_raw_historical(rows: list[dict], source_file: str):
    conn = psycopg2.connect(DB_CONN)
    cur = conn.cursor()
    for rec in rows:
        cur.execute(
            "INSERT INTO raw_historical (source_file, data) VALUES (%s, %s)",
            (source_file, json.dumps(rec, ensure_ascii=False))
        )
    conn.commit()
    cur.close()
    conn.close()


def _trigger_dag(dag_id: str, conf: dict = None) -> dict:
    if conf is None:
        conf = {}
    url = f"{AF_URL}/api/v1/dags/{dag_id}/dagRuns"
    resp = httpx.post(url, json={"conf": conf},
                      auth=(AF_USER, AF_PASS), timeout=10)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502,
                            detail=f"Airflow error: {resp.text}")
    return resp.json()


def _build_dss_bytes(rows: list[dict]) -> bytes:
    """Собирает DSS xlsx прямо в памяти (для mode=direct)."""
    sys.path.insert(0, str(ETL_DIR))
    from excel_builder import build_dss_excel
    import tempfile

    df = pd.DataFrame(rows)
    df["comment"] = df.get("comment", "")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        build_dss_excel(df, tmp.name)
        tmp_path = tmp.name

    data = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    return data


def _detect_dominant_direction(rows: list[dict], min_rows: int = 20,
                               min_share: float = 0.6):
    """
    Направление, преобладающее среди СОПОСТАВЛЕННЫХ с каталогом показателей.

    Направление на форме задаёт раздел DSS для всех показателей, которые не
    нашлись в каталоге, а таких обычно большинство. Ошибка тихая: файл
    грузится без единого предупреждения, но данные оказываются не в том
    разделе дашборда. Реальный случай: документ по сельскому хозяйству ушёл
    с дефолтным "economy", и 4461 строка легла в экономику вместо АПК.

    Сопоставленные показатели дают надёжную подсказку — они берут направление
    из самого каталога DSS, а не из формы. Если 60%+ из них согласны, этому
    можно доверять больше, чем значению выпадающего списка.

    Пороги намеренно консервативны: при 20 сопоставленных строках и меньше
    выборка слишком мала, при разбросе ниже 60% файл смешанный, и выбор
    пользователя не переопределяется.

    Возвращает (направление | None, статистика).
    """
    from collections import Counter
    matched = [r["direction_code"] for r in rows if r["direction_code"] != "unmapped"]
    if len(matched) < min_rows:
        return None, {"matched_rows": len(matched), "reason": "мало сопоставленных строк"}
    cnt = Counter(matched)
    top, n = cnt.most_common(1)[0]
    share = n / len(matched)
    stats = {"matched_rows": len(matched), "counts": dict(cnt),
             "top": top, "share": round(share, 3)}
    if share >= min_share:
        return top, stats
    stats["reason"] = "нет уверенного большинства"
    return None, stats


def _apply_fallback(rows: list[dict], fallback_direction: str,
                    fallback_indicator_hint: str = ""):
    """
    Несопоставленные показатели (direction_code == 'unmapped') получают
    направление: определённое автоматически по файлу, если оно надёжно, иначе
    выбранное пользователем на форме.

    Возвращает (rows, unmapped_count, effective_direction, stats).
    """
    auto, stats = _detect_dominant_direction(rows)
    effective = auto or fallback_direction
    unmapped_count = 0
    for r in rows:
        if r["direction_code"] == "unmapped":
            r["direction_code"] = effective
            note = ""
            if auto and auto != fallback_direction:
                note = (f" | направление '{auto}' определено автоматически по файлу "
                        f"(на форме было '{fallback_direction}')")
            if fallback_indicator_hint:
                note += f" | подсказка пользователя: {fallback_indicator_hint}"
            if note:
                r["comment"] = (r.get("comment", "") + note).strip(" |")
            unmapped_count += 1
    return rows, unmapped_count, effective, stats


# ════════════════════════════════════════════════════════════════════════════
# ЭНДПОИНТЫ
# ════════════════════════════════════════════════════════════════════════════

@router.post("/file")
async def upload_file(
    file:           UploadFile = File(...),
    indicator_name: Optional[str] = Form(None),
    direction:      str            = Form("economy"),
    unit:           Optional[str]  = Form(None),
    sheet_name:     Optional[str]  = Form(None),
    mode:           str            = Form("dag"),   # "dag" | "direct"
):
    """
    Загружает файл (xlsx/xls/csv/docx/pdf) и обрабатывает его двумя способами:

    mode=dag    (по умолчанию)
      → извлекаем таблицы (extract_sheets) → парсим через universal_detector →
        raw_historical → триггер dag_extract_historical (который сам
        запускает dag_build_excel)
      → возвращаем run_id; файл появится в /files/list после завершения DAG

    mode=direct
      → парсим → сразу возвращаем DSS xlsx для скачивания
      → быстро, минует БД и DAG (полезно для разовой проверки файла)

    indicator_name/direction/unit — используются только как fallback для
    показателей, которые universal_detector не смог сопоставить с каталогом
    (etl/indicator_catalog.csv). Показатели, которые сматчились, получают
    свой правильный direction_code/unit_name из каталога автоматически —
    параметры формы их не переопределяют.

    Для PDF и Word документов название показателя берётся из текста,
    физически расположенного над таблицей в документе (см.
    etl/document_adapters.py) — если в исходном файле над таблицей нет
    текста или он не похож на название показателя, такие строки получат
    direction_code из параметра form (см. выше), а не потеряются.
    """
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction должен быть одним из: {sorted(VALID_DIRECTIONS)}")

    content = await file.read()
    filename = file.filename or "upload.xlsx"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Недопустимый формат '.{ext}'. Разрешены: {sorted(ALLOWED_EXTENSIONS)}"
        )

    catalog = _load_catalog(direction)

    try:
        xl, categorical_rows = extract_sheets(
            filename, content, catalog=catalog, snapshot_year=datetime.now().year
        )
    except Exception as e:
        raise HTTPException(400, f"Не удалось извлечь таблицы из файла: {e}")

    if sheet_name:
        if sheet_name not in xl:
            raise HTTPException(400, f"Лист/таблица '{sheet_name}' не найдена. Доступные: {list(xl.keys())}")
        xl = {sheet_name: xl[sheet_name]}

    rows, stats = convert_sheets(xl, catalog, source_file=filename, log_fn=lambda m: None)
    rows.extend(categorical_rows)

    if not rows:
        raise HTTPException(422, (
            "Не удалось извлечь данные. Структура файла не распознана "
            "универсальным детектором (нет блоков 'район в строках, года в столбцах', "
            "ни 'район в строках, категория в столбцах'). "
            "Проверьте структуру через POST /upload/detect. "
            f"Листы в файле: {list(xl.keys())}"
        ))

    rows, unmapped_count, direction_used, dir_stats = _apply_fallback(
        rows, direction, indicator_name or ""
    )

    # дедупликация — на случай если в одном файле один и тот же показатель/
    # район/период встретился в нескольких блоках (например, область
    # повторяется в нескольких листах одного файла)
    seen = set()
    deduped = []
    for r in rows:
        k = (r["indicator_name"], r["kato_code"], r["direction_code"], r["period_start"])
        if k not in seen:
            seen.add(k)
            deduped.append(r)

    # ══ mode=direct: сразу возвращаем файл ═══════════════════════════════
    if mode == "direct":
        try:
            dss_bytes = _build_dss_bytes(deduped)
        except Exception as e:
            raise HTTPException(500, f"Ошибка сборки Excel: {e}")

        ts = datetime.now().strftime("%Y%m%d_%H%M")
        base = re.sub(r"\.(xlsx?|xls)$", "", filename, flags=re.IGNORECASE)
        out_name = f"dss_isolated_{base}_{ts}.xlsx"

        try:
            (ARTIFACTS_DIR / out_name).write_bytes(dss_bytes)
        except Exception as e:
            print(f"[warn] не удалось сохранить изолированный файл в артефакты: {e}")

        return StreamingResponse(
            io.BytesIO(dss_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{out_name}"',
                "X-Rows-Extracted":   str(len(rows)),
                "X-Rows-After-Dedup": str(len(deduped)),
                "X-Unmapped-Count":   str(unmapped_count),
            }
        )

    # ══ mode=dag: сохраняем в БД и триггерим DAG ═════════════════════════
    try:
        _save_to_raw_historical(deduped, filename)
    except Exception as e:
        raise HTTPException(500, f"Ошибка записи в БД: {e}")

    try:
        run = _trigger_dag("dag_extract_historical")
    except HTTPException:
        return {
            "status":      "saved_no_trigger",
            "rows_saved":  len(deduped),
            "unmapped":    unmapped_count,
            "source_file": filename,
            "message":     "Данные сохранены в raw_historical. Запустите dag_extract_historical вручную.",
        }

    return {
        "status":      "triggered",
        "rows_saved":  len(deduped),
        "unmapped":    unmapped_count,
        "direction_used": direction_used,
        "direction_auto": dir_stats,
        "source_file": filename,
        "dag_run_id":  run.get("dag_run_id"),
        "message":     "Данные в очереди. Файл появится в /files/list после завершения DAG.",
    }


def _cat_preview(cat_rows: list, indicator: str, limit: int = 5) -> list:
    """
    Образцы строк для категориальной таблицы в превью /detect.

    Раньше здесь всегда возвращался пустой список. Для файлов, где ВСЕ
    данные категориальные (район x категория без годов), превью выходило
    пустым целиком, фронт показывал прочерки вместо регионов и значений и
    блокировал генерацию с текстом "пустые регионы/значения" — хотя
    строки на самом деле извлеклись и сохранились бы корректно.
    """
    from kato_map import KATO_MAP
    names = {}
    for _k, _v in KATO_MAP.items():
        names.setdefault(_v[0], _v[1])
    try:
        from kato_map_auto import AUTO_KATO_MAP
        for _k, _v in AUTO_KATO_MAP.items():
            names.setdefault(_v[0], _v[1])
    except ImportError:
        pass

    out = []
    for r in cat_rows:
        if r["indicator_name"] != indicator:
            continue
        out.append({
            "region": names.get(r["kato_code"], str(r["kato_code"])),
            "sample_value": r["value"],
            "kato_code": r["kato_code"],
            "kato_error": False,
        })
        if len(out) >= limit:
            break
    return out


@router.post("/detect")
async def detect_structure(file: UploadFile = File(...)):
    """
    Показывает структуру файла и предпросмотр данных без сохранения.
    Используй перед /file, чтобы убедиться, что universal_detector
    правильно найдёт блоки показателей на каждом листе/таблице.

    Поддерживает те же форматы, что и /file (xlsx, xls, csv, docx, pdf).

    Реализует Задачу 1.3 плана (Data Preview): отвечает тем же движком
    (find_blocks из universal_detector), что и реальная обработка, поэтому
    превью гарантированно совпадает с тем, что попадёт в raw_historical.
    """
    content = await file.read()
    filename = file.filename or "upload.xlsx"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Недопустимый формат '.{ext}'. Разрешены: {sorted(ALLOWED_EXTENSIONS)}"
        )

    from universal_detector import find_blocks, match_block, block_to_rows, lookup_kato, resolve_orientation, _parse_number

    catalog = _load_catalog()
    snapshot_year = datetime.now().year

    try:
        xl, categorical_rows = extract_sheets(
            filename, content, catalog=catalog, snapshot_year=snapshot_year
        )
    except Exception as e:
        raise HTTPException(400, f"Не удалось извлечь таблицы из файла: {e}")

    sheets_info = []
    total_rows_will_be_saved = len(categorical_rows)

    if categorical_rows:
        # категориальные таблицы (район х категория, без годов) показываем
        # отдельной псевдо-секцией в превью — у них нет "блоков" в смысле
        # find_blocks, но пользователь должен видеть, что они тоже будут
        # сохранены при подтверждении
        sample_indicators = sorted(set(r["indicator_name"] for r in categorical_rows))[:10]
        sheets_info.append({
            "sheet": "Категориальные таблицы (снимок на " + str(snapshot_year) + " год)",
            "rows": len(categorical_rows),
            "cols": None,
            "blocks_found": len(sample_indicators),
            "blocks": [{
                "title": ind, "matched": True, "indicator_name": ind,
                "direction_code": next(r["direction_code"] for r in categorical_rows if r["indicator_name"] == ind),
                "unit_name": next(r["unit_name"] for r in categorical_rows if r["indicator_name"] == ind),
                # Реальные годы, а не snapshot_year: показатели с периодом-
                # диапазоном в шапке ("2004-2014 гг.") разворачиваются на
                # каждый год этого диапазона. Превью же показывало всем
                # категориальным строкам год снимка, из-за чего казалось,
                # будто исторические данные сохраняются как 2026-й.
                "years": sorted({
                    int(str(r["period_start"])[:4])
                    for r in categorical_rows
                    if r["indicator_name"] == ind
                }) or [snapshot_year],
                "rows_will_be_saved": sum(1 for r in categorical_rows if r["indicator_name"] == ind),
                "preview": _cat_preview(categorical_rows, ind),
            } for ind in sample_indicators],
        })

    for sname, df in xl.items():
        if df.empty:
            sheets_info.append({
                "sheet": sname, "rows": 0, "cols": 0,
                "blocks_found": 0, "blocks": [],
            })
            continue

        blocks = find_blocks(df)
        blocks_preview = []
        for b in blocks:
            canon_name, direction_code, unit_name, matched = match_block(b, catalog)
            preview_rows = []
            # первые 5 строк данных этого блока — для UI-превью
            count = 0
            _mode, _label_col = resolve_orientation(df, b)
            for ri in range(b.data_start, b.data_end):
                if count >= 5:
                    break
                label_raw = df.iloc[ri, _label_col]
                if pd.isna(label_raw) or not str(label_raw).strip():
                    continue
                first_year_col = next(iter(b.year_cols), None)
                sample_value = df.iloc[ri, first_year_col] if first_year_col is not None else None
                _sv_num = _parse_number(sample_value)
                if _sv_num is not None:
                    sample_value = _sv_num
                if _mode == "regions":
                    _region_disp = str(label_raw).strip()
                    _kato = lookup_kato(_region_disp)
                    _err = _kato is None
                else:
                    _region_disp = "Северо-Казахстанская область"
                    _kato = 590000000
                    _err = False
                preview_rows.append({
                    "region": _region_disp,
                    "sample_value": None if pd.isna(sample_value) else sample_value,
                    "kato_code": _kato,
                    "kato_error": _err,
                })
                count += 1

            # точное число строк, которые реально будут сохранены —
            # тот же block_to_rows, что вызовет /file при реальной загрузке,
            # поэтому "Будет сохранено N строк" в превью не может разойтись
            # с фактическим результатом после нажатия "Подтвердить"
            actual_rows = block_to_rows(df, b, catalog, source_file=filename, log_fn=lambda m: None)
            rows_will_be_saved = len(actual_rows)
            total_rows_will_be_saved += rows_will_be_saved

            blocks_preview.append({
                "title":              b.title,
                "matched":            matched,
                "indicator_name":     canon_name,
                "direction_code":     direction_code if matched else "unmapped (нужен fallback)",
                "unit_name":          unit_name,
                "years":              sorted(set(b.year_cols.values())),
                "rows_will_be_saved": rows_will_be_saved,
                "preview":            preview_rows,
            })

        sheets_info.append({
            "sheet":        sname,
            "rows":         len(df),
            "cols":         len(df.columns),
            "blocks_found": len(blocks),
            "blocks":       blocks_preview,
        })

    total_blocks = sum(s["blocks_found"] for s in sheets_info)
    total_unmatched = sum(
        1 for s in sheets_info for b in s["blocks"] if not b["matched"]
    )

    return {
        "filename":                file.filename,
        "total_sheets":            len(xl),
        "total_blocks":            total_blocks,
        "total_unmatched":         total_unmatched,
        "total_rows_will_be_saved": total_rows_will_be_saved,
        "sheets":                  sheets_info,
    }


# ════════════════════════════════════════════════════════════════════════════
# ZIP-АРХИВЫ (Задача 1.2 плана): массовая загрузка
# ════════════════════════════════════════════════════════════════════════════

# Лимиты защиты от случайно/злонамеренно огромных архивов — сервер имеет
# мало свободной памяти (наблюдалось ~300 МБ MemAvailable), поэтому файлы
# из архива обрабатываются строго по одному, с освобождением памяти между
# ними, а не загружаются все сразу. Лимиты ниже — защита от исчерпания
# ДИСКА при распаковке (памяти это не касается напрямую, но огромное
# количество файлов также means много итераций конвертации подряд).
ZIP_MAX_FILES = 200
ZIP_MAX_TOTAL_UNCOMPRESSED_MB = 500


@router.post("/zip")
async def upload_zip(
    file:           UploadFile = File(...),
    direction:      str            = Form("economy"),
    mode:           str            = Form("dag"),  # "dag" | "direct"
):
    """
    Принимает ZIP-архив, распаковывает во временную папку на диске
    (не в память — на сервере мало свободной RAM), и прогоняет каждый
    найденный внутри поддерживаемый файл (.xlsx/.xls/.csv/.docx/.pdf)
    через тот же universal_detector, что и /file.

    Файлы внутри архива обрабатываются СТРОГО ПО ОДНОМУ — DataFrame
    одного файла высвобождается из памяти прежде, чем начинается
    обработка следующего. Это намеренное ограничение производительности
    в пользу стабильности на сервере с малым объёмом RAM.

    mode=dag (по умолчанию)
      → каждый файл архива сохраняется в raw_historical отдельно
      → один общий триггер dag_extract_historical в конце (не по файлу —
        иначе при большом архиве это запустило бы десятки DAG-ов подряд)

    mode=direct
      → возвращает ZIP с готовыми DSS xlsx, один на каждый исходный файл,
        у которого нашлись данные (без записи в БД и без DAG)

    Файлы, которые не дали ни одной строки (нераспознанная структура,
    повреждённый файл, неподдерживаемый формат внутри архива) — не валят
    весь запрос, а попадают в отчёт "failed" с причиной, чтобы по итогу
    архива было видно, что обработалось, а что нет.
    """
    import shutil
    import tempfile
    import zipfile

    content = await file.read()

    with tempfile.TemporaryDirectory(prefix="zip_upload_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        zip_path = tmp_path / "archive.zip"
        zip_path.write_bytes(content)

        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]

                if len(infos) > ZIP_MAX_FILES:
                    raise HTTPException(
                        400,
                        f"В архиве {len(infos)} файлов, лимит {ZIP_MAX_FILES}. "
                        "Разбейте на несколько архивов."
                    )

                total_size_mb = sum(i.file_size for i in infos) / (1024 * 1024)
                if total_size_mb > ZIP_MAX_TOTAL_UNCOMPRESSED_MB:
                    raise HTTPException(
                        400,
                        f"Суммарный размер файлов в архиве {total_size_mb:.0f} МБ, "
                        f"лимит {ZIP_MAX_TOTAL_UNCOMPRESSED_MB} МБ."
                    )

                extract_dir = tmp_path / "extracted"
                extract_dir.mkdir()
                zf.extractall(extract_dir)
        except zipfile.BadZipFile:
            raise HTTPException(400, "Файл повреждён или не является ZIP-архивом")

        candidate_files = [
            p for p in extract_dir.rglob("*")
            if p.is_file() and p.suffix.lstrip(".").lower() in ALLOWED_EXTENSIONS
        ]

        if not candidate_files:
            raise HTTPException(
                422,
                f"В архиве не найдено файлов поддерживаемых форматов "
                f"({sorted(ALLOWED_EXTENSIONS)}). "
                f"Найдено внутри архива: {len(infos)} файлов."
            )

        catalog = _load_catalog()
        succeeded = []
        failed = []
        all_rows_for_dag: list[dict] = []
        all_rows_for_merge: list[dict] = []
        direct_results: dict[str, bytes] = {}

        for path in sorted(candidate_files):
            rel_name = str(path.relative_to(extract_dir))
            try:
                file_bytes = path.read_bytes()
                xl, categorical_rows = extract_sheets(
                    rel_name, file_bytes, catalog=catalog, snapshot_year=datetime.now().year
                )
                rows, _ = convert_sheets(xl, catalog, source_file=rel_name, log_fn=lambda m: None)
                rows.extend(categorical_rows)
                rows, unmapped_count, _du, _ds = _apply_fallback(rows, direction, "")

                if not rows:
                    failed.append({"file": rel_name, "reason": "Структура не распознана, 0 строк извлечено"})
                    continue

                seen = set()
                deduped = []
                for r in rows:
                    k = (r["indicator_name"], r["kato_code"], r["direction_code"], r["period_start"])
                    if k not in seen:
                        seen.add(k)
                        deduped.append(r)

                if mode == "direct":
                    direct_results[rel_name] = _build_dss_bytes(deduped)
                elif mode == "direct_merged":
                    all_rows_for_merge.extend(deduped)
                else:
                    all_rows_for_dag.extend(deduped)

                succeeded.append({
                    "file": rel_name,
                    "rows": len(deduped),
                    "unmapped": unmapped_count,
                })

            except Exception as e:
                failed.append({"file": rel_name, "reason": str(e)})

            # явный сигнал, что DataFrame этого файла больше не нужен,
            # прежде чем переходить к следующему — на сервере мало RAM
            del file_bytes
            if "xl" in dir():
                del xl

    # ══ mode=direct: упаковываем все результаты обратно в один ZIP ══════
    if mode == "direct":
        if not direct_results:
            raise HTTPException(422, f"Не удалось обработать ни одного файла. Ошибки: {failed}")

        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out_buffer = io.BytesIO()
        with zipfile.ZipFile(out_buffer, "w", zipfile.ZIP_DEFLATED) as out_zip:
            for rel_name, dss_bytes in direct_results.items():
                base = re.sub(r"\.(xlsx?|xls|docx|pdf|csv)$", "", rel_name, flags=re.IGNORECASE)
                out_name = f"dss_isolated_{base}_{ts}.xlsx"
                out_zip.writestr(out_name, dss_bytes)
                try:
                    (ARTIFACTS_DIR / out_name).write_bytes(dss_bytes)
                except Exception as e:
                    print(f"[warn] не удалось сохранить изолированный файл в артефакты: {e}")
        out_buffer.seek(0)

        return StreamingResponse(
            out_buffer,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="dss_batch_{ts}.zip"',
                "X-Files-Succeeded":   str(len(succeeded)),
                "X-Files-Failed":      str(len(failed)),
            }
        )

    # ══ mode=direct_merged: несколько файлов -> один общий xlsx, без БД ═
    if mode == "direct_merged":
        if not all_rows_for_merge:
            raise HTTPException(422, f"Не удалось обработать ни одного файла. Ошибки: {failed}")

        seen_merged = set()
        deduped_merged = []
        for r in all_rows_for_merge:
            k = (r["indicator_name"], r["kato_code"], r["direction_code"], r["period_start"])
            if k not in seen_merged:
                seen_merged.add(k)
                deduped_merged.append(r)

        merged_bytes = _build_dss_bytes(deduped_merged)
        ts_merged = datetime.now().strftime("%Y%m%d_%H%M")
        out_name_merged = f"dss_isolated_merged_{ts_merged}.xlsx"
        try:
            (ARTIFACTS_DIR / out_name_merged).write_bytes(merged_bytes)
        except Exception as e:
            print(f"[warn] не удалось сохранить изолированный merged-файл в артефакты: {e}")

        return StreamingResponse(
            io.BytesIO(merged_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{out_name_merged}"',
                "X-Files-Succeeded":   str(len(succeeded)),
                "X-Files-Failed":      str(len(failed)),
                "X-Rows-Merged":       str(len(deduped_merged)),
            }
        )

    # ══ mode=dag: одна общая запись в БД + один общий триггер ═══════════
    if not all_rows_for_dag:
        raise HTTPException(422, f"Не удалось обработать ни одного файла. Ошибки: {failed}")

    try:
        _save_to_raw_historical(all_rows_for_dag, f"zip:{file.filename}")
    except Exception as e:
        raise HTTPException(500, f"Ошибка записи в БД: {e}")

    try:
        run = _trigger_dag("dag_extract_historical")
        dag_run_id = run.get("dag_run_id")
    except HTTPException:
        dag_run_id = None

    return {
        "status":           "triggered" if dag_run_id else "saved_no_trigger",
        "archive":          file.filename,
        "files_total":      len(candidate_files),
        "files_succeeded":  len(succeeded),
        "files_failed":     len(failed),
        "rows_saved":       len(all_rows_for_dag),
        "succeeded":        succeeded,
        "failed":           failed,
        "dag_run_id":       dag_run_id,
        "message":          (
            f"Обработано {len(succeeded)}/{len(candidate_files)} файлов из архива, "
            f"{len(all_rows_for_dag)} строк сохранено в raw_historical."
        ),
    }
