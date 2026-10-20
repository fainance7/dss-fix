"""Связь вариантов ландшафтного планирования (V1–V3) со сценариями развития 2030.

Связь смысловая. Классы вариантов ЛП — приоритетные направления использования
территории, а не категории земель, поэтому перевести вариант в изменение KPI
сценария пока нельзя (нужен слой фактических категорий земель, запрос АЛДИС
от 29.09.2026). Сейчас сценарий получает:

- выбранный вариант ЛП (по умолчанию V2 «сбалансированный»);
- снимок зонирования — доли функциональных зон по области и 14 районам,
  посчитанные по растру варианта наложением официальных границ районов;
- заготовку под численный расчёт (`kpi_effect.status = not_available`).

Снимок хранится в параметрах сценария в ai-service и попадает в контекст
заключений ИИ. Утверждённый сценарий вариант не меняет — он фиксируется вместе
с решением.

Доли считает scripts/lp_zoning_snapshot.py по полному разрешению растра и кладёт
снимок в том COG (`zoning-<версия>-<dataset_id>.json`); на проде он запускается
cron раз в 30 минут. Новый расчёт варианта даёт новый dataset_id — снимок для него
появится при следующем запуске, до этого привязка отвечает 503.

Почему не titiler: обзоры COG вариантов построены кубической интерполяцией
(OVERVIEW_RESAMPLING=CUBIC) — на них номера классов смешиваются, появляются
несуществующие классы 0 и 7, класс 3 раздувается до 5 % против 0,02 % в данных;
а статистику по полному разрешению прод-titiler не выдерживает (лимит памяти,
один воркер). Подробно — docs/Наложение_ЛП_на_маски_категорий_земель_2026-10-01.md.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import ai as ai_proxy
from app.api.v1 import gis_raster, landscape
from app.config import settings
from app.core.audit import log_action
from app.core.rbac import require_roles
from app.database import get_db
from app.deps import get_current_active_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/landscape", tags=["landscape"])

VARIANTS: dict[str, str] = {
    "lp-v1-conservative": "V1 — природоохранный",
    "lp-v2-balanced": "V2 — сбалансированный",
    "lp-v3-development": "V3 — хозяйственно-ориентированный",
}
DEFAULT_VARIANT = "lp-v2-balanced"

KPI_EFFECT_PENDING = {
    "status": "not_available",
    "reason": (
        "Классы варианта — функциональные зоны, а не категории земель. Перевод варианта "
        "в продукцию сельского хозяйства и KPI сценария выполнит модуль ЗБ-УР после "
        "получения слоя фактических категорий земель."
    ),
}

# Версия расчёта в имени кэша: снимки, посчитанные по обзорам, не переиспользуются.
_ZONING_VERSION = "fullres1"
_ZONING_CACHE: dict[str, dict] = {}
_ZONING_LOCKS: dict[str, asyncio.Lock] = {}




def _parse_legend(xml: str) -> list[dict]:
    """ColorMapEntry из SLD → [{value, label, color}] в порядке SLD."""
    out = []
    for entry in re.findall(r"<(?:\w+:)?ColorMapEntry\b[^>]*>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', entry))
        try:
            value = int(float(attrs["quantity"]))
        except (KeyError, ValueError):
            continue
        out.append({"value": value, "label": attrs.get("label") or str(value), "color": attrs.get("color")})
    return out




async def _variant_result(group: str) -> dict:
    for item in (await landscape._upstream("GET", "/landscape/results")).json() or []:
        if item.get("group") == group:
            latest = item.get("latest") or {}
            if latest.get("status") != "ready":
                raise HTTPException(status_code=409, detail="Результат варианта не готов")
            return item
    raise HTTPException(status_code=404, detail="Вариант не найден в результатах ЛП")


def _cache_file(dataset_id: str) -> str:
    return os.path.join(settings.COG_DIR, f"zoning-{_ZONING_VERSION}-{dataset_id}.json")


async def compute_zoning(group: str) -> dict:
    """Доли функциональных зон варианта по области и районам (с кэшем по dataset_id)."""
    if group not in VARIANTS:
        raise HTTPException(status_code=404, detail="Неизвестный вариант ландшафтного планирования")
    item = await _variant_result(group)
    dataset_id = str(item["latest"]["id"])
    if dataset_id in _ZONING_CACHE:
        return _ZONING_CACHE[dataset_id]

    lock = _ZONING_LOCKS.setdefault(dataset_id, asyncio.Lock())
    async with lock:
        if dataset_id in _ZONING_CACHE:
            return _ZONING_CACHE[dataset_id]
        try:
            with open(_cache_file(dataset_id), encoding="utf-8") as fh:
                _ZONING_CACHE[dataset_id] = json.load(fh)
                return _ZONING_CACHE[dataset_id]
        except (OSError, ValueError):
            pass

        # Снимок считает scripts/lp_zoning_snapshot.py (cron на проде раз в 30 минут):
        # по полному разрешению растра этот расчёт не выдерживает прод-titiler —
        # воркер падает по памяти и вместе с ним пропадают тайлы карт. Здесь только
        # запускаем заливку COG (скрипт читает её из того же тома) и просим подождать.
        gis_raster.start_ingest(dataset_id)
        logger.info("Снимок зонирования %s для %s ещё не подготовлен", dataset_id, group)
        raise HTTPException(
            status_code=503,
            detail=("Доли зон для нового расчёта ландшафтного планирования ещё готовятся "
                    "(обычно до 30 минут после расчёта). Повторите позже."),
        )


def _snapshot(zoning: dict, user_id: UUID | None) -> dict:
    return {
        **zoning,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "linked_by": str(user_id) if user_id else None,
        "kpi_effect": KPI_EFFECT_PENDING,
    }


async def _ai(method: str, path: str, **kwargs) -> httpx.Response:
    try:
        resp = await ai_proxy._get_client().request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="ИИ-сервис недоступен") from exc
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail") or detail
        except ValueError:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)
    return resp


async def link_variant(scenario_id: UUID, group: str, user_id: UUID | None) -> dict:
    zoning = await compute_zoning(group)
    resp = await _ai(
        "PUT",
        f"/api/v1/scenarios/{scenario_id}/landscape",
        json={"variant": group, "snapshot": _snapshot(zoning, user_id)},
    )
    return resp.json()


async def link_defaults(user_id: UUID | None) -> list[str]:
    """Вариант V2 всем сценариям без привязки (кроме утверждённых и архивных)."""
    scenarios = (await _ai("GET", "/api/v1/scenarios", params={"page_size": 200})).json()
    linked = []
    for sc in scenarios:
        if sc.get("status") in ("approved", "archived"):
            continue
        if (sc.get("parameters") or {}).get("landscape"):
            continue
        await link_variant(UUID(sc["id"]), DEFAULT_VARIANT, user_id)
        linked.append(sc["id"])
    return linked


async def link_defaults_background(user_id: UUID | None) -> None:
    """Для хука после генерации сценариев: ошибки только в лог, генерацию не ломают."""
    try:
        linked = await link_defaults(user_id)
        logger.info("Вариант ЛП по умолчанию привязан к сценариям: %s", linked)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось привязать вариант ЛП по умолчанию")


# ── Многокритериальное сравнение вариантов ───────────────────────────────────

COMPARE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "lp_compare.json")


def load_compare() -> dict:
    """Критерии сравнения V1–V3 (scripts/research/lp_compare_build.py): сырые площади, тыс. га."""
    with open(COMPARE_FILE, encoding="utf-8") as fh:
        return json.load(fh)


async def compare_freshness(data: dict) -> dict:
    """Совпадают ли варианты, по которым посчитаны критерии, с текущими результатами ЛП.

    Новый расчёт ЛП даёт новые dataset_id — тогда критерии устарели и их нужно пересчитать.
    Если ГИС-контур недоступен, проверка не выполняется (stale = None).
    """
    try:
        results = (await landscape._upstream("GET", "/landscape/results")).json() or []
    except Exception:  # noqa: BLE001 — сравнение показывается и без проверки актуальности
        return {"stale": None, "current": {}}
    current = {}
    for short, v in data["variants"].items():
        item = next((r for r in results if r.get("group") == v["group"]), None)
        current[short] = str((item or {}).get("latest", {}).get("id") or "") or None
    stale = any(current[s] and current[s] != v["dataset_id"] for s, v in data["variants"].items())
    return {"stale": stale, "current": current}


# ── Эндпоинты ────────────────────────────────────────────────────────────────


@router.get("/compare", dependencies=[Depends(get_current_active_user)])
async def get_compare():
    """Факты для многокритериального сравнения вариантов ЛП; веса и выбор — на стороне пользователя."""
    data = load_compare()
    return {**data, "freshness": await compare_freshness(data)}



@router.get("/zoning/{group}", dependencies=[Depends(get_current_active_user)])
async def get_zoning(group: str):
    """Доли функциональных зон варианта ЛП по области и районам."""
    return await compute_zoning(group)


class VariantBody(BaseModel):
    variant: Literal["lp-v1-conservative", "lp-v2-balanced", "lp-v3-development"]


@router.put("/scenarios/{scenario_id}/variant")
async def put_scenario_variant(
    scenario_id: UUID,
    body: VariantBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles("admin", "akim_obl", "exp_dir")),
):
    """Привязать к сценарию вариант ЛП: снимок зонирования считает сервер."""
    result = await link_variant(scenario_id, body.variant, user.id)
    await log_action(
        db, user_id=user.id, action="scenario_landscape_variant", entity_type="scenario",
        entity_id=scenario_id, details={"variant": body.variant},
        ip_address=request.client.host if request.client else None,
    )
    return result


@router.post("/scenarios/link-defaults")
async def post_link_defaults(user: User = Depends(require_roles("admin"))):
    """Привязать V2 всем сценариям без варианта ЛП."""
    return {"linked": await link_defaults(user.id)}
