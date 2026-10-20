"""
Краткий вывод ИИ по вкладке дашборда направления.

Факты вкладки считаются детерминированно из одобренных значений показателей
(последний год, изменение к предыдущему и за пять лет, лидер и аутсайдер среди
районов). LLM только формулирует по ним 3–4 предложения; числа в ответе
сверяются с фактами, при расхождении или недоступности LLM выдаётся шаблонный
текст из тех же фактов.

Отпечаток (fingerprint) считается по тексту фактов: пока данные вкладки не
изменились, сохранённый вывод отдаётся без обращения к LLM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_client import LLMUnavailableError, chat_completion

logger = logging.getLogger(__name__)

# Отчётный год дашбордов (см. DATA_YEAR на фронтенде): значения за более поздний
# период показываются как значения отчётного года, если за него данных нет.
DATA_YEAR = 2025
MAX_INDICATORS = 12
# Меняется вместе с промптом/форматом фактов — сбрасывает сохранённые выводы.
PROMPT_VERSION = "3"
# Показатели-даты (код ДД.ММ) нельзя сравнивать и усреднять как числа.
_DATE_UNIT = "дата"

SUMMARY_SYSTEM = (
    "Вы — аналитик системы поддержки принятия решений (DSS) Северо-Казахстанской "
    "области. По фактам с вкладки дашборда напишите краткий вывод для руководства "
    "области: 3–4 предложения одним абзацем, не более 80 слов.\n"
    "Требования: используйте только числа из фактов — не пересчитывайте, не "
    "складывайте и не округляйте их; называйте год данных; отметьте главное "
    "изменение и различия между районами, если они приведены; единицы измерения — "
    "как в фактах; без заголовков, списков, вступлений и рекомендаций.\n"
    "Только констатация: не объясняйте причины изменений и не давайте оценок "
    "(«свидетельствует», «за счёт», «ухудшение») — причин в фактах нет. О снижении "
    "пишите «снизился на 4,9 %», без знака минус; не упоминайте, каких данных нет."
)


@dataclass
class IndicatorFact:
    name: str
    unit: str | None
    basis: str  # «по области» | «в среднем по районам»
    year: int
    value: float
    prev: float | None  # значение за year-1
    base_year: int | None  # год сравнения «за пять лет»
    base_value: float | None
    top: tuple[str, float] | None  # район с максимумом за year
    bottom: tuple[str, float] | None


def fmt(v: float) -> str:
    """Число для текста: десятичная запятая, пробел между разрядами."""
    a = abs(v)
    digits = 0 if a >= 1000 else 1 if a >= 100 else 2 if a >= 1 else 3
    s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s.replace("-", "−")


def _signed(v: float, text_: str) -> str:
    return f"{'+' if v >= 0 else '−'}{text_}"


# Единицы и названия, для которых изменение в процентах бессмысленно: сами в
# процентах, температуры, относительные величины.
_DIFF_NAME_WORDS = ("индекс", "темп", "коэффициент", "уровень", "доля")
_MAX_PLAUSIBLE_PCT = 300.0


def change_text(fact_name: str, unit: str | None, cur: float, prev: float | None) -> str | None:
    """Изменение показателя словами. Для абсолютных положительных величин — в
    процентах; для долей, уровней, индексов, коэффициентов, температур и рядов с
    отрицательными значениями — разницей значений («рост безработицы на 10 %»
    при 4,4 → 4,84 % читается неверно). Скачок больше чем в 4 раза — почти
    всегда разрыв ряда (смена единиц, состава), а не динамика: не приводится."""
    if prev is None:
        return None
    u = (unit or "").lower()
    is_pct = "%" in u
    if (
        is_pct or "°c" in u.replace("с", "c") or cur < 0 or prev <= 0
        or any(w in fact_name.lower() for w in _DIFF_NAME_WORDS)
    ):
        d = cur - prev
        return _signed(d, fmt(abs(d))) + (" п.п." if is_pct else "")
    p = (cur - prev) / prev * 100
    if abs(p) > _MAX_PLAUSIBLE_PCT:
        return None
    return _signed(p, f"{abs(p):.1f} %".replace(".", ","))


def _cap_to_data_year(by_year: dict[int, float]) -> dict[int, float]:
    later = [y for y in by_year if y > DATA_YEAR]
    if not later:
        return by_year
    out = {y: v for y, v in by_year.items() if y <= DATA_YEAR}
    out.setdefault(DATA_YEAR, by_year[max(later)])
    return out


async def collect_facts(
    db: AsyncSession, direction_code: str, indicator_ids: list[UUID],
) -> list[IndicatorFact]:
    """Факты по показателям вкладки (в порядке запроса), только своего направления."""
    ids = list(dict.fromkeys(indicator_ids))[:MAX_INDICATORS]
    if not ids:
        return []
    rows = (await db.execute(text("""
        SELECT i.id AS indicator_id, i.name AS indicator, u.name AS unit,
               r.id AS region_id, r.name AS region, r.level AS level,
               EXTRACT(YEAR FROM iv.period_start)::int AS year,
               AVG(iv.value) AS value
        FROM indicator_values iv
        JOIN indicators i ON i.id = iv.indicator_id
        JOIN directions d ON d.id = i.direction_id
        LEFT JOIN units u ON u.id = i.unit_id
        JOIN regions r ON r.id = iv.region_id
        WHERE i.id = ANY(:ids)
          AND d.code = :code
          AND iv.validation_status = 'approved'
          AND iv.period_type = i.period_kind
        GROUP BY i.id, i.name, u.name, r.id, r.name, r.level, EXTRACT(YEAR FROM iv.period_start)
    """), {"ids": ids, "code": direction_code})).mappings().all()

    # indicator → region → {год: значение}
    meta: dict[UUID, tuple[str, str | None]] = {}
    series: dict[UUID, dict[UUID, dict[int, float]]] = {}
    region: dict[UUID, tuple[str, int]] = {}
    for r in rows:
        meta[r["indicator_id"]] = (r["indicator"], r["unit"])
        region[r["region_id"]] = (r["region"], r["level"])
        series.setdefault(r["indicator_id"], {}).setdefault(r["region_id"], {})[r["year"]] = float(r["value"])

    facts: list[IndicatorFact] = []
    for ind_id in ids:
        if ind_id not in series:
            continue
        name, unit = meta[ind_id]
        if (unit or "").strip().lower() == _DATE_UNIT:
            continue
        by_region = {rid: _cap_to_data_year(s) for rid, s in series[ind_id].items()}
        oblast: dict[int, float] = {}
        districts: dict[UUID, dict[int, float]] = {}
        for rid, s in by_region.items():
            if region[rid][1] == 1:
                oblast.update(s)
            else:
                districts[rid] = s
        mean_by_year: dict[int, float] = {}
        coverage: dict[int, int] = {}
        for y in {y for s in districts.values() for y in s}:
            vals = [s[y] for s in districts.values() if y in s]
            mean_by_year[y] = sum(vals) / len(vals)
            coverage[y] = len(vals)

        # Областной ряд, если он не отстаёт от районного по последнему году.
        if oblast and (not mean_by_year or max(oblast) >= max(mean_by_year)):
            main, basis = oblast, "по области"
        else:
            main, basis = mean_by_year, "в среднем по районам"
        if not main:
            continue
        year = max(main)
        # Сравнивать с прошлыми годами можно только сопоставимые значения:
        # не через границу приведения к отчётному году (там срез другого года
        # загрузки) и, для среднего по районам, при том же составе районов.
        redated = any(max(s) > DATA_YEAR for s in series[ind_id].values())
        if redated:
            main = {year: main[year]}
        elif main is mean_by_year:
            main = {y: v for y, v in main.items() if coverage[y] >= 0.8 * coverage[year]}
        base_year = max(min(main), year - 5)
        while base_year < year - 1 and base_year not in main:
            base_year += 1
        has_base = base_year < year - 1 and base_year in main

        ranked = sorted(
            ((region[rid][0], s[year]) for rid, s in districts.items() if year in s),
            key=lambda x: x[1],
        )
        spread = len(ranked) >= 3 and ranked[0][1] != ranked[-1][1]
        facts.append(IndicatorFact(
            name=name, unit=unit, basis=basis, year=year, value=main[year],
            prev=main.get(year - 1),
            base_year=base_year if has_base else None,
            base_value=main[base_year] if has_base else None,
            top=ranked[-1] if spread else None,
            bottom=ranked[0] if spread else None,
        ))
    return facts


def render_facts(facts: list[IndicatorFact]) -> str:
    """Факты текстом — вход LLM и основа отпечатка данных."""
    lines = []
    for f in facts:
        unit = f", {f.unit}" if f.unit else ""
        parts = [f"- {f.name}{unit} ({f.basis}): {f.year} г. — {fmt(f.value)}"]
        if f.prev is not None:
            change = change_text(f.name, f.unit, f.value, f.prev)
            parts.append(
                f"{f.year - 1} г. — {fmt(f.prev)}" + (f" (изменение за год {change})" if change else "")
            )
        if f.base_year is not None and f.base_value is not None:
            change = change_text(f.name, f.unit, f.value, f.base_value)
            parts.append(
                f"{f.base_year} г. — {fmt(f.base_value)}"
                + (f" (изменение с {f.base_year} г. {change})" if change else "")
            )
        line = "; ".join(parts) + "."
        if f.top and f.bottom:
            line += (
                f" Районы, {f.year} г.: максимум — {f.top[0]} ({fmt(f.top[1])}), "
                f"минимум — {f.bottom[0]} ({fmt(f.bottom[1])})."
            )
        lines.append(line)
    return "\n".join(lines)


def fingerprint(title: str, facts_text: str) -> str:
    payload = json.dumps(
        {"v": PROMPT_VERSION, "title": title, "facts": facts_text},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def template_summary(facts: list[IndicatorFact]) -> str:
    """Запасной вывод без LLM: те же факты короткими фразами."""
    out = []
    for f in facts[:3]:
        unit = f" {f.unit}" if f.unit else ""
        s = f"{f.name} ({f.basis}) в {f.year} году — {fmt(f.value)}{unit}"
        change = change_text(f.name, f.unit, f.value, f.prev)
        if change:
            s += f" ({change} к {f.year - 1} году)"
        if f.top and f.bottom:
            s += f"; максимум — {f.top[0]} ({fmt(f.top[1])}), минимум — {f.bottom[0]} ({fmt(f.bottom[1])})"
        out.append(s + ".")
    return " ".join(out)


# ── Числовой контроль ответа LLM ──────────────────────────────────────

_SPACES = " \u00a0\u202f"  # пробел, неразрывный и узкий неразрывный — разделители разрядов
_NUM = re.compile(rf"\d+(?:[{_SPACES}]\d{{3}})*(?:[.,]\d+)?")


def _numbers(s: str) -> list[tuple[float, int]]:
    """Числа текста как (значение, число знаков после запятой)."""
    out = []
    for m in _NUM.finditer(s):
        raw = re.sub(rf"[{_SPACES}]", "", m.group()).replace(",", ".")
        out.append((float(raw), len(raw.split(".")[1]) if "." in raw else 0))
    return out


def foreign_numbers(summary: str, facts_text: str) -> list[float]:
    """Числа вывода, которых нет в фактах (с учётом округления). Целые до 10 —
    счёт и порядковые («3 района», «5 лет») — не проверяются."""
    known = [v for v, _ in _numbers(facts_text)]
    bad = []
    for v, digits in _numbers(summary):
        if digits == 0 and v <= 10:
            continue
        if not any(abs(round(k, digits) - v) < 10 ** -(digits + 3) for k in known):
            bad.append(v)
    return bad


def clean(raw: str) -> str:
    s = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    s = re.sub(r"[*#`_]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


async def generate_summary(
    direction_name: str, title: str, facts: list[IndicatorFact],
) -> tuple[str, str]:
    """(текст, источник): 'llm' или 'template', если LLM недоступна либо дважды
    привела числа, которых нет в фактах."""
    facts_text = render_facts(facts)
    user_prompt = f"Дашборд: {direction_name} — {title}\nФакты:\n{facts_text}"
    system = SUMMARY_SYSTEM
    for attempt in (1, 2):
        try:
            raw = await chat_completion(
                system, user_prompt, temperature=0.2, max_tokens=320, read_timeout=180.0,
                # Выводов десятки: резервная CPU-модель считала бы их часами —
                # при недоступном GPU сразу отдаём шаблонный текст.
                allow_fallback=False,
            )
        except LLMUnavailableError as exc:
            logger.warning("Вывод дашборда «%s»: LLM недоступна (%s)", title, exc)
            break
        summary = clean(raw)
        bad = foreign_numbers(summary, facts_text)
        if summary and not bad:
            return summary, "llm"
        logger.warning(
            "Вывод дашборда «%s», попытка %d: числа вне фактов %s", title, attempt, bad,
        )
        system = (
            SUMMARY_SYSTEM
            + "\nВ прошлом ответе были числа, которых нет в фактах. Приводите числа "
            "только дословно из фактов."
        )
    return template_summary(facts), "template"
