"""Связь вариантов ЛП со сценариями (`/api/v1/landscape/scenarios/*`, прокси ИИ).

ai-service заменён `httpx.MockTransport`, расчёт зонирования — фикстурой: здесь
проверяется разделение прав, запрет прямой записи снимка через прокси и выбор
сценариев для варианта по умолчанию.
"""
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient

from app.api.v1 import ai as ai_proxy
from app.api.v1 import landscape_link
from app.core.security import hash_password
from app.models.user import User, UserRole

PASSWORD = "LinkPass123!"
ZONING = {
    "variant": "lp-v2-balanced", "label": "V2 — сбалансированный", "dataset_id": "d",
    "legend": [{"value": 4, "label": "пашенное земледелие", "color": "#f5e102"}],
    "oblast_pct": {"4": 100.0}, "districts": [],
}


@pytest.fixture
def ai_upstream(monkeypatch):
    calls: list[httpx.Request] = []
    scenarios = [
        {"id": str(uuid.uuid4()), "status": "calculated", "parameters": {}},
        {"id": str(uuid.uuid4()), "status": "calculated", "parameters": {"landscape": {"variant": "lp-v1-conservative"}}},
        {"id": str(uuid.uuid4()), "status": "approved", "parameters": {}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET" and request.url.path == "/api/v1/scenarios":
            return httpx.Response(200, json=scenarios)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(base_url="http://ai", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ai_proxy, "_get_client", lambda: client)

    async def fake_zoning(group: str) -> dict:
        return {**ZONING, "variant": group, "label": landscape_link.VARIANTS[group]}

    monkeypatch.setattr(landscape_link, "compute_zoning", fake_zoning)
    return calls, scenarios


async def _headers(client: AsyncClient, db, roles, role: str) -> dict:
    username = f"lnk_{role}"
    user = User(username=username, email=f"{username}@example.com", password_hash=hash_password(PASSWORD))
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=roles[role].id))
    await db.commit()
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _puts(calls):
    return [c for c in calls if c.method == "PUT"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "akim_obl", "exp_dir"])
async def test_link_variant_allowed(client, db, roles, ai_upstream, role):
    calls, _ = ai_upstream
    headers = await _headers(client, db, roles, role)
    sid = uuid.uuid4()
    resp = await client.put(
        f"/api/v1/landscape/scenarios/{sid}/variant", headers=headers, json={"variant": "lp-v3-development"},
    )
    assert resp.status_code == 200
    [put] = _puts(calls)
    assert put.url.path == f"/api/v1/scenarios/{sid}/landscape"
    sent = json.loads(put.content)
    assert sent["variant"] == "lp-v3-development"
    assert sent["snapshot"]["variant"] == "lp-v3-development"
    assert sent["snapshot"]["kpi_effect"]["status"] == "not_available"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_ray", "exp_ver", "data_uploader"])
async def test_link_variant_forbidden(client, db, roles, ai_upstream, role):
    calls, _ = ai_upstream
    headers = await _headers(client, db, roles, role)
    resp = await client.put(
        f"/api/v1/landscape/scenarios/{uuid.uuid4()}/variant", headers=headers, json={"variant": "lp-v1-conservative"},
    )
    assert resp.status_code == 403
    assert _puts(calls) == []


