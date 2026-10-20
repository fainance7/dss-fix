"""
Тесты CRUD/bulk/timeseries/validate для indicator_values.
Покрытие: создание, listing, фильтрация, RBAC по направлению, валидация.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.data import Direction
from app.models.user import Region, Role, User, UserDirection, UserRole
from app.core.security import hash_password

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────

async def _create_unit(client: AsyncClient, token: str, name: str) -> str:
    resp = await client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": name, "is_active": True},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def _get_direction_id(client: AsyncClient, token: str, code: str) -> str:
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


async def _create_region(engine, code: str, name: str) -> str:
    """Create region via a fresh session on the test engine to avoid
    interleaving with the client's session (asyncpg single-conn-in-flight)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        region = Region(code=code, name=name, level=1)
        s.add(region)
        await s.flush()
        rid = str(region.id)
        await s.commit()
    return rid


async def _make_validate_bulk_check(client, token, ids, status):
    resp = await client.post(
        "/api/v1/indicator-values/validate-bulk",
        headers={"Authorization": f"Bearer {token}"},
        json={"ids": ids, "status": status},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_value_payload(
    indicator_id: str, region_id: str, year: int = 2024, value: float = 100.0
) -> dict:
    return {
        "indicator_id": indicator_id,
        "region_id": region_id,
        "period_type": "year",
        "period_start": f"{year}-01-01",
        "period_end": f"{year}-12-31",
        "value": value,
    }


# ── tests ──────────────────────────────────────────────────────────────────

class TestIndicatorValueCRUD:
    async def test_create_and_list(
        self,
        client: AsyncClient,
        engine,
        admin_token: str,
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Тест-район")

        payload = await _make_value_payload(ind_id, region_id, 2024, 123.45)
        resp = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["value"] == 123.45
        assert data["validation_status"] == "pending"

        # list contains it
        list_resp = await client.get(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"indicator_id": ind_id, "region_id": region_id},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] == 1

    async def test_unique_constraint(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Дубль-регион")

        payload = await _make_value_payload(ind_id, region_id, 2024, 1.0)
        first = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert first.status_code == 201

        dup = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert dup.status_code == 409

    async def test_period_order_validation(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "ТестОрдер")

        bad = {
            "indicator_id": ind_id,
            "region_id": region_id,
            "period_type": "year",
            "period_start": "2024-12-31",
            "period_end": "2024-01-01",
            "value": 1.0,
        }
        resp = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=bad,
        )
        assert resp.status_code == 400


class TestTimeSeries:
    async def test_returns_ordered_points(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "TS-регион")

        for year, val in [(2022, 1.0), (2023, 2.0), (2021, 0.5), (2024, 3.0)]:
            r = await client.post(
                "/api/v1/indicator-values",
                headers={"Authorization": f"Bearer {admin_token}"},
                json=await _make_value_payload(ind_id, region_id, year, val),
            )
            assert r.status_code == 201

        ts = await client.get(
            "/api/v1/indicator-values/timeseries",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={
                "indicator_id": ind_id,
                "region_id": region_id,
                "period_type": "year",
            },
        )
        assert ts.status_code == 200
        body = ts.json()
        years = [p["period_start"] for p in body["points"]]
        assert years == sorted(years), "points must be sorted by period_start"
        assert len(body["points"]) == 4


class TestValidationFlow:
    async def test_admin_can_approve(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "VR")

        created = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=await _make_value_payload(ind_id, region_id, 2024, 5.0),
        )
        assert created.status_code == 201
        vid = created.json()["id"]

        validate = await client.post(
            f"/api/v1/indicator-values/{vid}/validate",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "approved", "comment": "ok"},
        )
        assert validate.status_code == 200
        assert validate.json()["validation_status"] == "approved"

    async def test_bulk_validate_by_ids(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Bulk")
        ids = []
        for y in range(2020, 2024):
            r = await client.post(
                "/api/v1/indicator-values",
                headers={"Authorization": f"Bearer {admin_token}"},
                json=await _make_value_payload(ind_id, region_id, y, float(y)),
            )
            ids.append(r.json()["id"])
        body = await client.post(
            "/api/v1/indicator-values/validate-bulk",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ids": ids, "status": "approved"},
        )
        assert body.status_code == 200
        assert body.json()["updated"] == 4

    async def test_bulk_validate_requires_ids_or_job(
        self, client: AsyncClient, admin_token: str
    ):
        resp = await client.post(
            "/api/v1/indicator-values/validate-bulk",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "approved"},
        )
        assert resp.status_code == 400


class TestBulk:
    async def test_bulk_insert(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "economy")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "BR")

        items = [
            await _make_value_payload(ind_id, region_id, year=y, value=float(y))
            for y in (2020, 2021, 2022, 2023, 2024)
        ]
        resp = await client.post(
            "/api/v1/indicator-values/bulk",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"items": items},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["inserted"] == 5

        # idempotent re-run with new values → same count, no errors
        items2 = [
            await _make_value_payload(ind_id, region_id, year=y, value=float(y) * 2)
            for y in (2020, 2021, 2022, 2023, 2024)
        ]
        resp2 = await client.post(
            "/api/v1/indicator-values/bulk",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"items": items2},
        )
        assert resp2.status_code == 200
        assert resp2.json()["skipped"] == 0


class TestRBAC:
    async def _make_exp_dir_user(
        self, engine, allowed_direction_code: str, username: str
    ) -> tuple[str, str]:
        """Returns (username, password) for an exp_dir user pinned to one direction.
        Uses a fresh session on the test engine to avoid asyncpg interleaving."""
        factory = async_sessionmaker(engine, expire_on_commit=False)
        password = "ExpDirPass123!"
        async with factory() as s:
            role = (await s.execute(select(Role).where(Role.name == "exp_dir"))).scalar_one_or_none()
            if not role:
                role = Role(name="exp_dir", description="exp_dir")
                s.add(role)
                await s.flush()
            user = User(
                username=username,
                email=f"{username}@example.com",
                password_hash=hash_password(password),
            )
            s.add(user)
            await s.flush()
            s.add(UserRole(user_id=user.id, role_id=role.id, region_id=None))
            d = (
                await s.execute(select(Direction).where(Direction.code == allowed_direction_code))
            ).scalar_one()
            s.add(UserDirection(user_id=user.id, direction_id=d.id))
            await s.commit()
        return username, password

    async def test_exp_dir_cannot_create_outside_direction(
        self, client: AsyncClient, engine, admin_token: str
    ):
        # Setup: economy indicator + region
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        eco_dir_id = await _get_direction_id(client, admin_token, "economy")
        eco_ind = await _create_indicator(
            client, admin_token, eco_dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "RBAC-R")

        # exp_dir user pinned to climate (not economy)
        uname, pwd = await self._make_exp_dir_user(
            engine, "climate", f"climateexp_{uuid.uuid4().hex[:6]}"
        )
        login = await client.post("/api/v1/auth/login", json={"username": uname, "password": pwd})
        assert login.status_code == 200
        token = login.json()["access_token"]

        resp = await client.post(
            "/api/v1/indicator-values",
            headers={"Authorization": f"Bearer {token}"},
            json=await _make_value_payload(eco_ind, region_id, 2024, 1.0),
        )
        assert resp.status_code == 403
