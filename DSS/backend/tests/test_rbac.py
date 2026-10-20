"""
RBAC tests: проверка доступа по ролям к эндпоинтам.
"""
import uuid
from datetime import date

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestRBACUsers:
    """Только admin может управлять пользователями."""

    async def test_admin_can_list_users(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    async def test_expert_cannot_list_users(self, client: AsyncClient, expert_token: str):
        resp = await client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {expert_token}"},
        )
        assert resp.status_code == 403

    async def test_unauthenticated_cannot_list_users(self, client: AsyncClient):
        resp = await client.get("/api/v1/users")
        assert resp.status_code == 401

    async def test_admin_can_create_user(self, client: AsyncClient, admin_token: str):
        resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "rbac_test_user",
                "email": "rbac_test@example.com",
                "password": "TestPass123!",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["username"] == "rbac_test_user"

    async def test_expert_cannot_create_user(self, client: AsyncClient, expert_token: str):
        resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={
                "username": "should_not_create",
                "email": "nope@example.com",
                "password": "TestPass123!",
            },
        )
        assert resp.status_code == 403

    async def test_admin_can_assign_role(
        self, client: AsyncClient, admin_token: str, regular_user, roles
    ):
        resp = await client.post(
            f"/api/v1/users/{regular_user.id}/roles",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role_name": "exp_dir", "region_id": None},
        )
        # 200 OK or 409 if already assigned
        assert resp.status_code in (200, 409)

    async def test_expert_cannot_assign_role(
        self, client: AsyncClient, expert_token: str, regular_user
    ):
        resp = await client.post(
            f"/api/v1/users/{regular_user.id}/roles",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"role_name": "admin", "region_id": None},
        )
        assert resp.status_code == 403


class TestRBACProtectedRoutes:
    """Проверка что все защищённые маршруты требуют auth."""

    async def test_health_is_public(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_docs_accessible(self, client: AsyncClient):
        resp = await client.get("/docs")
        assert resp.status_code == 200


# ── helpers для data_uploader-сценариев ─────────────────────────────────


async def _create_unit(client: AsyncClient, token: str) -> str:
    resp = await client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": f"u_{uuid.uuid4().hex[:8]}", "is_active": True},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


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


async def _create_indicator(
    client: AsyncClient, token: str, direction_id: str, unit_id: str
) -> str:
    resp = await client.post(
        "/api/v1/indicators",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "direction_id": direction_id,
            "unit_id": unit_id,
            "name": f"ind_{uuid.uuid4().hex[:8]}",
            "sort_order": 1,
            "is_active": True,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def _create_region(engine, code_prefix: str = "R"):
    """Создаёт регион через прямую сессию (как в test_indicator_values)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models.user import Region

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        region = Region(
            code=f"{code_prefix}{uuid.uuid4().hex[:6]}",
            name=f"reg_{uuid.uuid4().hex[:6]}",
            level=1,
        )
        s.add(region)
        await s.flush()
        rid = str(region.id)
        await s.commit()
    return rid


# ── data_uploader RBAC ─────────────────────────────────────────────────


class TestRBACDataUploader:
    """data_uploader получает доступ к mappings/remote/indicator-values."""

    async def test_can_list_mappings(
        self, client: AsyncClient, data_uploader_token: str
    ):
        resp = await client.get(
            "/api/v1/data-intake/mappings",
            headers={"Authorization": f"Bearer {data_uploader_token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_can_create_mapping(
        self,
        client: AsyncClient,
        engine,
        admin_token: str,
        data_uploader_token: str,
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models.kato import KatoReference

        # admin создаёт unit + indicator (data_uploader на это прав не имеет)
        unit_id = await _create_unit(client, admin_token)
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(client, admin_token, dir_id, unit_id)

        # seed kato_reference (FK у intake_mappings.kato_constant)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            kato_code = "590000000"
            s.add(
                KatoReference(
                    kato_code=kato_code, name_ru="СКО", level=1, is_active=True
                )
            )
            await s.commit()

        payload = {
            "remote_table": "tbl_remote_test",
            "indicator_id": ind_id,
            "region_resolution_kind": "constant",
            "kato_constant": kato_code,
            "period_kind": "yearly",
            "year_column_idx": 0,
            "period_columns_idx": [{"index": 1, "marker": "01"}],
            "skip_if_first_col_in": [],
        }
        resp = await client.post(
            "/api/v1/data-intake/mappings",
            headers={"Authorization": f"Bearer {data_uploader_token}"},
            json=payload,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["remote_table"] == "tbl_remote_test"
        assert body["indicator_id"] == ind_id

    async def test_can_list_remote_tables(
        self, client: AsyncClient, data_uploader_token: str
    ):
        """503 / 200 допустимы (reports_db может быть недоступна),
        но 403 — недопустимо."""
        resp = await client.get(
            "/api/v1/data-intake/remote/tables",
            headers={"Authorization": f"Bearer {data_uploader_token}"},
        )
        assert resp.status_code != 403, resp.text
        assert resp.status_code in (200, 503)

    async def test_list_indicator_values_visible_to_data_uploader(
        self,
        client: AsyncClient,
        engine,
        admin_token: str,
        data_uploader_token: str,
        data_uploader_user,
    ):
        """data_uploader видит все значения (own/foreign × pending/approved)."""
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models.data import IndicatorValue

        unit_id = await _create_unit(client, admin_token)
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(client, admin_token, dir_id, unit_id)
        region_id = await _create_region(engine)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            own_pending = IndicatorValue(
                indicator_id=ind_id,
                region_id=region_id,
                period_type="year",
                period_start=date(2021, 1, 1),
                period_end=date(2021, 12, 31),
                value=1.0,
                validation_status="pending",
                created_by=data_uploader_user.id,
            )
            others_approved = IndicatorValue(
                indicator_id=ind_id,
                region_id=region_id,
                period_type="year",
                period_start=date(2022, 1, 1),
                period_end=date(2022, 12, 31),
                value=2.0,
                validation_status="approved",
            )
            others_pending = IndicatorValue(
                indicator_id=ind_id,
                region_id=region_id,
                period_type="year",
                period_start=date(2023, 1, 1),
                period_end=date(2023, 12, 31),
                value=3.0,
                validation_status="pending",
            )
            s.add_all([own_pending, others_approved, others_pending])
            await s.flush()
            expected = {str(own_pending.id), str(others_approved.id), str(others_pending.id)}
            await s.commit()

        resp = await client.get(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {data_uploader_token}"},
            params={"indicator_id": ind_id, "region_id": region_id},
        )
        assert resp.status_code == 200, resp.text
        ids = {it["id"] for it in resp.json()["items"]}
        assert expected.issubset(ids)
