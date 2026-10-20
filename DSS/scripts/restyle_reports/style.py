"""
Оформление документов отчётности по образцу оформления смежного исполнителя:
Times New Roman, титульный лист, аннотация, содержание с номерами страниц,
сквозная нумерация разделов, таблиц и рисунков, подпись руководителя.
"""

from __future__ import annotations

import re

import docx
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

FONT = "Times New Roman"
BODY_SIZE = Pt(12)
CAPTION_COLOR = RGBColor(0x66, 0x66, 0x66)
TOC_TAB = Cm(15.4)


def _force_font(style) -> None:
    """Заменяет шрифт темы явным Times New Roman во всех начертаниях."""
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.get_or_add_rFonts()
    for theme_attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if fonts.get(qn(theme_attr)) is not None:
            del fonts.attrib[qn(theme_attr)]
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(attr), FONT)


def init_document() -> docx.Document:
    """Создаёт документ с базовыми стилями и полями страницы 2,5 см."""
    document = docx.Document()

    normal = document.styles["Normal"]
    normal.font.size = BODY_SIZE
    _force_font(normal)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    heading_sizes = {1: Pt(15), 2: Pt(13), 3: Pt(12)}
    for level, size in heading_sizes.items():
        style = document.styles[f"Heading {level}"]
        style.font.size = size
        _force_font(style)
        style.font.bold = True
        style.font.italic = level == 3
        style.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)
        style.paragraph_format.space_before = Pt(14 if level == 1 else 10)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        style.paragraph_format.keep_with_next = True

    for section in document.sections:
        section.top_margin = Cm(2.5)
        section.bottom_margin = Cm(2.5)
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)

    return document


def _set_run(run, size=None, bold=False, italic=False, color=None):
    run.font.name = FONT
    run.font.size = size or BODY_SIZE
    run.font.bold = bold
    run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    return run


INLINE_MARKUP = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def _add_text(p, text: str, *, size=None, bold=False, italic=False, color=None,
              inline=False):
    """Добавляет текст абзаца; при inline=True разбирает **полужирный** и `моноширинный`."""
    if not inline or not INLINE_MARKUP.search(text):
        _set_run(p.add_run(text), size=size, bold=bold, italic=italic, color=color)
        return

    position = 0
    for match in INLINE_MARKUP.finditer(text):
        if match.start() > position:
            _set_run(p.add_run(text[position:match.start()]),
                     size=size, bold=bold, italic=italic, color=color)
        if match.group(1) is not None:
            _set_run(p.add_run(match.group(1)),
                     size=size, bold=True, italic=italic, color=color)
        else:
            run = _set_run(p.add_run(match.group(2)),
                           size=Pt((size or BODY_SIZE).pt - 1.5), italic=italic, color=color)
            run.font.name = "Consolas"
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
        position = match.end()
    if position < len(text):
        _set_run(p.add_run(text[position:]),
                 size=size, bold=bold, italic=italic, color=color)


def para(document, text="", *, size=None, bold=False, italic=False, align=None,
         color=None, space_after=None, space_before=None, keep_with_next=False,
         inline=False):
    p = document.add_paragraph()
    if text:
        _add_text(p, text, size=size, bold=bold, italic=italic, color=color, inline=inline)
    if align is not None:
        p.paragraph_format.alignment = align
    if space_after is not None:
        p.paragraph_format.space_after = space_after
    if space_before is not None:
        p.paragraph_format.space_before = space_before
    p.paragraph_format.keep_with_next = keep_with_next
    return p


def labelled_para(document, label: str, text: str, align=WD_ALIGN_PARAGRAPH.JUSTIFY):
    """Абзац вида «Объект работ: …» с полужирным вводным словом."""
    p = document.add_paragraph()
    _set_run(p.add_run(label), bold=True)
    _set_run(p.add_run(text))
    p.paragraph_format.alignment = align
    return p


def page_break(document):
    p = document.add_paragraph()
    p.add_run().add_break(WD_BREAK.PAGE)
    p.paragraph_format.space_after = Pt(0)
    return p