@pytest.mark.asyncio
async def test_link_rejects_unknown_variant(client, db, roles, ai_upstream):
    headers = await _headers(client, db, roles, "admin")
    resp = await client.put(
        f"/api/v1/landscape/scenarios/{uuid.uuid4()}/variant", headers=headers, json={"variant": "lp-e"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_direct_snapshot_write_through_proxy_blocked(client, db, roles, ai_upstream):
    """Снимок считает сервер — подложить доли зон через прокси ИИ нельзя даже админу."""
    calls, _ = ai_upstream
    headers = await _headers(client, db, roles, "admin")
    resp = await client.put(
        f"/api/v1/ai/api/v1/scenarios/{uuid.uuid4()}/landscape", headers=headers,
        json={"variant": "lp-v1-conservative", "snapshot": {"variant": "lp-v1-conservative"}},
    )
    assert resp.status_code == 403
    assert calls == []


@pytest.mark.asyncio
async def test_link_defaults_only_unlinked_active(client, db, roles, ai_upstream):
    calls, scenarios = ai_upstream
    headers = await _headers(client, db, roles, "admin")
    resp = await client.post("/api/v1/landscape/scenarios/link-defaults", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["linked"] == [scenarios[0]["id"]]
    [put] = _puts(calls)
    assert json.loads(put.content)["variant"] == landscape_link.DEFAULT_VARIANT


def test_parse_legend_reads_colormap_entries():
    xml = (
        '<sld:ColorMap type="values"><sld:ColorMapEntry color="#e30004" quantity="1" label="регулируемое"/>'
        '<ColorMapEntry quantity="4" color="#f5e102" label="пашня"/></sld:ColorMap>'
    )
    assert landscape_link._parse_legend(xml) == [
        {"value": 1, "label": "регулируемое", "color": "#e30004"},
        {"value": 4, "label": "пашня", "color": "#f5e102"},
    ]


@pytest.mark.asyncio
async def test_compute_zoning_uses_snapshot_and_never_titiler(monkeypatch, tmp_path):
    """Доли зон берутся из снимка, посчитанного scripts/lp_zoning_snapshot.py по полному
    разрешению. Снимок старого формата (по обзорам) не используется; при отсутствии
    снимка — 503, titiler не вызывается (полный проход роняет прод-titiler)."""
    from fastapi import HTTPException
    from app.api.v1 import gis_raster, landscape

    dataset_id = str(uuid.uuid4())
    monkeypatch.setattr(landscape_link.settings, "COG_DIR", str(tmp_path))
    monkeypatch.setattr(landscape_link, "_ZONING_CACHE", {})
    (tmp_path / f"zoning-{dataset_id}.json").write_text(json.dumps({"stale": True}))

    async def fake_upstream(method, path, **kwargs):
        return httpx.Response(200, json=[{
            "group": "lp-v2-balanced", "sld_url": "/sld",
            "latest": {"id": dataset_id, "status": "ready"},
        }])

    def no_titiler():
        raise AssertionError("titiler вызываться не должен")

    ingested = []
    monkeypatch.setattr(landscape, "_upstream", fake_upstream)
    monkeypatch.setattr(gis_raster, "_get_client", no_titiler)
    monkeypatch.setattr(gis_raster, "start_ingest", lambda i: ingested.append(i) or {"status": "running"})

    with pytest.raises(HTTPException) as exc:
        await landscape_link.compute_zoning("lp-v2-balanced")
    assert exc.value.status_code == 503 and ingested == [dataset_id]

    snapshot = {**ZONING, "dataset_id": dataset_id, "calc_version": landscape_link._ZONING_VERSION}
    (tmp_path / f"zoning-{landscape_link._ZONING_VERSION}-{dataset_id}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False))
    assert await landscape_link.compute_zoning("lp-v2-balanced") == snapshot


@pytest.mark.parametrize("new_v2,expected", [(False, False), (True, True)])
async def test_compare_criteria_and_freshness(client, db, roles, monkeypatch, new_v2, expected):
    data = landscape_link.load_compare()
    ids = {s: v["dataset_id"] for s, v in data["variants"].items()}
    if new_v2:
        ids["V2"] = str(uuid.uuid4())

    async def fake_upstream(method, path, **kwargs):
        return httpx.Response(200, json=[{"group": v["group"], "latest": {"id": ids[s]}}
                                         for s, v in data["variants"].items()])

    monkeypatch.setattr(landscape_link.landscape, "_upstream", fake_upstream)
    headers = await _headers(client, db, roles, "akim_obl")
    resp = await client.get("/api/v1/landscape/compare", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["freshness"]["stale"] is expected
    crit = {c["id"] for c in body["criteria"]}
    assert len(body["territories"]) == 14
    for v in ("V1", "V2", "V3"):
        assert set(body["oblast"][v]) == crit
    # Область = сумма территорий
    s = sum(t["values"]["V3"]["land_change"] for t in body["territories"])
    assert s == pytest.approx(body["oblast"]["V3"]["land_change"], abs=1.0)
