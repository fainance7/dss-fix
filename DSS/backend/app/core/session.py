"""
Браузерная сессия: токены в httpOnly-cookie (H-1), защита от CSRF для запросов
с cookie и ограничение попыток входа (H-2).

Скрипты, интеграции и нагрузочные тесты по-прежнему работают с заголовком
Authorization: Bearer — для них ничего не меняется. Cookie выдаются только
веб-интерфейсу, который просит об этом заголовком X-Auth-Mode: cookie.
"""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, Response, status

from app.config import settings
from app.core.cache import get_cache

COOKIE_MODE_HEADER = "x-auth-mode"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# --------------------------------------------------------------------------- cookie
def wants_cookie(request: Request) -> bool:
    return request.headers.get(COOKIE_MODE_HEADER, "").lower() == "cookie"


def set_auth_cookies(response: Response, access_token: str, refresh_token: str | None = None) -> None:
    """Access-cookie живёт столько же, сколько refresh: по её наличию middleware фронта
    пускает на страницы, а истёкший JWT внутри обновляется запросом /auth/refresh."""
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400
    common = {"httponly": True, "secure": settings.is_production, "samesite": "strict"}
    response.set_cookie(settings.AUTH_COOKIE_ACCESS, access_token, max_age=max_age, path="/", **common)
    if refresh_token is not None:
        response.set_cookie(settings.AUTH_COOKIE_REFRESH, refresh_token, max_age=max_age,
                            path=settings.AUTH_COOKIE_REFRESH_PATH, **common)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.AUTH_COOKIE_ACCESS, path="/")
    response.delete_cookie(settings.AUTH_COOKIE_REFRESH, path=settings.AUTH_COOKIE_REFRESH_PATH)


def check_csrf(request: Request) -> None:
    """Запрос, аутентифицированный cookie, должен прийти со страницы самой Системы.

    SameSite=Strict не защищает от соседних поддоменов *.dwh.kz — для браузера это
    тот же «сайт». Поэтому для изменяющих методов сверяем Origin со списком
    разрешённых адресов, а при его отсутствии — признак Sec-Fetch-Site.
    """
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin:
        if origin.rstrip("/") not in settings.cors_origins:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Запрос с чужого источника отклонён")
        return
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Запрос с чужого источника отклонён")


# --------------------------------------------------------------------------- адрес клиента
def client_ip(request: Request) -> str | None:
    """Адрес пользователя. Caddy перезаписывает X-Forwarded-For адресом клиента
    (входящему заголовку не доверяет), поэтому первый элемент — реальный адрес."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


# --------------------------------------------------------------------------- лимит входа
_memory: dict[str, tuple[int, float]] = {}  # запасной счётчик, если Redis недоступен


async def _incr(key: str, ttl: int) -> int:
    redis = await get_cache()
    if redis is not None:
        try:
            n = await redis.incr(key)
            if n == 1:
                await redis.expire(key, ttl)
            return int(n)
        except Exception:  # Redis упал посреди работы — продолжаем на памяти
            pass
    now = time.monotonic()
    n, until = _memory.get(key, (0, now + ttl))
    if until < now:
        n, until = 0, now + ttl
    _memory[key] = (n + 1, until)
    return n + 1


async def _get(key: str) -> int:
    redis = await get_cache()
    if redis is not None:
        try:
            return int(await redis.get(key) or 0)
        except Exception:
            pass
    n, until = _memory.get(key, (0, 0.0))
    return n if until >= time.monotonic() else 0


async def _reset(key: str) -> None:
    redis = await get_cache()
    if redis is not None:
        try:
            await redis.delete(key)
        except Exception:
            pass
    _memory.pop(key, None)


def _locked() -> HTTPException:
    minutes = max(1, settings.LOGIN_LOCK_SECONDS // 60)
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Слишком много неудачных попыток входа. Повторите через {minutes} мин.",
        headers={"Retry-After": str(settings.LOGIN_LOCK_SECONDS)},
    )


async def guard_login_attempt(account: str, ip: str | None) -> None:
    """Вызывается до проверки пароля: учётная запись заблокирована после серии неудач
    или с адреса слишком много неудачных входов (перебор по разным учётным записям)
    → 429, пароль не проверяется. Успешные входы не считаются: пользователи акимата
    за одним NAT не упираются в лимит."""
    if await _get(f"login:fail:{account.lower()}") >= settings.LOGIN_MAX_FAILS:
        raise _locked()
    if ip and await _get(f"login:ipfail:{ip}") >= settings.LOGIN_IP_MAX_FAILS:
        raise _locked()


async def register_login_failure(account: str, ip: str | None) -> int:
    if ip:
        await _incr(f"login:ipfail:{ip}", settings.LOGIN_LOCK_SECONDS)
    return await _incr(f"login:fail:{account.lower()}", settings.LOGIN_LOCK_SECONDS)


async def register_login_success(account: str) -> None:
    await _reset(f"login:fail:{account.lower()}")
