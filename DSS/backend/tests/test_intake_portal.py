"""Тесты sub-app /intake-portal.

Покрывает:
- 401 без X-API-Key
- 200 с валидным ключом
- 401 при просроченном ключе
- 401 при неактивном пользователе
- 200 на POST /data-intake/json (валидный batch)
- 403 при попытке data_uploader сделать commit (через основной app)
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.api_key import ApiKey
from app.models.kato import KatoReference
from app.models.user import Region, Role, User, UserRole

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────


async def _ensure_role(db, name: str) -> Role:
    role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(name=name, description=name)
        db.add(role)
        await db.flush()
        await db.commit()
    return role


async def _make_uploader(db, username: str = "intake_uploader", *, active: bool = True) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Pass1234!"),
        is_active=active,
    )
    db.add(user)
    await db.flush()
    role = await _ensure_role(db, "data_uploader")
    db.add(UserRole(user_id=user.id, role_id=role.id, region_id=None))
    await db.commit()
    return user


async def _issue_key(
    db,
    user: User,
    *,
    expires_at: datetime | None = None,
    is_active: bool = True,
    name: str = "test-key",
) -> str:
    """Создаёт ApiKey напрямую (без HTTP). Возвращает plain-ключ."""
    plain = secrets.token_urlsafe(32)
    key = ApiKey(
        key_hash=hashlib.sha256(plain.encode()).hexdigest(),
        user_id=user.id,
        name=name,
        is_active=is_active,
        expires_at=expires_at,
    )
    db.add(key)
    await db.commit()
    return plain


async def _setup_indicator_and_region(client: AsyncClient, engine, admin_token: str):
    """Готовит unit/indicator/region/kato как в test_data_intake (для JSON-batch теста)."""
    unit_name = f"u_{uuid.uuid4().hex[:6]}"
    resp = await client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": unit_name, "is_active": True},
    )
    unit_id = resp.json()["id"]

    dir_resp = await client.get(
        "/api/v1/directions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    economy_dir = next(d for d in dir_resp.json() if d["code"] == "economy")

    indicator_name = f"Показатель_{uuid.uuid4().hex[:6]}"
    ind_resp = await client.post(
        "/api/v1/indicators",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "direction_id": economy_dir["id"],
            "unit_id": unit_id,
            "name": indicator_name,
            "sort_order": 1,
            "is_active": True,
        },
    )
    indicator_id = ind_resp.json()["id"]

    factory = async_sessionmaker(engine, expire_on_commit=False)
    kato_code = f"5910{uuid.uuid4().hex[:5]}"[:9]
    region_code = f"R{uuid.uuid4().hex[:6]}"
    async with factory() as s:
        kato = KatoReference(kato_code=kato_code, name_ru="Тест-район", level=4)
        s.add(kato)
        await s.flush()
        region = Region(code=region_code, name="Тест-район", level=1, kato_code=kato_code)
        s.add(region)
        await s.flush()
        kato.region_id = region.id
        await s.commit()
        region_id = str(region.id)
    return {"indicator_id": indicator_id, "region_id": region_id, "kato_code": kato_code}


# ── tests ──────────────────────────────────────────────────────────────────


class TestIntakePortalAuth:
    async def test_missing_header_returns_401(self, client: AsyncClient):
        resp = await client.get("/intake-portal/api/v1/directions")
        assert resp.status_code == 401
        assert "X-API-Key" in resp.json()["detail"]

    async def test_invalid_key_returns_401(self, client: AsyncClient):
        resp = await client.get(
            "/intake-portal/api/v1/directions",
            headers={"X-API-Key": "definitely-not-a-real-key"},
        )
        assert resp.status_code == 401

    async def test_valid_key_returns_200(self, client: AsyncClient, db):
        uploader = await _make_uploader(db, "intake_ok")
        plain = await _issue_key(db, uploader)
        resp = await client.get(
            "/intake-portal/api/v1/directions",
            headers={"X-API-Key": plain},
        )
        assert resp.status_code == 200, resp.text
        # должны быть 8 фиксированных направлений
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    async def test_expired_key_returns_401(self, client: AsyncClient, db):
        uploader = await _make_uploader(db, "intake_expired")
        plain = await _issue_key(
            db,
            uploader,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        resp = await client.get(
            "/intake-portal/api/v1/directions",
            headers={"X-API-Key": plain},
        )
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    async def test_inactive_user_returns_401(self, client: AsyncClient, db):
        uploader = await _make_uploader(db, "intake_inactive", active=False)
        plain = await _issue_key(db, uploader)
        resp = await client.get(
            "/intake-portal/api/v1/directions",
            headers={"X-API-Key": plain},
        )
        assert resp.status_code == 401

    async def test_revoked_key_returns_401(self, client: AsyncClient, db):
        uploader = await _make_uploader(db, "intake_revoked")
        plain = await _issue_key(db, uploader, is_active=False)
        resp = await client.get(
            "/intake-portal/api/v1/directions",
            headers={"X-API-Key": plain},
        )
        assert resp.status_code == 401


class TestIntakePortalJsonBatch:
    async def test_json_batch_ok(
        self, client: AsyncClient, db, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        uploader = await _make_uploader(db, "intake_batch_ok")
        plain = await _issue_key(db, uploader)

        payload = {
            "rows": [
                {
                    "indicator_id": ctx["indicator_id"],
                    "region_id": ctx["region_id"],
                    "period_type": "year",
                    "period_start": "2023-01-01",
                    "period_end": "2023-12-31",
                    "value": 100.5,
                },
                {
                    "indicator_id": ctx["indicator_id"],
                    "region_id": ctx["region_id"],
                    "period_type": "year",
                    "period_start": "2024-01-01",
                    "period_end": "2024-12-31",
                    "value": 200.0,
                },
            ],
            "source_ref": "adjacent-team-test",
        }
        resp = await client.post(
            "/intake-portal/api/v1/data-intake/json",
            headers={"X-API-Key": plain},
            json=payload,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job"]["rows_total"] == 2
        assert body["job"]["rows_ok"] == 2
        assert body["job"]["rows_invalid"] == 0
        assert body["job"]["source_kind"] == "json_api"
        assert body["job"]["status"] == "validated"

    async def test_json_batch_invalid_indicator(
        self, client: AsyncClient, db, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        uploader = await _make_uploader(db, "intake_batch_bad_ind")
        plain = await _issue_key(db, uploader)

        bogus = str(uuid.uuid4())
        payload = {
            "rows": [
                {
                    "indicator_id": bogus,
                    "region_id": ctx["region_id"],
                    "period_type": "year",
                    "period_start": "2023-01-01",
                    "period_end": "2023-12-31",
                    "value": 1.0,
                }
            ]
        }
        resp = await client.post(
            "/intake-portal/api/v1/data-intake/json",
            headers={"X-API-Key": plain},
            json=payload,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["job"]["rows_invalid"] == 1
        assert body["rows"][0]["status"] == "invalid"
        assert "не найден" in body["rows"][0]["error_message"]

    async def test_data_uploader_cannot_commit_via_portal(
        self, client: AsyncClient, db, engine, admin_token: str
    ):
        """commit требует admin/exp_ver — data_uploader должен получить 403."""
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        uploader = await _make_uploader(db, "intake_no_commit")
        plain = await _issue_key(db, uploader)

        # сначала создаём job через JSON-batch
        payload = {
            "rows": [
                {
                    "indicator_id": ctx["indicator_id"],
                    "region_id": ctx["region_id"],
                    "period_type": "year",
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                    "value": 42.0,
                }
            ]
        }
        up = await client.post(
            "/intake-portal/api/v1/data-intake/json",
            headers={"X-API-Key": plain},
            json=payload,
        )
        job_id = up.json()["job"]["id"]

        # commit через sub-app под api-key с ролью data_uploader → 403
        resp = await client.post(
            f"/intake-portal/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"X-API-Key": plain},
        )
        assert resp.status_code == 403
