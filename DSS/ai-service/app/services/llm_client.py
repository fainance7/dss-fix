"""
LLM client for the autonomous local inference server (vLLM / Ollama /
llama.cpp). Talks via the OpenAI-compatible Chat Completions API — no
provider SDK is used. **Traffic never leaves the customer's infrastructure**:
either the docker-compose network (Ollama) or an encrypted tunnel to the
dedicated GPU host. Nothing is sent to external services — hard security
requirement for the DSS project.

Два бэкенда:
* основной (`LLM_BASE_URL`) — vLLM на GPU-сервере, отвечает за секунды;
* резервный (`LLM_FALLBACK_BASE_URL`) — локальная Ollama с малой моделью,
  включается при обрыве канала к GPU. Качество и скорость хуже, но функция
  остаётся живой и полностью автономной.
"""
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Жёсткое требование проекта: автономный ИИ ДОЛЖЕН отвечать только на русском языке.
# Директива добавляется в системный промпт централизованно — для любого вызова
# chat_completion, независимо от того, что передал вызывающий код.
_RUSSIAN_DIRECTIVE = (
    "Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке. Весь ответ — только по-русски, "
    "без слов, фраз и предложений на других языках (в т.ч. английском и китайском). "
    "Если в контексте встречаются иноязычные термины — передавай их по-русски.\n\n"
)


@dataclass
class LLMHealth:
    ready: bool
    base_url: str
    model: str
    available_models: list[str]
    error: Optional[str] = None


class LLMUnavailableError(RuntimeError):
    """Raised when the local inference server is unreachable or returned
    a non-success status. Endpoints translate this into HTTP 503 so the
    UI can show a friendly 'LLM offline' message."""


def _auth_headers(api_key: str | None) -> dict[str, str]:
    """Заголовок авторизации. vLLM, запущенный с `--api-key`, отвечает 401 без
    него; Ollama лишний заголовок игнорирует."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _extra_body() -> dict[str, Any]:
    """Доп. поля тела запроса из настроек (JSON-строка `LLM_EXTRA_BODY`).

    Нужны для специфики конкретной модели без правки кода. Пример для Qwen3,
    у которого включён гибридный режим рассуждений и без отключения в ответ
    попадает блок `<think>…</think>`:
        LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
    """
    raw = (settings.LLM_EXTRA_BODY or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError as exc:
        logger.warning("LLM_EXTRA_BODY не разобран как JSON (%s) — игнорируем", exc)
        return {}


def _is_ollama(base_url: str) -> bool:
    """`keep_alive` — расширение Ollama; vLLM держит модель в VRAM всегда."""
    return "11434" in base_url or "ollama" in base_url.lower()


async def check_health() -> LLMHealth:
    """Probe `<base>/models` (OpenAI-compatible). Used by /health."""
    url = f"{settings.LLM_BASE_URL.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=_auth_headers(settings.LLM_API_KEY))
        if resp.status_code != 200:
            return LLMHealth(
                ready=False, base_url=settings.LLM_BASE_URL,
                model=settings.LLM_MODEL, available_models=[],
                error=f"HTTP {resp.status_code}",
            )
        data = resp.json()
        # Ollama returns {"data": null} when no models installed; OpenAI returns [].
        models = [m.get("id", "") for m in (data.get("data") or [])]
        return LLMHealth(
            ready=settings.LLM_MODEL in models or any(
                settings.LLM_MODEL.split(":")[0] in m for m in models
            ),
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            available_models=models,
        )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        return LLMHealth(
            ready=False, base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL, available_models=[], error=str(e),
        )


async def _call(
    base_url: str,
    model: str,
    api_key: str | None,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    read_timeout: float,
    extra_body: dict[str, Any],
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _RUSSIAN_DIRECTIVE + system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if _is_ollama(base_url):
        # Ollama-specific: держать модель в памяти между запросами,
        # без этого она выгружается через 5 минут idle → холодная перезагрузка.
        payload["keep_alive"] = settings.LLM_KEEP_ALIVE
    payload.update(extra_body)

    # Короткий connect (сервер недоступен → быстрый отказ), длинный read (даём
    # генерации завершиться, какой бы медленной она ни была).
    timeout = httpx.Timeout(read_timeout, connect=10.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=_auth_headers(api_key))
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
        raise LLMUnavailableError(f"LLM unreachable ({base_url}): {e}") from e

    if resp.status_code != 200:
        raise LLMUnavailableError(
            f"LLM returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise LLMUnavailableError(f"LLM response had no choices: {data}")
    return choices[0]["message"]["content"]


async def chat_completion(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    read_timeout: Optional[float] = None,
    allow_fallback: bool = True,
) -> str:
    """Single-turn chat completion via OpenAI-compatible API. Returns the
    assistant message content. Raises `LLMUnavailableError` when both the
    primary and the fallback backend are unavailable.

    `read_timeout` — сколько ждать ГЕНЕРАЦИЮ (тело ответа). None → берём
    settings.LLM_TIMEOUT_SECONDS. Connect-таймаут всегда короткий — недоступность
    сервера определяется быстро, а не в конце длинного ожидания.

    `allow_fallback=False` — не уходить на резервную CPU-модель (она отвечает
    десятки минут): для коротких массовых вызовов лучше сразу получить отказ."""
    read_t = read_timeout if read_timeout is not None else float(settings.LLM_TIMEOUT_SECONDS)
    tokens = max_tokens if max_tokens is not None else settings.LLM_SECTION_MAX_TOKENS
    extra = _extra_body()

    try:
        return await _call(
            settings.LLM_BASE_URL, settings.LLM_MODEL, settings.LLM_API_KEY,
            system_prompt, user_prompt,
            temperature=temperature, max_tokens=tokens,
            read_timeout=read_t, extra_body=extra,
        )
    except LLMUnavailableError as exc:
        if not allow_fallback or not settings.LLM_FALLBACK_BASE_URL:
            raise
        logger.warning(
            "Основной LLM недоступен (%s) — переключаюсь на резервный %s",
            exc, settings.LLM_FALLBACK_BASE_URL,
        )
        # Резервный бэкенд — локальная малая модель на CPU: даём ей длинный read,
        # иначе она гарантированно не успеет.
        return await _call(
            settings.LLM_FALLBACK_BASE_URL,
            settings.LLM_FALLBACK_MODEL or settings.LLM_MODEL,
            settings.LLM_FALLBACK_API_KEY,
            system_prompt, user_prompt,
            temperature=temperature, max_tokens=tokens,
            read_timeout=float(settings.LLM_NARRATIVE_READ_TIMEOUT_SECONDS),
            extra_body={},
        )
