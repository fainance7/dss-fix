#!/usr/bin/env python3
"""Отчёт о заполненности данными DSS по направлениям и показателям → PDF.

Модель полноты («должно быть / загружено / %»):
  • Ожидаемая сетка — регион × год: 15 регионов (14 районов/городов + область)
    за каждый год диапазона [начало; 2025].
  • Начало диапазона = первый год с данными показателя; если данных нет — 2000.
  • «Должно быть»  = 15 × (2025 − начало + 1).
  • «Загружено»    = число фактически покрытых ячеек (регион × год) по approved
    в пределах [начало; 2025]. Месячные данные сворачиваются в годовую ячейку,
    поэтому % ≤ 100.
  • «%»            = Загружено / Должно быть × 100.

Сборка .docx через python-docx, конвертация в PDF через системный libreoffice.

Запуск:
    backend/.venv/bin/python scripts/generate_data_completeness_report.py

Подключение к БД — через env POSTGRES_* (дефолты — локальный dss-postgres :5433,
синхронный с продом).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg2
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()
DOCX_OUT = ROOT / "docs" / f"Отчёт_заполненности_данных_DSS_{TODAY}.docx"

REGION_COUNT = 15      # 14 районов/городов (level 2) + областной уровень (level 1)
END_YEAR = 2025        # конец ожидаемого диапазона
DEFAULT_START = 2000   # начало для показателей без данных

# Каталог геоданных берётся вживую из GIS-сервиса dssgis.dwh.kz (не из dss_db).
GIS_SERVICE_URL = os.environ.get("GIS_SERVICE_URL", "https://dssgis.dwh.kz").rstrip("/")
# GIS требует X-API-Key (аудит C-2); ключ отправляется только на GIS_SERVICE_URL.
GIS_API_KEY = os.environ.get("GIS_API_KEY", "")


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5433")),
        user=os.environ.get("POSTGRES_USER", "dss_user"),
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ.get("POSTGRES_DB", "dss_db"),
    )


# Строка на показатель (включая показатели без данных и новые направления — LEFT JOIN).
# loaded_cells — покрытые ячейки (регион × год) по approved в пределах [.. ; END_YEAR].
QUERY = f"""
SELECT
    d.name  AS direction_name,
    d.code  AS direction_code,
    i.id    AS indicator_id,
    i.name  AS indicator_name,
    i.sort_order AS sort_order,
    u.name  AS unit_name,
    count(iv.id)                                                  AS total_all,
    count(iv.id) FILTER (WHERE iv.validation_status = 'approved') AS total_approved,
    min(EXTRACT(YEAR FROM iv.period_start))
        FILTER (WHERE iv.validation_status = 'approved')          AS year_min,
    max(EXTRACT(YEAR FROM iv.period_start))
        FILTER (WHERE iv.validation_status = 'approved')          AS year_max,
    count(DISTINCT (iv.region_id, EXTRACT(YEAR FROM iv.period_start)))
        FILTER (WHERE iv.validation_status = 'approved'
                AND r.level IN (1, 2)
                AND EXTRACT(YEAR FROM iv.period_start) <= {END_YEAR}) AS loaded_cells
FROM directions d
JOIN indicators i             ON i.direction_id = d.id
LEFT JOIN units u             ON u.id = i.unit_id
LEFT JOIN indicator_values iv ON iv.indicator_id = i.id
LEFT JOIN regions r           ON r.id = iv.region_id
GROUP BY d.name, d.code, i.id, i.name, i.sort_order, u.name
ORDER BY d.name, i.sort_order, i.name;
"""


def _http_get_json(url: str, timeout: int = 15):
    headers = {"Accept": "application/json"}
    if GIS_API_KEY and url.startswith(GIS_SERVICE_URL + "/"):
        headers["X-API-Key"] = GIS_API_KEY
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_gis_catalog():
    """Каталог dssgis: ({category_id: {code,name}}, [datasets]) или None при недоступности.

    Данные тянутся вживую из GIS-сервиса — при недоступности секция «геоданные»
    просто пропускается, отчёт по indicator_values формируется как обычно.
    """
    try:
        cats_raw = _http_get_json(f"{GIS_SERVICE_URL}/categories")
        categories = {
            c["id"]: {"code": c.get("code"), "name": c.get("name")} for c in cats_raw
        }
        datasets: list[dict] = []
        page = 1
        while True:
            j = _http_get_json(f"{GIS_SERVICE_URL}/datasets?page={page}&page_size=100")
            datasets.extend(j.get("data", []))
            if page >= j.get("total_pages", 1):
                break
            page += 1
        return categories, datasets
    except Exception as exc:  # noqa: BLE001 — сеть/сервис недоступны
        print(f"[gis] каталог dssgis недоступен ({exc}) — секция «геоданные» пропущена")
        return None


def fetch_gis_links(conn):
    """({gis_category_id -> {code,name направления}}, число gis_dataset_links)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gcl.gis_category_id::text, d.code, d.name "
            "FROM gis_category_links gcl JOIN directions d ON d.id = gcl.direction_id"
        )
        cat_links = {cid: {"code": code, "name": name} for cid, code, name in cur.fetchall()}
        cur.execute("SELECT count(*) FROM gis_dataset_links")
        n_ds_links = cur.fetchone()[0]
    return cat_links, n_ds_links


