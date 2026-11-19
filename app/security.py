"""Общие механизмы защиты веб-приложения ETL (Этап 7, испытания ИБ).

- заголовки безопасности HTTP (ПТ-03);
- ограничение размера тела запроса до разбора (ПТ-24);
- ограничение числа одновременных разборов файлов (ПТ-24);
- журнал событий безопасности `etl.security` (вход, отказы в доступе, загрузки).
"""
import logging
import os
import sys
import threading

from fastapi import HTTPException, Request

# --------------------------------------------------------------------------- журнал событий ИБ
security_log = logging.getLogger("etl.security")
if not security_log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s SECURITY %(message)s"))
    security_log.addHandler(_h)
    security_log.setLevel(logging.INFO)
    security_log.propagate = False


def client_ip(request: Request) -> str:
    """Адрес клиента. X-Forwarded-For учитывается только от доверенного обратного
    прокси: его разбирает Uvicorn (--proxy-headers, FORWARDED_ALLOW_IPS), поэтому
    подменить адрес заголовком из браузера нельзя (ПТ-11)."""
    return request.client.host if request.client else "unknown"


def log_event(event: str, request: Request | None = None, **fields):
    parts = [f"event={event}"]
    if request is not None:
        parts.append(f"ip={client_ip(request)}")
        parts.append(f"path={request.url.path}")
    for k, v in fields.items():
        parts.append(f"{k}={str(v).replace(' ', '_')[:120]}")
    security_log.info(" ".join(parts))


# --------------------------------------------------------------------------- лимиты загрузки
MAX_UPLOAD_MB = float(os.environ.get("ETL_MAX_UPLOAD_MB", "10"))
MAX_ZIP_UPLOAD_MB = float(os.environ.get("ETL_MAX_ZIP_UPLOAD_MB", "50"))
MAX_LINE_BYTES = int(os.environ.get("ETL_MAX_LINE_BYTES", str(64 * 1024)))
PARSE_CONCURRENCY = int(os.environ.get("ETL_PARSE_CONCURRENCY", "2"))
PARSE_WAIT_S = float(os.environ.get("ETL_PARSE_WAIT_S", "60"))
_BODY_LIMIT = int((max(MAX_UPLOAD_MB, MAX_ZIP_UPLOAD_MB) + 1) * 1024 * 1024)

_parse_slots = threading.BoundedSemaphore(PARSE_CONCURRENCY)


class parse_slot:
    """Не более PARSE_CONCURRENCY одновременных разборов файлов: разбор выполняется
    в пуле потоков и не блокирует обработку остальных запросов."""

    def __enter__(self):
        if not _parse_slots.acquire(timeout=PARSE_WAIT_S):
            raise HTTPException(503, "Сервер занят обработкой других файлов, повторите позже")
        return self

    def __exit__(self, *exc):
        _parse_slots.release()
        return False


def read_upload(file, limit_mb: float = MAX_UPLOAD_MB) -> bytes:
    """Читает загруженный файл с проверкой размера и, для CSV, длины строки."""
    limit = int(limit_mb * 1024 * 1024)
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"Файл больше допустимого размера {limit_mb:g} МБ")
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        check_lines(data)
    return data


def check_lines(data: bytes):
    start = 0
    n = len(data)
    while start < n:
        end = data.find(b"\n", start)
        if end == -1:
            end = n
        if end - start > MAX_LINE_BYTES:
            raise HTTPException(400, f"Строка файла длиннее {MAX_LINE_BYTES // 1024} КБ — файл не похож на таблицу показателей")
        start = end + 1


def safe_filename(name: str | None, default: str = "upload") -> str:
    """Имя файла без каталогов, управляющих и HTML-символов (ПТ-21, ПТ-22)."""
    base = (name or default).replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join("_" if (c in '<>:"\'&;|?*`$%{}' or ord(c) < 32) else c for c in base).strip(" .")
    return (cleaned or default)[:150]


# --------------------------------------------------------------------------- ASGI-прослойки
class _BodyTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Отклоняет запросы с телом больше лимита до их разбора (413)."""

    def __init__(self, app, limit: int = _BODY_LIMIT):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl is not None and cl.isdigit() and int(cl) > self.limit:
            return await _send_413(send)
        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.limit:
                    raise _BodyTooLarge()
            return msg

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await _send_413(send)


async def _send_413(send):
    body = '{"detail":"Тело запроса больше допустимого размера"}'.encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; worker-src 'self' blob:; "
       "child-src blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"geolocation=(), camera=(), microphone=()"),
    (b"content-security-policy", CSP.encode()),
]


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(msg):
            if msg["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"server"]
                present = {k.lower() for k, _ in hdrs}
                hdrs += [(k, v) for k, v in SECURITY_HEADERS if k not in present]
                msg = {**msg, "headers": hdrs}
            await send(msg)

        await self.app(scope, receive, send_with_headers)
