"""Тонкий прокси к подсистеме приёма/хранения геоданных (смежная команда).

Фронт ходит на наш бэкенд (/api/v1/gis/*), мы форвардим запросы на GIS-сервис
(settings.GIS_SERVICE_URL, прод: https://dssgis.dwh.kz). Это:
- даёт ролевую авторизацию DSS (require_roles на уровне роутера);
- скрывает адрес GIS-сервиса от браузера и обходит CORS;
- не требует знания контракта: путь/тело/квери/ответ форвардятся как есть,
  поэтому при эволюции спеки v1.0.0 менять прокси не нужно.

Сервис ещё в разработке — при недоступности отдаём понятный 503.

Ручки ландшафтного планирования (/landscape/*) через прокси не пропускаем: они
доступны только через /api/v1/landscape, где действуют свои ограничения ролей,
слотов и тела запуска (иначе exp_ver/exp_dir могли бы менять входы ЛП и
запускать перерасчёт в обход них).
"""

import posixpath
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import settings
from app.core.rbac import require_roles

router = APIRouter(
    prefix="/gis",
    tags=["gis"],
    dependencies=[Depends(require_roles("admin", "exp_ver", "exp_dir", "data_uploader"))],
)

# Заголовки, специфичные для соединения/исходного запроса — не пробрасываем.
# Authorization (Bearer DSS) тоже не уходит в GIS-сервис.
_SKIP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "authorization",
    "cookie",
}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.GIS_SERVICE_URL.rstrip("/"),
            headers=settings.gis_headers,
            timeout=settings.GIS_SERVICE_TIMEOUT,
        )
    return _client


@router.get("/datasets/{dataset_id}/sld")
async def dataset_sld(dataset_id: uuid.UUID):
    """SLD набора с той же нормализацией, что на дашбордах (кодировка, переносы
    строк в подписях категорий) — иначе правила не совпадают с атрибутами."""
    from app.api.v1.dashboard_geo import _sld_text

    text = await _sld_text(str(dataset_id))
    if text is None:
        raise HTTPException(status_code=404, detail="Стиль для набора не задан")
    return Response(content=text, media_type="application/xml")


def _is_landscape_path(path: str) -> bool:
    """Путь ведёт в /landscape GIS-сервиса (с учётом «..», «//» и регистра)."""
    norm = posixpath.normpath("/" + path).lstrip("/").lower()
    return norm.split("/", 1)[0] == "landscape"


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    if _is_landscape_path(path):
        raise HTTPException(
            status_code=403,
            detail="Ландшафтное планирование доступно только через /api/v1/landscape",
        )
    client = _get_client()
    body = await request.body()
    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS
    }

    try:
        upstream = await client.request(
            request.method,
            f"/{path}",
            params=request.query_params,
            content=body or None,
            headers=fwd_headers,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise HTTPException(status_code=503, detail="ГИС-сервис недоступен") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Ошибка обращения к ГИС-сервису: {exc}"
        ) from exc

    # Ошибки апстрима нормализуем в detail, чтобы фронт показал текст (формат GIS: {code,message}).
    if upstream.status_code >= 400:
        detail = upstream.text
        try:
            data = upstream.json()
            detail = data.get("message") or data.get("detail") or detail
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
