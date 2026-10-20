"""
TC-AUTH-001 — TC-AUTH-007: Auth flow tests.
"""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestLogin:
    """TC-AUTH-001: Успешный вход."""
    async def test_login_success(self, client: AsyncClient, admin_user):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "test_admin", "password": "AdminPass123!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    """TC-AUTH-002: Неверный пароль → 401."""
    async def test_login_wrong_password(self, client: AsyncClient, admin_user):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "test_admin", "password": "WrongPassword!"},
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]

    """TC-AUTH-003: Несуществующий пользователь → 401."""
    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "ghost_user", "password": "SomePass123!"},
        )
        assert resp.status_code == 401

    """TC-AUTH-004: Заблокированный пользователь → 403."""
    async def test_login_inactive_user(self, client: AsyncClient, db, roles):
        from sqlalchemy import select
        from app.core.security import hash_password
        from app.models.user import User, UserRole

        # Create inactive user
        user = User(
            username="blocked_user",
            email="blocked@example.com",
            password_hash=hash_password("BlockedPass123!"),
            is_active=False,
        )
        db.add(user)
        await db.flush()
        ur = UserRole(user_id=user.id, role_id=roles["exp_ver"].id, region_id=None)
        db.add(ur)
        await db.commit()

        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "blocked_user", "password": "BlockedPass123!"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"]

    async def test_login_empty_credentials(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "", "password": ""},
        )
        # Empty username won't match any user → 401
        assert resp.status_code == 401


class TestRefreshToken:
    """TC-AUTH-005: Обновление access_token через refresh_token."""
    async def test_refresh_success(self, client: AsyncClient, admin_user):
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "test_admin", "password": "AdminPass123!"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_refresh_invalid_token(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert resp.status_code == 401

    async def test_refresh_with_access_token(self, client: AsyncClient, admin_token: str):
        """Access token must NOT work as refresh token."""
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": admin_token},
        )
        assert resp.status_code == 401


class TestMe:
    """TC-AUTH-006: GET /auth/me — получение текущего пользователя."""
    async def test_me_success(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "test_admin"
        assert data["is_active"] is True
        assert isinstance(data["roles"], list)
        assert any(r["name"] == "admin" for r in data["roles"])

    """TC-AUTH-007: Доступ без токена → 401 (интерфейс на 401 обновляет сеанс)."""
    async def test_me_no_token(self, client: AsyncClient):
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer completely.invalid.jwt"},
        )
        assert resp.status_code == 401
