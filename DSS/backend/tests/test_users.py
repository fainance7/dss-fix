"""
Users CRUD tests.
"""
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestUsersCRUD:
    async def test_create_and_get_user(self, client: AsyncClient, admin_token: str):
        # Create
        create_resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "crud_test_user",
                "email": "crud@example.com",
                "password": "CrudPass123!",
            },
        )
        assert create_resp.status_code == 201
        user_id = create_resp.json()["id"]

        # Get by ID
        get_resp = await client.get(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["username"] == "crud_test_user"
        assert get_resp.json()["is_active"] is True
        assert get_resp.json()["roles"] == []

    async def test_update_user(self, client: AsyncClient, admin_token: str):
        create_resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "update_test_user",
                "email": "update@example.com",
                "password": "UpdatePass123!",
            },
        )
        user_id = create_resp.json()["id"]

        # Block user
        update_resp = await client.patch(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"is_active": False},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["is_active"] is False

    async def test_delete_user(self, client: AsyncClient, admin_token: str):
        create_resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "username": "delete_test_user",
                "email": "delete_test@example.com",
                "password": "DeletePass123!",
            },
        )
        user_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert del_resp.status_code == 204

        # Verify gone
        get_resp = await client.get(
            f"/api/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_resp.status_code == 404

    async def test_duplicate_username_rejected(self, client: AsyncClient, admin_token: str):
        payload = {
            "username": "dup_user_xyz",
            "email": "dup1@example.com",
            "password": "DupPass123!",
        }
        resp1 = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert resp1.status_code == 201

        payload2 = {**payload, "email": "dup2@example.com"}
        resp2 = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload2,
        )
        assert resp2.status_code == 409

    async def test_get_nonexistent_user(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/v1/users/00000000-0000-0000-0000-000000000000",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    async def test_list_users_pagination(self, client: AsyncClient, admin_token: str):
        resp = await client.get(
            "/api/v1/users?page=1&page_size=5",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 5
        assert data["page"] == 1
        assert data["page_size"] == 5

    async def test_create_user_password_validation(self, client: AsyncClient, admin_token: str):
        """Password shorter than 8 chars must be rejected."""
        resp = await client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"username": "badpass_user", "email": "badpass@example.com", "password": "short"},
        )
        assert resp.status_code == 422
