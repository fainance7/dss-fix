"""Журнал аудита: admin видит всё, data_uploader — только записи о загрузке данных."""
import pytest
from httpx import AsyncClient

from app.models.user import AuditLog, User


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def audit_rows(db, admin_user: User):
    db.add_all([
        AuditLog(user_id=admin_user.id, action="upload", entity_type="data_intake_job",
                 details={"rows_invalid": 3}),
        AuditLog(user_id=admin_user.id, action="update", entity_type="user"),
    ])
    await db.commit()


@pytest.mark.asyncio
async def test_admin_sees_all(client: AsyncClient, admin_token: str, audit_rows):
    resp = await client.get("/api/v1/audit-log", headers=_h(admin_token))
    assert resp.status_code == 200
    types = {i["entity_type"] for i in resp.json()["items"]}
    assert {"data_intake_job", "user"} <= types


@pytest.mark.asyncio
async def test_data_uploader_sees_only_intake(
    client: AsyncClient, data_uploader_token: str, audit_rows
):
    resp = await client.get("/api/v1/audit-log", headers=_h(data_uploader_token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items and all(i["entity_type"] == "data_intake_job" for i in items)
    assert items[0]["details"] == {"rows_invalid": 3}

    # явный фильтр на чужую сущность не расширяет область видимости
    resp = await client.get(
        "/api/v1/audit-log", params={"entity_type": "user"}, headers=_h(data_uploader_token)
    )
    assert resp.json()["items"] == []

    actions = await client.get("/api/v1/audit-log/actions", headers=_h(data_uploader_token))
    assert actions.json() == ["upload"]


@pytest.mark.asyncio
async def test_expert_forbidden(client: AsyncClient, expert_token: str):
    resp = await client.get("/api/v1/audit-log", headers=_h(expert_token))
    assert resp.status_code == 403