def build_gis_section(doc, gis, section_no: int):
    """Раздел «Геоданные dssgis»: каталог GIS-сервиса по направлениям."""
    categories, datasets, cat_links, n_ds_links = gis
    doc.add_heading(f"{section_no}. Геоданные dssgis (каталог GIS-сервиса)", level=1)
    doc.add_paragraph(
        "Слои из GIS-сервиса dssgis.dwh.kz. Векторные проксируются вживую, растровые — "
        "COG-файлы. Эти данные образуют отдельный канал и в таблицу indicator_values "
        "НЕ входят. Привязка категорий GIS к направлениям DSS — через gis_category_links."
    )
    by_dir: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "vector": 0, "vector_with_kato": 0, "raster": 0, "ready": 0}
    )
    for ds in datasets:
        cid = ds.get("category_id")
        dirinfo = cat_links.get(cid) or categories.get(cid)
        name = (dirinfo or {}).get("name") or "— (без привязки)"
        b = by_dir[name]
        b["n"] += 1
        ft = ds.get("file_type", "")
        if ft in ("vector", "vector_with_kato", "raster"):
            b[ft] += 1
        if ds.get("status") == "ready":
            b["ready"] += 1
    rows = []
    for name, b in sorted(by_dir.items(), key=lambda x: -x[1]["n"]):
        rows.append([
            name, b["n"], b["vector"], b["vector_with_kato"], b["raster"],
            f"{b['ready']}/{b['n']}",
        ])
    add_table(
        doc,
        ["Направление", "Датасетов", "vector", "vector+KATO", "raster", "ready"],
        rows,
        col_widths_cm=[6.0, 2.0, 1.8, 2.4, 1.6, 1.6],
        font_size=9,
    )
    doc.add_paragraph(
        f"Всего в каталоге dssgis: {len(datasets)} датасетов в {len(categories)} категориях. "
        f"Категорий привязано к направлениям DSS: {len(cat_links)}/{len(categories)}. "
        f"Датасетов привязано к показателям (gis_dataset_links): {n_ds_links} "
        "(поштучная привязка датасет→показатель ещё не выполнена)."
    )


def fmt_year(v) -> str:
    return str(int(v)) if v is not None else "—"


def expectation(row) -> tuple[int, int, int, int]:
    """Возвращает (start_year, expected, loaded, pct)."""
    start = int(row["year_min"]) if row["year_min"] is not None else DEFAULT_START
    start = min(start, END_YEAR)
    n_years = END_YEAR - start + 1
    expected = REGION_COUNT * n_years
    loaded = row["loaded_cells"] or 0
    pct = round(100.0 * loaded / expected) if expected else 0
    return start, expected, loaded, pct


def add_table(doc, header, rows, col_widths_cm=None, font_size=9):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, h in enumerate(header):
        hdr[i].text = h
        for p in hdr[i].paragraphs:
            for run in p.runs:
                run.bold = True
                run.font.size = Pt(font_size)
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)
            for p in cells[i].paragraphs:
                for run in p.runs:
                    run.font.size = Pt(font_size)
    if col_widths_cm:
        for row in table.rows:
            for i, w in enumerate(col_widths_cm):
                row.cells[i].width = Cm(w)
    doc.add_paragraph()


def thousands(n) -> str:
    return f"{n:,}".replace(",", " ")


