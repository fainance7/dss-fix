"""
Сборка документов отчётности в едином оформлении.

Содержание исходных документов переносится без изменений; заново формируются
титульный лист, аннотация, содержание с номерами страниц, нумерация разделов,
подписи таблиц и рисунков, блок подписи руководителя.

Запуск: backend/.venv/bin/python scripts/restyle_reports/build.py [ключ ...]
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))

import style as st  # noqa: E402
from extract import Block, extract  # noqa: E402
from extract_md import extract_md  # noqa: E402
from meta import (  # noqa: E402
    CUSTOMER,
    DIRECTOR,
    DOCUMENTS,
    EXECUTOR,
    EXTRA_COPIES,
    PROJECT,
)  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = Path("/home/rus/Documents/АН/ЭТАПЫ-обновленные")
TOC_LEVELS = (1, 2)


# --------------------------------------------------------------------------
# точечные правки оформления


def apply_fixes(blocks: list[Block], meta: dict) -> list[Block]:
    """Приводит ссылки на рисунки к новой нумерации и вставляет схемы из репозитория."""
    for pattern, replacement in meta.get("text_fixes", []):
        for block in blocks:
            if block.kind in ("p", "bullet", "num"):
                block.text = re.sub(pattern, replacement, block.text)
                block.items = [re.sub(pattern, replacement, item) for item in block.items]

    result: list[Block] = []
    inserts = list(meta.get("figure_inserts", []))
    for block in blocks:
        result.append(block)
        for insert in list(inserts):
            if block.kind == "p" and insert["after"] in block.text:
                path = Path(insert["file"])
                path = path if path.is_absolute() else ROOT / path
                result.append(Block(kind="image", image=path.read_bytes()))
                inserts.remove(insert)
    return result


# --------------------------------------------------------------------------
# нумерация


def number_blocks(blocks: list[Block]) -> tuple[list[dict], list[tuple[str, int]]]:
    """Проставляет номера разделам, таблицам и рисункам; возвращает план и содержание."""
    counters = [0, 0, 0]
    table_no = 0
    figure_no = 0
    plan: list[dict] = []
    toc: list[tuple[str, int]] = []

    for block in blocks:
        item = {"block": block}
        if block.kind == "h":
            level = block.level
            counters[level - 1] += 1
            for deeper in range(level, 3):
                counters[deeper] = 0
            number = ".".join(str(counters[i]) for i in range(level) if counters[i])
            item["number"] = number
            item["title"] = f"{number} {block.text}"
            if level in TOC_LEVELS:
                toc.append((item["title"], level))
        elif block.kind == "table":
            table_no += 1
            item["index"] = table_no
        elif block.kind == "image":
            figure_no += 1
            item["index"] = figure_no
        plan.append(item)

    return plan, toc


# --------------------------------------------------------------------------
# страницы документа


def add_title_page(document, meta: dict) -> None:
    st.para(document, meta["date"], align=WD_ALIGN_PARAGRAPH.LEFT, space_after=Pt(120))
    st.para(
        document,
        meta["title"],
        size=Pt(20),
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=Pt(24),
    )
    st.para(
        document,
        f"Проект: {PROJECT}",
        size=Pt(13),
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=Pt(8),
    )
    st.para(
        document,
        meta["stage"],
        size=Pt(13),
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=Pt(180),
    )
    st.para(
        document,
        f"Исполнитель: {EXECUTOR}.",
        size=Pt(13),
        align=WD_ALIGN_PARAGRAPH.CENTER,
    )
    st.page_break(document)


def add_abstract(document, meta: dict) -> None:
    # гриф приложения в правом верхнем углу (например, «Приложение 2 к Отчёту…»)
    lines = meta.get("annex_lines", [])
    for i, line in enumerate(lines):
        st.para(document, line, size=Pt(9), align=WD_ALIGN_PARAGRAPH.RIGHT,
                space_after=Pt(6) if i == len(lines) - 1 else Pt(0))
    st.para(
        document,
        meta["abstract_heading"],
        size=Pt(14),
        bold=True,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=Pt(16),
    )
    st.para(document, f"Проект: {PROJECT}.", align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    st.para(document, f"{meta['stage']}.", align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    st.para(document, f"Заказчик: {CUSTOMER}.")
    st.para(document, f"Исполнитель: {EXECUTOR}.")
    st.para(document, f"Версия документа: {meta['version']}.")
    st.para(document, f"Дата: {meta['date']}", space_after=Pt(12))
    st.para(document, meta["summary"], align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    st.labelled_para(document, "Объект работ: ", meta["object"])
    st.labelled_para(document, "Полученные результаты: ", meta["results"])
    st.labelled_para(document, "Ключевые слова: ", meta["keywords"])
    st.page_break(document)


def add_toc(document, toc: list[tuple[str, int]], pages: dict[str, int]) -> None:
    st.para(
        document,
        "Содержание",
        size=Pt(15),
        bold=True,
        align=WD_ALIGN_PARAGRAPH.LEFT,
        space_after=Pt(10),
    )
    for title, level in toc:
        st.toc_entry(document, title, pages.get(title), level)
    st.page_break(document)


def add_signature(document) -> None:
    st.para(document, "", space_after=Pt(24))
    st.para(document, "С уважением,", space_after=Pt(18))
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.tab_stops.add_tab_stop(Cm(15.4), alignment=2)
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    st._set_run(p.add_run(f"Директор {EXECUTOR}"))
    st._set_run(p.add_run(f"\t{DIRECTOR}"))


def add_body(document, plan: list[dict], captions: dict, inline: bool = False) -> None:
    table_titles = captions.get("tables", [])
    figure_titles = captions.get("figures", [])

    for item in plan:
        block: Block = item["block"]

        if block.kind == "h":
            document.add_heading(item["title"], level=block.level)
        elif block.kind == "p":
            st.para(document, block.text, inline=inline)
        elif block.kind == "bullet":
            st.dash_list(document, block.items, inline=inline)
        elif block.kind == "num":
            st.numbered_list(document, block.items, inline=inline)
        elif block.kind == "code":
            st.code_block(document, block.text)
        elif block.kind == "table":
            index = item["index"]
            widths = captions.get("widths", {})
            st.table(document, block.header, block.rows, inline=inline,
                     widths=widths.get(index) or widths.get(len(block.header)))
            title = table_titles[index - 1] if index <= len(table_titles) else ""
            st.caption(document, f"Таблица {index} — {title}".rstrip(" —"))
        elif block.kind == "image":
            index = item["index"]
            picture = document.add_paragraph()
            picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
            picture.paragraph_format.space_after = Pt(2)
            picture.add_run().add_picture(io.BytesIO(block.image), width=Cm(15))
            title = figure_titles[index - 1] if index <= len(figure_titles) else ""
            st.caption(document, f"Рисунок {index} — {title}".rstrip(" —"))


def build_document(meta: dict, blocks: list[Block], pages: dict[str, int]):
    document = st.init_document()
    plan, toc = number_blocks(blocks)

    if meta.get("layout", "report") == "report":
        add_title_page(document, meta)
    add_abstract(document, meta)
    add_toc(document, toc, pages)
    add_body(document, plan, meta.get("captions", {}), inline=meta.get("markup") == "md")
    add_signature(document)
    st.footer_page_numbers(document)
    return document, toc


# --------------------------------------------------------------------------
# пагинация: номера страниц берутся из фактической вёрстки PDF


def docx_to_pdf(docx_path: Path, out_dir: Path) -> Path:
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return out_dir / (docx_path.stem + ".pdf")


def page_map_from_pdf(pdf_path: Path, toc: list[tuple[str, int]]) -> dict[str, int]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        timeout=300,
    ).stdout.decode("utf-8", "replace")

    pages = text.split("\f")
    # содержание перечисляет те же заголовки — поиск ведём после его последней страницы
    toc_page = 0
    for index, page in enumerate(pages):
        # заголовок содержания — отдельная строка; слово в шапке таблицы не в счёт
        if any(line.strip() == "Содержание" for line in page.splitlines()):
            toc_page = index

    found: dict[str, int] = {}
    for title, _ in toc:
        needle = re.sub(r"\s+", " ", title).strip()
        for index in range(toc_page + 1, len(pages)):
            flat = re.sub(r"\s+", " ", pages[index])
            if needle in flat:
                found[title] = index + 1
                break
    return found


def render(key: str, meta: dict, workdir: Path) -> tuple[Path, Path]:
    src = meta["src"] if Path(meta["src"]).is_absolute() else ROOT / meta["src"]
    reader = extract_md if meta.get("markup") == "md" else extract
    blocks = apply_fixes(reader(src), meta)

    docx_path = workdir / f"{meta['out_name']}.docx"
    pages: dict[str, int] = {}
    toc: list[tuple[str, int]] = []

    for _ in range(3):
        document, toc = build_document(meta, blocks, pages)
        document.save(docx_path)
        pdf_path = docx_to_pdf(docx_path, workdir)
        new_pages = page_map_from_pdf(pdf_path, toc)
        if new_pages == pages:
            break
        pages = new_pages

    document, _ = build_document(meta, blocks, pages)
    document.save(docx_path)
    pdf_path = docx_to_pdf(docx_path, workdir)

    missing = [title for title, _ in toc if title not in pages]
    if missing:
        print(f"    ! без номера страницы в содержании: {len(missing)} поз. — {missing[:3]}")
    return docx_path, pdf_path


def main(keys: list[str]) -> None:
    selected = keys or list(DOCUMENTS)
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for key in selected:
            meta = DOCUMENTS[key]
            print(f"[{key}] {meta['out_name']}")
            docx_path, pdf_path = render(key, meta, workdir)
            targets = [meta["out_dir"], *EXTRA_COPIES.get(key, [])]
            for out_dir in targets:
                target = Path(out_dir) if Path(out_dir).is_absolute() else OUT_ROOT / out_dir
                target.mkdir(parents=True, exist_ok=True)
                for produced in (docx_path, pdf_path):
                    shutil.copy2(produced, target / produced.name)
                print(f"    → {target / docx_path.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
