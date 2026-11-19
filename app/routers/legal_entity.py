import os
import json
import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/party_kz"
DADATA_API_KEY = os.environ.get("DADATA_API_KEY")

EGOV_URL = "https://data.egov.kz/api/v4/gbd_ul/v1"
EGOV_API_KEY = os.environ.get("EGOV_API_KEY")

# Переключатель источника — "dadata" (рабочий, по умолчанию) или "egov"
# (тестовый, data.egov.kz). 12.08.2026: на data.egov.kz найдены реальные
# проблемы — устаревший статус на живом БИН и дублирующиеся записи с
# разными ФИО директора под одним id (проверено на Казахтелекоме).
# Возврат на dadata — одна строка в .env + docker compose up -d fastapi,
# код трогать не нужно.
LEGAL_ENTITY_SOURCE = os.environ.get("LEGAL_ENTITY_SOURCE", "egov").strip().lower()  # по умолчанию data.egov.kz: данные не передаются за пределы РК


async def _search_dadata(q: str) -> list[dict]:
    if not DADATA_API_KEY:
        raise HTTPException(status_code=500, detail="DADATA_API_KEY не настроен на сервере")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Token {DADATA_API_KEY}",
    }
    payload = {"query": q}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(DADATA_URL, json=payload, headers=headers)
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="DaData не ответил вовремя")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка DaData: {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Не удалось связаться с DaData ({type(exc).__name__})")

    raw = resp.json().get("suggestions", [])
    results = []
    for item in raw:
        d = item.get("data", {})
        results.append({
            "bin": d.get("bin"),
            "name_ru": d.get("name_ru"),
            "status": d.get("status"),
            "type": d.get("type"),
            "fio": d.get("fio"),
            "registration_date": d.get("registration_date"),
            "address_ru": d.get("address_ru"),
            "oked": d.get("oked"),
            "oked_name_ru": d.get("oked_name_ru"),
            "krp": d.get("krp"),
            "krp_name_ru": d.get("krp_name_ru"),
        })
    return results


async def _search_egov(q: str) -> list[dict]:
    """
    Поиск через data.egov.kz (набор gbd_ul). ТЕСТОВЫЙ источник — см.
    предупреждение у LEGAL_ENTITY_SOURCE выше. Дубли не скрываются:
    все записи из ответа попадают в results как есть, без дедупликации
    по id, чтобы расхождения между записями были видны, а не спрятаны
    выбором "первой попавшейся".
    """
    if not EGOV_API_KEY:
        raise HTTPException(status_code=500, detail="EGOV_API_KEY не настроен на сервере")

    q_clean = q.strip()
    if q_clean.isdigit() and len(q_clean) == 12:
        query = {"size": 10, "query": {"bool": {"must": [{"match": {"bin": q_clean}}]}}}
    else:
        query = {"size": 10, "query": {"fuzzy_like_this": {"fields": ["_all"], "like_text": q_clean}}}

    params = {"apiKey": EGOV_API_KEY, "source": json.dumps(query, ensure_ascii=False)}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(EGOV_URL, params=params)
            if resp.status_code == 429:
                raise HTTPException(status_code=429, detail="data.egov.kz: превышен лимит 40 запросов/мин, подождите")
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="data.egov.kz не ответил вовремя")
        except httpx.HTTPStatusError as exc:
            raise HTTPException(status_code=502, detail=f"Ошибка data.egov.kz: {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Не удалось связаться с data.egov.kz ({type(exc).__name__})")

    raw = resp.json()
    if not isinstance(raw, list):
        raw = []

    results = []
    for d in raw:
        results.append({
            "bin": d.get("bin"),
            "name_ru": d.get("nameru"),
            "status": d.get("statusru"),
            "type": None,
            "fio": d.get("director"),
            "registration_date": d.get("datereg"),
            "address_ru": d.get("addressru"),
            "oked": None,
            "oked_name_ru": d.get("okedru"),
            "krp": None,
            "krp_name_ru": None,
        })
    return results


@router.get("/search")
async def search_company(q: str = Query(..., min_length=2, description="БИН, название или ФИО руководителя")):
    """
    Поиск юрлиц/ИП Казахстана. Источник переключается переменной
    LEGAL_ENTITY_SOURCE в .env — "dadata" (по умолчанию) или "egov".
    """
    if LEGAL_ENTITY_SOURCE == "egov":
        results = await _search_egov(q)
    else:
        results = await _search_dadata(q)

    return {"query": q, "count": len(results), "results": results, "source": LEGAL_ENTITY_SOURCE}