def build_docx(rows, totals, gis=None):
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    # ── Титул ──
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Отчёт о заполненности данными DSS")
    r.bold = True
    r.font.size = Pt(20)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.add_run(
        "Сколько данных должно быть, сколько загружено и % заполнения\n"
        f"Дата формирования: {TODAY}\n"
        "Источник: БД dss_db · Северо-Казахстанская область · "
        f"{REGION_COUNT} регионов (14 районов/городов + область) · диапазон до {END_YEAR} г."
    )
    doc.add_paragraph()

    # ── Сводка ──
    doc.add_heading("1. Сводка", level=1)
    pct_total = round(100.0 * totals["loaded"] / totals["expected"]) if totals["expected"] else 0
    add_table(
        doc,
        ["Метрика", "Значение"],
        [
            ["Направлений", totals["directions"]],
            ["Показателей в каталоге", totals["catalog"]],
            ["Показателей с данными", f"{totals['with_data']} "
             f"({round(100*totals['with_data']/totals['catalog']) if totals['catalog'] else 0}%)"],
            ["Должно быть (ячеек регион×год)", thousands(totals["expected"])],
            ["Загружено", thousands(totals["loaded"])],
            ["% заполнения", f"{pct_total}%"],
        ],
        col_widths_cm=[8, 6],
        font_size=10,
    )
    doc.add_paragraph(
        "Расчёт: должно быть = 15 регионов × число лет от начала данных "
        f"(или 2000, если данных нет) до {END_YEAR} г.; загружено = покрытые "
        "ячейки регион×год (approved, месячные данные сведены к годовым)."
    )

    # ── Сводка по направлениям ──
    doc.add_heading("2. Покрытие по направлениям", level=1)
    by_dir: dict[str, dict] = {}
    for row in rows:
        _, expected, loaded, _ = expectation(row)
        d = by_dir.setdefault(
            row["direction_name"],
            {"inds": 0, "with_data": 0, "expected": 0, "loaded": 0},
        )
        d["inds"] += 1
        if (row["total_all"] or 0) > 0:
            d["with_data"] += 1
        d["expected"] += expected
        d["loaded"] += loaded
    dir_rows = []
    for name, d in by_dir.items():
        pct = round(100.0 * d["loaded"] / d["expected"]) if d["expected"] else 0
        dir_rows.append([
            name, d["inds"], d["with_data"],
            thousands(d["expected"]), thousands(d["loaded"]), f"{pct}%",
        ])
    add_table(
        doc,
        ["Направление", "Показ.", "С данными", "Должно быть", "Загружено", "%"],
        dir_rows,
        col_widths_cm=[6.0, 1.6, 2.0, 2.6, 2.4, 1.4],
        font_size=9,
    )

    # ── Геоданные dssgis (отдельный канал, не в indicator_values) ──
    detail_no = 3
    if gis is not None:
        build_gis_section(doc, gis, section_no=3)
        detail_no = 4

    # ── Детально по показателям ──
    doc.add_heading(f"{detail_no}. Детально по показателям", level=1)
    header = ["Показатель", "Ед.", "Годы", "Должно быть", "Загружено", "% запол."]
    widths = [7.4, 1.8, 2.4, 2.4, 2.2, 1.6]
    cur_dir = None
    chunk: list[list] = []

    def flush():
        if chunk:
            add_table(doc, header, chunk, col_widths_cm=widths, font_size=8)

    for row in rows:
        if row["direction_name"] != cur_dir:
            flush()
            chunk = []
            cur_dir = row["direction_name"]
            doc.add_heading(f"{cur_dir} ({row['direction_code']})", level=2)
        start, expected, loaded, pct = expectation(row)
        chunk.append([
            row["indicator_name"],
            row["unit_name"] or "—",
            f"{start}–{END_YEAR}",
            thousands(expected),
            thousands(loaded),
            f"{pct}%",
        ])
    flush()

    # ── Методика ──
    doc.add_heading("Методика", level=1)
    doc.add_paragraph(
        "• «Должно быть» = 15 регионов (14 районов/городов + областной уровень) × "
        f"число лет диапазона [начало; {END_YEAR}]."
    )
    doc.add_paragraph(
        "• «Начало» = первый год, за который есть данные показателя; если данных "
        f"нет вовсе — {DEFAULT_START} год."
    )
    doc.add_paragraph(
        "• «Загружено» = число фактически покрытых ячеек регион×год (учитываются "
        "только approved-значения; месячные данные сведены к году, поэтому % ≤ 100)."
    )
    note = doc.add_paragraph()
    note.add_run(
        "Источник: снимок БД dss_db (синхронен с продом). Значения после "
        f"{END_YEAR} г. в «загружено» не учитываются."
    ).font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    DOCX_OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(DOCX_OUT)
    return DOCX_OUT


def convert_to_pdf(docx_path: Path) -> Path:
    soffice = shutil.which("libreoffice") or shutil.which("soffice")
    if not soffice:
        raise RuntimeError("libreoffice/soffice не найден — не могу сконвертировать в PDF")
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf",
         "--outdir", str(docx_path.parent), str(docx_path)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    pdf = docx_path.with_suffix(".pdf")
    if not pdf.exists():
        raise RuntimeError("PDF не создан после конвертации")
    return pdf


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("SELECT count(*) FROM directions")
            n_dir = cur.fetchone()[0]
        cat_links, n_ds_links = fetch_gis_links(conn)
    finally:
        conn.close()

    # Каталог геоданных dssgis (вживую); при недоступности — секция пропускается.
    catalog = fetch_gis_catalog()
    gis = None
    if catalog is not None:
        categories, datasets = catalog
        gis = (categories, datasets, cat_links, n_ds_links)

    total_expected = total_loaded = 0
    for row in rows:
        _, expected, loaded, _ = expectation(row)
        total_expected += expected
        total_loaded += loaded

    totals = {
        "directions": n_dir,
        "catalog": len(rows),
        "with_data": sum(1 for r in rows if (r["total_all"] or 0) > 0),
        "expected": total_expected,
        "loaded": total_loaded,
    }

    docx_path = build_docx(rows, totals, gis=gis)
    print(f"DOCX: {docx_path}  ({docx_path.stat().st_size:,} bytes)")
    pdf_path = convert_to_pdf(docx_path)
    print(f"PDF:  {pdf_path}  ({pdf_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
