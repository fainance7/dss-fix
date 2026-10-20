"""Прокси ландшафтного планирования (`/api/v1/landscape/*`).

GIS-сервис заменён `httpx.MockTransport`: тест видит, дошёл ли запрос до него и
с каким телом. Проверяется разделение прав (чтение — все роли, правка — admin и
data_uploader), белый список слотов, тело запуска и ограничение растровых ручек
датасетами ЛП.
"""
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient

from app.api.v1 import landscape
from app.core.security import hash_password
from app.models.user import User, UserRole

PASSWORD = "LandscapePass123!"
PERIOD = {"start_date": "2025-01-01", "end_date": "2025-12-31"}
SLOT_DATASET = str(uuid.uuid4())
RESULT_DATASET = str(uuid.uuid4())
RESULT_GROUP = str(uuid.uuid4())


@pytest.fixture
def upstream(monkeypatch):
    """Подменяет клиент GIS-сервиса; возвращает список дошедших запросов."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/landscape/slots" and request.method == "GET":
            return httpx.Response(200, json=[{
                "code": "lp-z-wr", "group_id": str(uuid.uuid4()),
                "latest": {"id": SLOT_DATASET, "status": "ready"},
            }])
        if path == "/landscape/results":
            return httpx.Response(200, json=[{
                "group": "lp-v2-balanced", "group_id": RESULT_GROUP,
                "latest": {"id": RESULT_DATASET, "status": "ready"},
                "sld_url": f"/dataset-groups/{RESULT_GROUP}/sld",
            }])
        if path.endswith("/sld"):
            return httpx.Response(200, text="<StyledLayerDescriptor/>")
        if path.startswith("/landscape/slots/") and request.method == "POST":
            return httpx.Response(201, json={"id": str(uuid.uuid4()), "status": "processing"})
        if path == "/landscape/runs" and request.method == "POST":
            return httpx.Response(201, json={"id": str(uuid.uuid4()), "status": "queued"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(base_url="http://gis", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(landscape, "_get_client", lambda: client)
    monkeypatch.setitem(landscape._allowed, "at", 0.0)
    return calls


async def _headers(client: AsyncClient, db, roles, role: str) -> dict:
    username = f"lp_{role}"
    user = User(username=username, email=f"{username}@example.com", password_hash=hash_password(PASSWORD))
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=roles[role].id))
    await db.commit()
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _posts(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [c for c in calls if c.method == "POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_obl", "akim_ray", "exp_dir"])
async def test_read_allowed_for_all_roles(client, db, roles, upstream, role):
    headers = await _headers(client, db, roles, role)
    for path in ("/api/v1/landscape/slots", "/api/v1/landscape/results", "/api/v1/landscape/runs"):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200, path


@pytest.mark.asyncio
async def test_read_requires_auth(client, upstream):
    resp = await client.get("/api/v1/landscape/slots")
    assert resp.status_code in (401, 403)
    assert upstream == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_obl", "akim_ray", "exp_ver"])
async def test_write_forbidden_for_viewers(client, db, roles, upstream, role):
    headers = await _headers(client, db, roles, role)
    r1 = await client.post("/api/v1/landscape/runs", headers=headers, json={})
    r2 = await client.post(
        "/api/v1/landscape/slots/lp-z-wr", headers=headers, data=PERIOD,
        files={"file": ("x.tif", b"II*\x00", "image/tiff")},
    )
    assert r1.status_code == 403
    assert r2.status_code == 403
    assert _posts(upstream) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "data_uploader"])
async def test_run_allowed_and_body_normalized(client, db, roles, upstream, role):
    headers = await _headers(client, db, roles, role)
    resp = await client.post("/api/v1/landscape/runs", headers=headers, json={})
    assert resp.status_code == 201
    resp = await client.post("/api/v1/landscape/runs", headers=headers, json={"from_stage": 0})
    assert resp.status_code == 201
    sent = [json.loads(c.content) for c in _posts(upstream)]
    assert sent == [{}, {"from_stage": 0}]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"from_stage": 2}, {"overrides": {"x": 1}}])
async def test_run_rejects_other_bodies(client, db, roles, upstream, body):
    headers = await _headers(client, db, roles, "admin")
    resp = await client.post("/api/v1/landscape/runs", headers=headers, json=body)
    assert resp.status_code == 422
    assert _posts(upstream) == []


@pytest.mark.asyncio
async def test_replace_unknown_slot_404(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "admin")
    resp = await client.post(
        "/api/v1/landscape/slots/lp-z-unknown", headers=headers, data=PERIOD,
        files={"file": ("x.tif", b"II*\x00", "image/tiff")},
    )
    assert resp.status_code == 404
    assert _posts(upstream) == []


@pytest.mark.asyncio
async def test_replace_rejects_non_tiff(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "admin")
    resp = await client.post(
        "/api/v1/landscape/slots/lp-z-wr", headers=headers, data=PERIOD,
        files={"file": ("x.shp", b"abc", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert _posts(upstream) == []


@pytest.mark.asyncio
async def test_replace_forwards_file_and_approved_name(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "data_uploader")
    resp = await client.post(
        "/api/v1/landscape/slots/lp-z-wr", headers=headers,
        data=PERIOD,
        files={"file": ("wr.tif", b"II*\x00payload", "image/tiff")},
    )
    assert resp.status_code == 201
    [req] = _posts(upstream)
    assert req.url.path == "/landscape/slots/lp-z-wr"
    body = req.content
    assert b"II*\x00payload" in body
    assert landscape.SLOT_NAMES["lp-z-wr"].encode() in body
    assert b"2025-01-01" in body
    assert b"2025-12-31" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("period,status", [
    ({"start_date": "2025-01-01"}, 422),
    ({}, 422),
    ({"start_date": "2025-12-31", "end_date": "2025-01-01"}, 400),
    ({"start_date": "2025-01-01", "end_date": "31.12.2025"}, 422),
])
async def test_replace_requires_valid_period(client, db, roles, upstream, period, status):
    """Без периода сервер может не выбрать новый файл текущим входом."""
    headers = await _headers(client, db, roles, "admin")
    resp = await client.post(
        "/api/v1/landscape/slots/lp-z-wr", headers=headers, data=period,
        files={"file": ("wr.tif", b"II*\x00", "image/tiff")},
    )
    assert resp.status_code == status
    assert _posts(upstream) == []


@pytest.mark.asyncio
async def test_raster_foreign_dataset_forbidden(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_ray")
    resp = await client.get(f"/api/v1/landscape/raster/{uuid.uuid4()}/ingest/status", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("dataset_id", [SLOT_DATASET, RESULT_DATASET])
async def test_raster_landscape_dataset_allowed(client, db, roles, upstream, dataset_id):
    headers = await _headers(client, db, roles, "akim_ray")
    resp = await client.get(f"/api/v1/landscape/raster/{dataset_id}/ingest/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("absent", "done")


@pytest.mark.asyncio
async def test_sld_only_for_landscape_groups(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_obl")
    ok = await client.get(
        "/api/v1/landscape/sld", headers=headers, params={"url": f"/dataset-groups/{RESULT_GROUP}/sld"},
    )
    assert ok.status_code == 200
    foreign = await client.get(
        "/api/v1/landscape/sld", headers=headers, params={"url": f"/dataset-groups/{uuid.uuid4()}/sld"},
    )
    assert foreign.status_code == 403
    bad = await client.get("/api/v1/landscape/sld", headers=headers, params={"url": "/admin/secret"})
    assert bad.status_code == 400
