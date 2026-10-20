"""Тесты клиента автономного LLM-инференса.

Покрывают то, без чего переключение с Ollama на vLLM молча ломается:
авторизацию (vLLM с --api-key отвечает 401), передачу доп. полей тела запроса
(отключение режима рассуждений Qwen3) и резервный бэкенд при обрыве канала
к GPU-серверу.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _ok_response(content="ответ"):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _client_mock(post=None, get=None):
    """Контекстный менеджер httpx.AsyncClient с подменёнными post/get."""
    client = MagicMock()
    client.post = post or AsyncMock(return_value=_ok_response())
    client.get = get or AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_authorization_header_sent_when_key_set():
    """С LLM_API_KEY уходит заголовок Bearer — иначе vLLM вернёт 401."""
    from app.services import llm_client

    ctx, client = _client_mock()
    with patch.object(llm_client.settings, "LLM_API_KEY", "secret-key"), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("sys", "user")

    headers = client.post.await_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-key"


@pytest.mark.asyncio
async def test_no_authorization_header_without_key():
    from app.services import llm_client

    ctx, client = _client_mock()
    with patch.object(llm_client.settings, "LLM_API_KEY", None), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("sys", "user")

    assert "Authorization" not in client.post.await_args.kwargs["headers"]


@pytest.mark.asyncio
async def test_extra_body_disables_qwen_thinking():
    """LLM_EXTRA_BODY подмешивается в payload (отключение <think> у Qwen3)."""
    from app.services import llm_client

    ctx, client = _client_mock()
    extra = json.dumps({"chat_template_kwargs": {"enable_thinking": False}})
    with patch.object(llm_client.settings, "LLM_EXTRA_BODY", extra), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("sys", "user")

    payload = client.post.await_args.kwargs["json"]
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_broken_extra_body_is_ignored():
    from app.services import llm_client

    ctx, client = _client_mock()
    with patch.object(llm_client.settings, "LLM_EXTRA_BODY", "{не json"), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("sys", "user")

    assert "chat_template_kwargs" not in client.post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_keep_alive_only_for_ollama():
    """keep_alive — расширение Ollama; в vLLM его слать не нужно."""
    from app.services import llm_client

    ctx, client = _client_mock()
    with patch.object(llm_client.settings, "LLM_BASE_URL", "http://gpu-tunnel:8000/v1"), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("sys", "user")
    assert "keep_alive" not in client.post.await_args.kwargs["json"]

    ctx2, client2 = _client_mock()
    with patch.object(llm_client.settings, "LLM_BASE_URL", "http://ollama:11434/v1"), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx2):
        await llm_client.chat_completion("sys", "user")
    assert "keep_alive" in client2.post.await_args.kwargs["json"]


@pytest.mark.asyncio
async def test_russian_directive_always_prepended():
    from app.services import llm_client

    ctx, client = _client_mock()
    with patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        await llm_client.chat_completion("Вы — аналитик", "данные")

    system = client.post.await_args.kwargs["json"]["messages"][0]["content"]
    assert system.startswith("Отвечай ИСКЛЮЧИТЕЛЬНО на русском языке")
    assert "Вы — аналитик" in system


@pytest.mark.asyncio
async def test_fallback_used_when_primary_unreachable():
    """Обрыв канала к GPU → запрос уходит на резервный бэкенд."""
    from app.services import llm_client

    calls: list[str] = []

    async def _post(url, **kwargs):
        calls.append(url)
        if "gpu-tunnel" in url:
            raise httpx.ConnectError("канал оборван")
        return _ok_response("резервный ответ")

    ctx, _ = _client_mock(post=AsyncMock(side_effect=_post))
    with patch.object(llm_client.settings, "LLM_BASE_URL", "http://gpu-tunnel:8000/v1"), \
         patch.object(llm_client.settings, "LLM_FALLBACK_BASE_URL", "http://ollama:11434/v1"), \
         patch.object(llm_client.settings, "LLM_FALLBACK_MODEL", "qwen2.5:3b-instruct-q4_K_M"), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        result = await llm_client.chat_completion("sys", "user")

    assert result == "резервный ответ"
    assert len(calls) == 2 and "ollama" in calls[1]


@pytest.mark.asyncio
async def test_error_raised_when_no_fallback_configured():
    from app.services import llm_client

    ctx, _ = _client_mock(post=AsyncMock(side_effect=httpx.ConnectError("нет связи")))
    with patch.object(llm_client.settings, "LLM_FALLBACK_BASE_URL", None), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        with pytest.raises(llm_client.LLMUnavailableError):
            await llm_client.chat_completion("sys", "user")


@pytest.mark.asyncio
async def test_non_200_raises():
    from app.services import llm_client

    bad = MagicMock()
    bad.status_code = 401
    bad.text = "Unauthorized"
    ctx, _ = _client_mock(post=AsyncMock(return_value=bad))
    with patch.object(llm_client.settings, "LLM_FALLBACK_BASE_URL", None), \
         patch.object(llm_client.httpx, "AsyncClient", return_value=ctx):
        with pytest.raises(llm_client.LLMUnavailableError, match="401"):
            await llm_client.chat_completion("sys", "user")
