"""
Извлечение содержимого Markdown-документов в ту же блочную модель, что и .docx.

Используется для документов, которые изначально писались в Markdown
(записки, обоснования), но сдаются заказчику в общем оформлении.
"""

from __future__ import annotations

import re
from pathlib import Path

from extract import Block, strip_numbering

HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
BULLET = re.compile(r"^[-*]\s+(.*)$")
NUMBERED = re.compile(r"^\d+[.)]\s+(.*)$")
TABLE_DIVIDER = re.compile(r"^\|[\s:|-]+\|$")
IMAGE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)$")


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def extract_md(path: str | Path, *, skip_to_heading: int = 2) -> list[Block]:
    """Читает .md и возвращает блоки; заголовки уровня `skip_to_heading` — разделы первого уровня.

    Всё, что предшествует первому заголовку указанного уровня (титульный блок с
    реквизитами), отбрасывается: эти сведения формируются заново из метаданных.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()

    blocks: list[Block] = []
    started = False
    in_code = False
    code: list[str] = []
    table: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table
        if table:
            blocks.append(Block(kind="table", header=table[0], rows=table[1:]))
            table = []

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("```"):
            if in_code:
                blocks.append(Block(kind="code", text="\n".join(code)))
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(raw)
            continue

        heading = HEADING.match(line)
        if heading:
            flush_table()
            level = len(heading.group(1))
            if level < skip_to_heading:
                continue
            started = True
            blocks.append(
                Block(kind="h", level=min(level - skip_to_heading + 1, 3),
                      text=strip_numbering(heading.group(2).strip()))
            )
            continue

        if not started:
            continue

        image = IMAGE.match(line.strip())
        if image:
            flush_table()
            # путь — относительно файла-исходника или абсолютный
            img = Path(image.group(1))
            img = img if img.is_absolute() else path.parent / img
            blocks.append(Block(kind="image", image=img.read_bytes()))
            continue

        if line.startswith("|"):
            if not TABLE_DIVIDER.match(line):
                table.append(_cells(line))
            continue
        flush_table()

        if not line.strip() or set(line.strip()) <= {"-", "*", "_"} and len(line.strip()) >= 3:
            continue

        bullet = BULLET.match(line)
        if bullet:
            if blocks and blocks[-1].kind == "bullet":
                blocks[-1].items.append(bullet.group(1).strip())
            else:
                blocks.append(Block(kind="bullet", items=[bullet.group(1).strip()]))
            continue

        numbered = NUMBERED.match(line)
        if numbered:
            if blocks and blocks[-1].kind == "num":
                blocks[-1].items.append(numbered.group(1).strip())
            else:
                blocks.append(Block(kind="num", items=[numbered.group(1).strip()]))
            continue

        blocks.append(Block(kind="p", text=line.strip()))

    flush_table()
    return blocks
