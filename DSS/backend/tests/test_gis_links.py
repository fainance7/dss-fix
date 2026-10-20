"""Тесты связей каталога GIS с доменом DSS (/api/v1/gis-links).

Категория GIS → направление, датасет GIS → показатель. Проверяем upsert
(идемпотентность + перепривязка), листинг, delete, валидацию и RBAC.
"""
import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────

async def _get_direction_id(client: AsyncClient, token: str, code: str = "economy") -> str:
    resp = await client.get(
        "/api/v1/directions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    for d in resp.json():
        if d["code"] == code:
            return d["id"]
    raise AssertionError(f"Direction {code} not found")


async def _create_unit(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "is_active": True},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def _create_indicator(
    client: AsyncClient, token: str, direction_id: str, unit_id: str, name: str
) -> str:
    resp = await client.post(
        "/api/v1/indicators",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "direction_id": direction_id,
            "unit_id": unit_id,
            "name": name,
            "sort_order": 1,
            "is_active": True,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Category links ───────────────────────────────────────────────────────────

class TestCategoryLinks:
    async def test_upsert_and_list(self, client: AsyncClient, admin_token: str):
        dir_id = await _get_direction_id(client, admin_token)
        gis_cat = str(uuid.uuid4())

        resp = await client.put(
            "/api/v1/gis-links/categories",
            headers=_h(admin_token),
            json={
                "gis_category_id": gis_cat,
                "direction_id": dir_id,
                "gis_category_code": "land",
                "gis_category_name": "Земельные ресурсы",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["gis_category_id"] == gis_cat
        assert body["direction_id"] == dir_id
        assert body["direction_name"]

        resp = await client.get("/api/v1/gis-links/categories", headers=_h(admin_token))
        assert resp.status_code == 200
        assert any(x["gis_category_id"] == gis_cat for x in resp.json())

    async def test_upsert_idempotent_no_duplicate(self, client: AsyncClient, admin_token: str):
        dir_id = await _get_direction_id(client, admin_token)
        gis_cat = str(uuid.uuid4())
        payload = {"gis_category_id": gis_cat, "direction_id": dir_id}

        first = await client.put(
            "/api/v1/gis-links/categories", headers=_h(admin_token), json=payload
        )
        second = await client.put(
            "/api/v1/gis-links/categories", headers=_h(admin_token), json=payload
        )
        assert first.status_code == 200 and second.status_code == 200
        # тот же ряд (один и тот же id) — дубля нет
        assert first.json()["id"] == second.json()["id"]

        resp = await client.get("/api/v1/gis-links/categories", headers=_h(admin_token))
        matches = [x for x in resp.json() if x["gis_category_id"] == gis_cat]
        assert len(matches) == 1

    async def test_delete(self, client: AsyncClient, admin_token: str):
        dir_id = await _get_direction_id(client, admin_token)
        gis_cat = str(uuid.uuid4())
        await client.put(
            "/api/v1/gis-links/categories",
            headers=_h(admin_token),
            json={"gis_category_id": gis_cat, "direction_id": dir_id},
        )
        resp = await client.delete(
            f"/api/v1/gis-links/categories/{gis_cat}", headers=_h(admin_token)
        )
        assert resp.status_code == 204, resp.text
        resp = await client.get("/api/v1/gis-links/categories", headers=_h(admin_token))
        assert not any(x["gis_category_id"] == gis_cat for x in resp.json())

    async def test_unknown_direction_rejected(self, client: AsyncClient, admin_token: str):
        resp = await client.put(
            "/api/v1/gis-links/categories",
            headers=_h(admin_token),
            json={"gis_category_id": str(uuid.uuid4()), "direction_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 400, resp.text


# ── Dataset links ────────────────────────────────────────────────────────────

class TestDatasetLinks:
    async def test_upsert_rebind_and_filter(self, client: AsyncClient, admin_token: str):
        dir_id = await _get_direction_id(client, admin_token)
        unit_id = await _create_unit(client, admin_token, f"ед-{uuid.uuid4().hex[:6]}")
        ind1 = await _create_indicator(client, admin_token, dir_id, unit_id, f"Показатель A {uuid.uuid4().hex[:6]}")
        ind2 = await _create_indicator(client, admin_token, dir_id, unit_id, f"Показатель B {uuid.uuid4().hex[:6]}")
        gis_ds = str(uuid.uuid4())
        gis_cat = str(uuid.uuid4())

        resp = await client.put(
            "/api/v1/gis-links/datasets",
            headers=_h(admin_token),
            json={
                "gis_dataset_id": gis_ds,
                "indicator_id": ind1,
                "gis_dataset_name": "Пастбища 2023",
                "gis_category_id": gis_cat,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["indicator_id"] == ind1
        assert resp.json()["direction_id"] == dir_id

        # перепривязка к другому показателю — тот же ряд
        resp2 = await client.put(
            "/api/v1/gis-links/datasets",
            headers=_h(admin_token),
            json={"gis_dataset_id": gis_ds, "indicator_id": ind2, "gis_category_id": gis_cat},
        )
        assert resp2.status_code == 200
        assert resp2.json()["indicator_id"] == ind2
        assert resp2.json()["id"] == resp.json()["id"]

        # фильтр по gis_category_id
        resp3 = await client.get(
            "/api/v1/gis-links/datasets",
            headers=_h(admin_token),
            params={"gis_category_id": gis_cat},
        )
        assert resp3.status_code == 200
        ids = [x["gis_dataset_id"] for x in resp3.json()]
        assert gis_ds in ids

    async def test_delete(self, client: AsyncClient, admin_token: str):
        dir_id = await _get_direction_id(client, admin_token)
        unit_id = await _create_unit(client, admin_token, f"ед-{uuid.uuid4().hex[:6]}")
        ind = await _create_indicator(client, admin_token, dir_id, unit_id, f"Показатель D {uuid.uuid4().hex[:6]}")
        gis_ds = str(uuid.uuid4())
        await client.put(
            "/api/v1/gis-links/datasets",
            headers=_h(admin_token),
            json={"gis_dataset_id": gis_ds, "indicator_id": ind},
        )
        resp = await client.delete(
            f"/api/v1/gis-links/datasets/{gis_ds}", headers=_h(admin_token)
        )
        assert resp.status_code == 204, resp.text

    async def test_unknown_indicator_rejected(self, client: AsyncClient, admin_token: str):
        resp = await client.put(
            "/api/v1/gis-links/datasets",
            headers=_h(admin_token),
            json={"gis_dataset_id": str(uuid.uuid4()), "indicator_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 400, resp.text


# ── RBAC ─────────────────────────────────────────────────────────────────────

class TestGisLinksRbac:
    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/gis-links/categories")
        assert resp.status_code in (401, 403)

    async def test_data_uploader_allowed(self, client: AsyncClient, data_uploader_token: str):
        resp = await client.get("/api/v1/gis-links/categories", headers=_h(data_uploader_token))
        assert resp.status_code == 200
