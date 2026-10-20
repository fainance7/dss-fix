"""
Извлечение содержимого исходных .docx в промежуточную блочную модель.

Содержание документов не изменяется: разбираются заголовки, абзацы, списки,
таблицы и врезки кода, отбрасываются только служебные элементы старого
оформления (титульный лист, ручное содержание, блок подписи).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

MONO_STYLES = {"Block Quotation", "HTML Preformatted", "Macro Text", "No Spacing"}
SIGNATURE_MARKERS = ("Директор ТОО", "С уважением")


@dataclass
class Block:
    kind: str  # h | p | bullet | num | table | code | image | hr
    text: str = ""
    level: int = 1
    items: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    image: bytes | None = None


NUMBER_PREFIX = re.compile(r"^(?:\d+(?:\.\d+)*\.?)\s+")
OLD_CAPTION = re.compile(r"^(Рисунок|Таблица)\s+\d+\s*[—–-]\s")


def strip_numbering(text: str) -> str:
    """Убирает ручную нумерацию заголовка («2.1. Название» → «Название»)."""
    return NUMBER_PREFIX.sub("", text).strip()


def _iter_body(document) -> list:
    """Обход тела документа с сохранением порядка параграфов и таблиц."""
    body = document.element.body
    out = []
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            out.append(Paragraph(child, document))
        elif child.tag == qn("w:tbl"):
            out.append(Table(child, document))
    return out


def _paragraph_image(par: Paragraph, document) -> bytes | None:
    blips = par._p.findall(".//" + qn("a:blip"))
    for blip in blips:
        rid = blip.get(qn("r:embed"))
        if rid:
            try:
                return document.part.related_parts[rid].blob
            except KeyError:
                return None
    return None


def _table_blocks(table: Table) -> Block:
    rows = [[cell.text.strip().replace("\n", " ") for cell in row.cells] for row in table.rows]
    if not rows:
        return Block(kind="p", text="")
    return Block(kind="table", header=rows[0], rows=rows[1:])


def _is_mono(par: Paragraph) -> bool:
    if par.style.name in MONO_STYLES:
        return True
    fonts = {run.font.name for run in par.runs if run.font.name}
    return bool(fonts) and all(f and ("Mono" in f or "Courier" in f or "Consol" in f) for f in fonts)


def extract(path: str | Path) -> list[Block]:
    """Читает .docx и возвращает список блоков без старого оформления."""
    document = docx.Document(str(path))
    items = _iter_body(document)

    blocks: list[Block] = []
    started = False  # тело началось (после титула и старого содержания)
    skipping_toc = False

    for item in items:
        if isinstance(item, Table):
            if not started:
                continue
            blocks.append(_table_blocks(item))
            continue

        par: Paragraph = item
        text = par.text.strip()
        style = par.style.name

        # блоки подписи старого оформления отбрасываются: в части документов
        # подпись стоит не только в конце, но и перед приложениями
        if any(text.startswith(m) for m in SIGNATURE_MARKERS):
            continue

        # подписи рисунков и таблиц формируются заново при сборке
        if OLD_CAPTION.match(text):
            continue

        if not started:
            # старое содержание: заголовок «Содержание» и следующий за ним список
            if style.startswith("Heading") and text.lower().startswith("содержание"):
                skipping_toc = True
                continue
            if skipping_toc:
                if style in ("List Bullet", "List Number", "Normal") and not style.startswith("Heading"):
                    continue
                skipping_toc = False
            if style.startswith("Heading"):
                started = True
            else:
                image = _paragraph_image(par, document)
                if image is not None and blocks:
                    blocks.append(Block(kind="image", image=image))
                continue

        if style.startswith("Heading"):
            if not text:
                continue
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
            blocks.append(Block(kind="h", level=min(level, 3), text=strip_numbering(text)))
            continue

        image = _paragraph_image(par, document)
        if image is not None:
            blocks.append(Block(kind="image", image=image))
            if not text:
                continue

        if not text:
            continue

        if style == "List Bullet":
            if blocks and blocks[-1].kind == "bullet":
                blocks[-1].items.append(text)
            else:
                blocks.append(Block(kind="bullet", items=[text]))
            continue

        if style == "List Number":
            if blocks and blocks[-1].kind == "num":
                blocks[-1].items.append(text)
            else:
                blocks.append(Block(kind="num", items=[text]))
            continue

        if style == "Horizontal Line":
            continue

        if _is_mono(par):
            if blocks and blocks[-1].kind == "code":
                blocks[-1].text += "\n" + par.text
            else:
                blocks.append(Block(kind="code", text=par.text))
            continue

        blocks.append(Block(kind="p", text=text))

    return blocks
