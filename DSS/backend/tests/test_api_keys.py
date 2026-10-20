"""CRUD api-ключей для intake-portal — admin-only."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models.api_key import ApiKey
from app.models.user import Role, User, UserRole

pytestmark = pytest.mark.asyncio


async def _ensure_role(db, name: str) -> Role:
    role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        role = Role(name=name, description=name)
        db.add(role)
        await db.flush()
        await db.commit()
    return role


async def _make_user(db, username: str, *, role_name: str | None = None, active: bool = True) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Pass1234!"),
        is_active=active,
    )
    db.add(user)
    await db.flush()
    if role_name:
        role = await _ensure_role(db, role_name)
        db.add(UserRole(user_id=user.id, role_id=role.id, region_id=None))
    await db.commit()
    return user


class TestApiKeysCrud:
    async def test_admin_creates_key_returns_plain_once(
        self, client: AsyncClient, db, admin_token: str
    ):
        uploader = await _make_user(db, "uploader_a", role_name="data_uploader")

        resp = await client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_id": str(uploader.id), "name": "adjacent-team"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "adjacent-team"
        assert body["user_id"] == str(uploader.id)
        assert body["is_active"] is True
        # plain_key обязан быть, ≥ 30 символов (token_urlsafe(32) → ~43)
        assert isinstance(body.get("plain_key"), str)
        assert len(body["plain_key"]) >= 30

        # list — plain быть НЕ должно
        list_resp = await client.get(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert len(items) == 1
        assert "plain_key" not in items[0]
        assert "key_hash" not in items[0]

    async def test_list_filter_by_user(
        self, client: AsyncClient, db, admin_token: str
    ):
        u1 = await _make_user(db, "uploader_b1", role_name="data_uploader")
        u2 = await _make_user(db, "uploader_b2", role_name="data_uploader")
        for u, name in [(u1, "k1"), (u1, "k2"), (u2, "k3")]:
            await client.post(
                "/api/v1/api-keys",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"user_id": str(u.id), "name": name},
            )

        resp = await client.get(
            f"/api/v1/api-keys?user_id={u1.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert {it["name"] for it in items} == {"k1", "k2"}

    async def test_non_admin_forbidden(
        self, client: AsyncClient, db, expert_token: str
    ):
        uploader = await _make_user(db, "uploader_c", role_name="data_uploader")
        resp = await client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {expert_token}"},
            json={"user_id": str(uploader.id), "name": "x"},
        )
        assert resp.status_code == 403

    async def test_revoke_marks_inactive(
        self, client: AsyncClient, db, admin_token: str, engine
    ):
        uploader = await _make_user(db, "uploader_d", role_name="data_uploader")
        create_resp = await client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_id": str(uploader.id), "name": "tmp"},
        )
        key_id = create_resp.json()["id"]

        rev = await client.delete(
            f"/api/v1/api-keys/{key_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert rev.status_code == 204

        from sqlalchemy.ext.asyncio import async_sessionmaker
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            row = (
                await s.execute(select(ApiKey).where(ApiKey.id == key_id))
            ).scalar_one()
            assert row.is_active is False

    async def test_create_for_unknown_user_404(
        self, client: AsyncClient, admin_token: str
    ):
        import uuid
        resp = await client.post(
            "/api/v1/api-keys",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"user_id": str(uuid.uuid4()), "name": "ghost"},
        )
        assert resp.status_code == 404