def caption(document, text: str):
    """Подпись таблицы или рисунка: курсив, серый, по центру."""
    return para(
        document,
        text,
        size=Pt(10),
        italic=True,
        color=CAPTION_COLOR,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_before=Pt(2),
        space_after=Pt(12),
    )


def dash_list(document, items: list[str], *, inline=False):
    """Перечисление тире с точкой с запятой, как в образце оформления."""
    total = len(items)
    for index, item in enumerate(items):
        text = item.rstrip(" .;")
        ending = "." if index == total - 1 else ";"
        p = document.add_paragraph()
        _add_text(p, f"— {text}{ending}", inline=inline)
        p.paragraph_format.left_indent = Cm(0.75)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def numbered_list(document, items: list[str], *, inline=False):
    for index, item in enumerate(items, start=1):
        text = item.rstrip(" .;")
        p = document.add_paragraph()
        _add_text(p, f"{index}) {text}.", inline=inline)
        p.paragraph_format.left_indent = Cm(0.75)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY


def code_block(document, text: str):
    p = document.add_paragraph()
    run = p.add_run(text)
    run.font.name = "Consolas"
    run.font.size = Pt(9.5)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
    fmt = p.paragraph_format
    fmt.left_indent = Cm(0.75)
    fmt.space_after = Pt(8)
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    fmt.line_spacing = 1.0
    return p


def table(document, header: list[str], rows: list[list[str]], *, inline=False,
          widths: list[float] | None = None):
    tbl = document.add_table(rows=1, cols=len(header))
    tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    tbl.autofit = True

    for cell, value in zip(tbl.rows[0].cells, header):
        cell.text = ""
        _add_text(cell.paragraphs[0], value, size=Pt(10.5), bold=True, inline=inline)
        cell.paragraphs[0].paragraph_format.space_after = Pt(2)
        cell.paragraphs[0].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _shade(cell, "F2F2F2")

    for row in rows:
        cells = tbl.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = ""
            _add_text(cell.paragraphs[0], str(value), size=Pt(10.5), inline=inline)
            cell.paragraphs[0].paragraph_format.space_after = Pt(2)
            cell.paragraphs[0].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    if widths:
        _set_widths(tbl, widths)
    _repeat_header(tbl)
    return tbl


def _set_widths(tbl, widths: list[float]):
    """Фиксированные ширины колонок в сантиметрах (Word берёт их из ячеек, LibreOffice — из сетки)."""
    tbl.autofit = False
    grid = tbl._tbl.tblGrid
    for col, w in zip(grid.findall(qn("w:gridCol")), widths):
        col.set(qn("w:w"), str(int(Cm(w).twips)))
    for row in tbl.rows:
        for cell, w in zip(row.cells, widths):
            cell.width = Cm(w)


def _shade(cell, hex_color: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _repeat_header(tbl):
    tr_pr = tbl.rows[0]._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def toc_entry(document, text: str, page: int | None, level: int):
    """Строка содержания с отбивкой номера страницы по правому краю."""
    p = document.add_paragraph()
    fmt = p.paragraph_format
    fmt.tab_stops.add_tab_stop(TOC_TAB, alignment=2, leader=0)  # 2 = right
    fmt.space_after = Pt(2)
    fmt.left_indent = Cm(0.6 * (level - 1))
    fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_run(p.add_run(text), size=Pt(11.5), bold=level == 1)
    _set_run(p.add_run("\t" + (str(page) if page else "")), size=Pt(11.5))
    return p


def footer_page_numbers(document):
    """Нумерация страниц по центру нижнего колонтитула."""
    for section in document.sections:
        section.different_first_page_header_footer = True
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run()
        _set_run(run, size=Pt(10))
        for tag, attrs, text in (
            ("w:fldChar", {"w:fldCharType": "begin"}, None),
            ("w:instrText", {"xml:space": "preserve"}, "PAGE"),
            ("w:fldChar", {"w:fldCharType": "end"}, None),
        ):
            el = OxmlElement(tag)
            for key, value in attrs.items():
                el.set(qn(key), value)
            if text:
                el.text = text
            run._r.append(el)
