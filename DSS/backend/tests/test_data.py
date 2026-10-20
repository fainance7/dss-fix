"""
Ф2.11: Тесты CRUD данных, фильтрация, RBAC СХД.
"""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ── Fixtures helpers ──────────────────────────────────────────────────────────

async def _create_source(client: AsyncClient, token: str, name: str = "ЦСУ РК") -> dict:
    resp = await client.post(
        "/api/v1/data-sources",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "description": "Центральное статистическое управление", "is_active": True},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_category(client: AsyncClient, token: str, name: str = "Демография") -> dict:
    resp = await client.post(
        "/api/v1/data-categories",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "description": "Демографические показатели"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _get_direction_id(client: AsyncClient, token: str, code: str = "economy") -> str:
    resp = await client.get(
        "/api/v1/directions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    for d in resp.json():
        if d["code"] == code:
            return d["id"]
    raise AssertionError(f"Direction {code} not found")


async def _create_layer(client: AsyncClient, token: str, category_id: str, source_id: str, name: str = "Население СКО") -> dict:
    direction_id = await _get_direction_id(client, token)
    resp = await client.post(
        "/api/v1/data-layers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": name,
            "category_id": category_id,
            "source_id": source_id,
            "direction_id": direction_id,
            "valid_from": "2020-01-01",
            "status": "active",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_spatial(client: AsyncClient, token: str, layer_id: str, data_date: str = "2026-01-01") -> dict:
    resp = await client.post(
        "/api/v1/spatial-data",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "layer_id": layer_id,
            "data_date": data_date,
            "geojson": {"type": "Point", "coordinates": [69.13, 54.87]},
            "properties": {"population": 590000, "district": "Петропавловск"},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── DataSource CRUD ───────────────────────────────────────────────────────────

class TestDataSourceCRUD:
    async def test_create_and_get(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "Test Source CRUD")
        assert src["name"] == "Test Source CRUD"
        assert src["is_active"] is True

        resp = await client.get(
            f"/api/v1/data-sources/{src['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == src["id"]

    async def test_list_sources(self, client: AsyncClient, admin_token: str):
        await _create_source(client, admin_token, "SourceList1")
        resp = await client.get(
            "/api/v1/data-sources",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    async def test_update_source(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "UpdateMe")
        resp = await client.patch(
            f"/api/v1/data-sources/{src['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_delete_source(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "DeleteMe")
        del_resp = await client.delete(
            f"/api/v1/data-sources/{src['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_resp.status_code == 204

        get_resp = await client.get(
            f"/api/v1/data-sources/{src['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_resp.status_code == 404

    async def test_expert_can_create_source(self, client: AsyncClient, expert_token: str):
        """exp_ver может создавать источники данных."""
        resp = await client.post(
            "/api/v1/data-sources",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"name": "Expert Source", "is_active": True},
        )
        assert resp.status_code == 201

    async def test_expert_cannot_delete_source(self, client: AsyncClient, admin_token: str, expert_token: str):
        src = await _create_source(client, admin_token, "ExpertCannotDelete")
        resp = await client.delete(
            f"/api/v1/data-sources/{src['id']}",
            headers={"Authorization": f"Bearer {expert_token}"},
        )
        assert resp.status_code == 403


# ── DataCategory CRUD ─────────────────────────────────────────────────────────

class TestDataCategoryCRUD:
    async def test_create_category(self, client: AsyncClient, admin_token: str):
        cat = await _create_category(client, admin_token, "ЭкономикаТест")
        assert cat["name"] == "ЭкономикаТест"
        assert cat["parent_id"] is None

    async def test_create_child_category(self, client: AsyncClient, admin_token: str):
        parent = await _create_category(client, admin_token, "РодительКат")
        resp = await client.post(
            "/api/v1/data-categories",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "ДочернийКат", "parent_id": parent["id"]},
        )
        assert resp.status_code == 201
        assert resp.json()["parent_id"] == parent["id"]

    async def test_duplicate_name_rejected(self, client: AsyncClient, admin_token: str):
        await _create_category(client, admin_token, "УникальноеИмяКат")
        resp = await client.post(
            "/api/v1/data-categories",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "УникальноеИмяКат"},
        )
        assert resp.status_code == 409


# ── DataLayer CRUD ────────────────────────────────────────────────────────────

class TestDataLayerCRUD:
    async def test_create_and_get_layer(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "LayerSrc")
        cat = await _create_category(client, admin_token, "LayerCat")
        layer = await _create_layer(client, admin_token, cat["id"], src["id"])
        assert layer["name"] == "Население СКО"
        assert layer["status"] == "active"
        assert layer["category_name"] == "LayerCat"
        assert layer["source_name"] == "LayerSrc"

    async def test_list_layers_filter_status(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "FilterSrc")
        cat = await _create_category(client, admin_token, "FilterCat")
        await _create_layer(client, admin_token, cat["id"], src["id"], "ActiveLayer")

        resp = await client.get(
            "/api/v1/data-layers?status=active",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert all(l["status"] == "active" for l in resp.json()["items"])

    async def test_layer_history_created(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "HistSrc")
        cat = await _create_category(client, admin_token, "HistCat")
        layer = await _create_layer(client, admin_token, cat["id"], src["id"], "HistLayer")

        resp = await client.get(
            f"/api/v1/data-layers/{layer['id']}/history",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        history = resp.json()
        assert len(history) >= 1
        assert history[0]["change_type"] == "create"

    async def test_layer_invalid_category(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "BadCatSrc")
        direction_id = await _get_direction_id(client, admin_token)
        resp = await client.post(
            "/api/v1/data-layers",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "name": "BadLayer",
                "category_id": "00000000-0000-0000-0000-000000000000",
                "source_id": src["id"],
                "direction_id": direction_id,
                "valid_from": "2020-01-01",
            },
        )
        assert resp.status_code == 404


# ── SpatialData CRUD + фильтрация ─────────────────────────────────────────────

class TestSpatialDataCRUD:
    async def _setup(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "SpatialSrc")
        cat = await _create_category(client, admin_token, "SpatialCat")
        layer = await _create_layer(client, admin_token, cat["id"], src["id"], "SpatialLayer")
        return layer

    async def test_create_spatial_data(self, client: AsyncClient, admin_token: str):
        layer = await self._setup(client, admin_token)
        sd = await _create_spatial(client, admin_token, layer["id"])
        assert sd["layer_id"] == layer["id"]
        assert sd["validation_status"] == "pending"
        assert sd["geojson"]["type"] == "Point"
        assert sd["properties"]["population"] == 590000

    async def test_filter_by_layer(self, client: AsyncClient, admin_token: str):
        layer = await self._setup(client, admin_token)
        await _create_spatial(client, admin_token, layer["id"], "2026-01-01")
        await _create_spatial(client, admin_token, layer["id"], "2026-02-01")

        resp = await client.get(
            f"/api/v1/spatial-data?layer_id={layer['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 2

    async def test_filter_by_date_range(self, client: AsyncClient, admin_token: str):
        layer = await self._setup(client, admin_token)
        await _create_spatial(client, admin_token, layer["id"], "2025-06-01")
        await _create_spatial(client, admin_token, layer["id"], "2025-12-01")
        await _create_spatial(client, admin_token, layer["id"], "2026-03-01")

        resp = await client.get(
            f"/api/v1/spatial-data?layer_id={layer['id']}&date_from=2025-01-01&date_to=2025-12-31",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all("2025" in i["data_date"] for i in items)

    async def test_filter_by_validation_status(self, client: AsyncClient, admin_token: str):
        layer = await self._setup(client, admin_token)
        await _create_spatial(client, admin_token, layer["id"])

        resp = await client.get(
            f"/api/v1/spatial-data?validation_status=pending",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert all(i["validation_status"] == "pending" for i in resp.json()["items"])

    async def test_invalid_geojson_rejected(self, client: AsyncClient, admin_token: str):
        layer = await self._setup(client, admin_token)
        resp = await client.post(
            "/api/v1/spatial-data",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "layer_id": layer["id"],
                "data_date": "2026-01-01",
                "geojson": {"type": "Invalid", "coordinates": []},
            },
        )
        assert resp.status_code == 422


# ── Validation workflow (RBAC) ────────────────────────────────────────────────

class TestValidationRBAC:
    async def _setup_and_create(self, client: AsyncClient, admin_token: str):
        src = await _create_source(client, admin_token, "ValSrc")
        cat = await _create_category(client, admin_token, "ValCat")
        layer = await _create_layer(client, admin_token, cat["id"], src["id"], "ValLayer")
        sd = await _create_spatial(client, admin_token, layer["id"])
        return sd

    async def test_exp_ver_can_approve(self, client: AsyncClient, admin_token: str, expert_token: str):
        sd = await self._setup_and_create(client, admin_token)
        resp = await client.post(
            f"/api/v1/spatial-data/{sd['id']}/validate",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"status": "approved"},
        )
        assert resp.status_code == 200
        assert resp.json()["validation_status"] == "approved"

    async def test_admin_can_reject(self, client: AsyncClient, admin_token: str):
        sd = await self._setup_and_create(client, admin_token)
        resp = await client.post(
            f"/api/v1/spatial-data/{sd['id']}/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "rejected", "comment": "Данные не соответствуют стандарту"},
        )
        assert resp.status_code == 200
        assert resp.json()["validation_status"] == "rejected"

    async def test_cannot_validate_twice(self, client: AsyncClient, admin_token: str, expert_token: str):
        sd = await self._setup_and_create(client, admin_token)
        # First validation
        await client.post(
            f"/api/v1/spatial-data/{sd['id']}/validate",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"status": "approved"},
        )
        # Second attempt must fail
        resp = await client.post(
            f"/api/v1/spatial-data/{sd['id']}/validate",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"status": "rejected"},
        )
        assert resp.status_code == 409

    async def test_akim_obl_cannot_validate(self, client: AsyncClient, admin_token: str, db, roles):
        """akim_obl не должен иметь права валидации."""
        from app.core.security import hash_password
        from app.models.user import User, UserRole

        akim = User(username="akim_obl_val_test", email="akim_val@example.com", password_hash=hash_password("AkimPass123!"))
        db.add(akim)
        await db.flush()
        ur = UserRole(user_id=akim.id, role_id=roles["akim_obl"].id, region_id=None)
        db.add(ur)
        await db.commit()

        # Login as akim_obl
        login = await client.post("/api/v1/auth/login", json={"username": "akim_obl_val_test", "password": "AkimPass123!"})
        akim_token = login.json()["access_token"]

        sd = await self._setup_and_create(client, admin_token)
        resp = await client.post(
            f"/api/v1/spatial-data/{sd['id']}/validate",
            headers={"Authorization": f"Bearer {akim_token}"},
            json={"status": "approved"},
        )
        assert resp.status_code == 403


# ── Monitoring ────────────────────────────────────────────────────────────────

class TestMonitoring:
    async def test_sdh_metrics(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/v1/monitoring/sdh",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_sources" in data
        assert "total_layers" in data
        assert "pending_validation" in data
        assert isinstance(data["total_sources"], int)

    async def test_akim_ray_cannot_view_metrics(self, client: AsyncClient, db, roles):
        from app.core.security import hash_password
        from app.models.user import User, UserRole

        ray = User(username="akim_ray_mon", email="ray_mon@example.com", password_hash=hash_password("RayPass123!"))
        db.add(ray)
        await db.flush()
        ur = UserRole(user_id=ray.id, role_id=roles["akim_ray"].id, region_id=None)
        db.add(ur)
        await db.commit()

        login = await client.post("/api/v1/auth/login", json={"username": "akim_ray_mon", "password": "RayPass123!"})
        token = login.json()["access_token"]

        resp = await client.get(
            "/api/v1/monitoring/sdh",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
