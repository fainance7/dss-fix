"""
Сборка промптов и посекционная генерация аналитического нарратива.

Модуль общий для двух потребителей: HTTP-эндпоинта (`api/v1/scenarios.py`) и
офлайн-предгенерации на GPU-сервере (`scripts/pregenerate_narratives.py`).
Держать промпты в одном месте обязательно: если скрипт предгенерации соберёт
промпт хоть немного иначе, его хэш не совпадёт с расчётным на проде и кэш
будет считаться устаревшим при каждом обращении.

Нарратив генерируется ПОСЕКЦИОННО: каждая секция — отдельный короткий запрос к
LLM, поэтому каждый вызов гарантированно укладывается в таймаут, а полный отчёт
собирается из результатов. На CPU это было единственным способом дойти до конца
отчёта; на GPU остаётся как дешёвая устойчивость к обрывам.
"""
import hashlib
import json
import logging
import time
from typing import Any, Callable, Iterable

from app.config import settings
from app.services.llm_client import chat_completion

logger = logging.getLogger(__name__)

NARRATIVE_BASE_SYSTEM = (
    "Вы — аналитик системы поддержки принятия решений (DSS) Северо-Казахстанской "
    "области. По данным сценария развития и геопространственному контексту готовите "
    "отчёт для акимата на русском языке. Не выдумывайте цифры — используйте только "
    "то, что есть в контексте."
)

# Кортеж: (заголовок Markdown, инструкция для этой секции).
NARRATIVE_SECTIONS: list[tuple[str, str]] = [
    (
        "## 1. Текущая ситуация",
        "Напишите ТОЛЬКО раздел «Текущая ситуация»: 2 коротких абзаца с опорой на "
        "агрегаты по районам из контекста. Без заголовка — только текст раздела.",
    ),
    (
        "## 2. Прогнозный нарратив (до целевого года)",
        "Напишите ТОЛЬКО раздел «Прогнозный нарратив»: как сценарий и его параметры "
        "влияют на регион до целевого года. Без заголовка — только текст раздела.",
    ),
    (
        "## 3. Рекомендации",
        "Напишите ТОЛЬКО раздел «Рекомендации»: 3 конкретных действия для акимата "
        "нумерованным списком. Без заголовка — только текст раздела.",
    ),
]


def build_user_prompt(
    name: str, scenario_type: str, target_year: Any, parameters: dict | None, geo_text: str,
) -> str:
    """Пользовательская часть промпта: описание сценария + вариант ЛП + гео-контекст."""
    params = dict(parameters or {})
    landscape = params.pop("landscape", None)
    params_json = json.dumps(params, ensure_ascii=False, indent=2)
    landscape_text = render_landscape_text(landscape)
    return (
        f"Сценарий: {name}\n"
        f"Тип: {scenario_type}\n"
        f"Целевой год: {target_year}\n"
        f"Параметры (рычаги влияния и computed KPI):\n```json\n{params_json}\n```\n\n"
        + (f"{landscape_text}\n\n" if landscape_text else "")
        + f"{geo_text}"
    )


def render_landscape_text(snapshot: dict | None, top_zones: int = 3) -> str:
    """Вариант ландшафтного планирования сценария — текстом для модели.

    Снимок строит бэкенд DSS по растру варианта: доли функциональных зон по
    области и районам. Зоны задают рекомендуемое направление использования
    территории, а не категории земель, и числа KPI из них не выводятся — это
    сказано модели прямо, чтобы она не придумывала эффект.
    """
    if not snapshot:
        return ""
    legend = {str(e["value"]): e["label"] for e in snapshot.get("legend", [])}

    def zones(pct: dict) -> str:
        top = sorted(pct.items(), key=lambda kv: kv[1], reverse=True)[:top_zones]
        return ", ".join(f"{legend.get(k, k)} {v:.0f} %" for k, v in top)

    lines = [
        f"Вариант ландшафтного планирования сценария: {snapshot.get('label', snapshot.get('variant'))}.",
        "Это рекомендуемое функциональное зонирование территории (приоритетное направление "
        "использования), а не категории земель; количественное влияние на KPI не рассчитано — "
        "не приписывайте варианту численный эффект.",
    ]
    if snapshot.get("oblast_pct"):
        lines.append(f"Доли зон по области: {zones(snapshot['oblast_pct'])}.")
    districts = snapshot.get("districts") or []
    if districts:
        lines.append("Преобладающие зоны по районам:")
        for d in districts:
            lines.append(f"- {d['name']}: {zones(d.get('pct', {}))}")
    return "\n".join(lines)


def build_user_prompt_for(scenario, geo_text: str) -> str:
    """Промпт по ORM-объекту сценария."""
    return build_user_prompt(
        scenario.name, scenario.scenario_type, scenario.target_year,
        scenario.parameters, geo_text,
    )


def input_hash(user_prompt: str, *, model: str | None = None) -> str:
    """Отпечаток входных данных нарратива.

    Считается по пользовательскому промпту (в нём и сценарий, и его параметры, и
    гео-контекст с агрегатами по районам), набору секций и имени модели. Если
    данные показателей обновились или сменилась модель — хэш расходится, и
    сохранённый текст помечается устаревшим вместо того, чтобы выдаваться как
    актуальный. Системная часть промпта включена через список секций.
    """
    payload = json.dumps(
        {
            "user": user_prompt,
            "system": NARRATIVE_BASE_SYSTEM,
            "sections": NARRATIVE_SECTIONS,
            "model": model if model is not None else settings.LLM_MODEL,
        },
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def generate_sections(
    user_prompt: str,
    *,
    sections: Iterable[tuple[str, str]] | None = None,
    max_tokens: int | None = None,
    read_timeout: float | None = None,
    on_section: Callable[[int, int, float], None] | None = None,
) -> str:
    """Генерирует нарратив по секциям и возвращает собранный Markdown.

    `on_section(индекс, всего, секунд)` — необязательный колбэк прогресса.
    Исключения (в т.ч. `LLMUnavailableError`) пробрасываются вызывающему коду:
    именно он решает, записать ли `failed` в БД или повторить.
    """
    secs = list(sections if sections is not None else NARRATIVE_SECTIONS)
    tokens = max_tokens if max_tokens is not None else settings.LLM_SECTION_MAX_TOKENS
    timeout = (
        read_timeout if read_timeout is not None
        else float(settings.LLM_NARRATIVE_READ_TIMEOUT_SECONDS)
    )

    started = time.monotonic()
    out: list[str] = []
    for idx, (header, instruction) in enumerate(secs, start=1):
        text = await chat_completion(
            system_prompt=f"{NARRATIVE_BASE_SYSTEM}\n\n{instruction}",
            user_prompt=user_prompt,
            max_tokens=tokens,
            read_timeout=timeout,
        )
        out.append(f"{header}\n\n{text.strip()}")
        if on_section is not None:
            on_section(idx, len(secs), time.monotonic() - started)
    return "\n\n".join(out)
