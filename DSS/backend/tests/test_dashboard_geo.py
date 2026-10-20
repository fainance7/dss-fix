"""Карты каталога геоданных на дашбордах (`/api/v1/dashboard-geo/*`).

Каталог заменён `httpx.MockTransport`. Проверяется: чтение доступно всем ролям,
включая акиматы; слой адресуется только ключом из утверждённого перечня;
из нескольких версий набора берётся самая поздняя; GeoJSON упрощается, лишние
атрибуты отбрасываются, ответ кэшируется; SLD с двойной перекодировкой
восстанавливается; пустой тайл растра отдаётся прозрачной картинкой.
"""
import json
import uuid

import httpx
import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.api.v1 import dashboard_geo, gis_raster
from app.core.security import hash_password
from app.models.user import User, UserRole

PASSWORD = "DashboardGeo123!"
OLD_ID = str(uuid.uuid4())
NEW_ID = str(uuid.uuid4())
RASTER_ID = str(uuid.uuid4())

SETTLEMENTS = dashboard_geo.LAYERS["settlements"].dataset_name
ROADS = dashboard_geo.LAYERS["roads"].dataset_name
SALINITY = dashboard_geo.LAYERS["soil-salinity"].dataset_name

# Квадрат с лишними вершинами на сторонах: после упрощения остаются углы.
_SQUARE = [[69.0, 54.0], [69.05, 54.0], [69.1, 54.0], [69.1, 54.1], [69.0, 54.1], [69.0, 54.0]]
GEOJSON = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [_SQUARE]},
        "properties": {"name": "Налобино", "adm2": "Кызылжарский\n район", "SHAPE_Area": 1.5, "OBJECTID": 7},
    }],
}
# «Код» в UTF-8, прочитанный как cp1251 и снова сохранённый в UTF-8.
MOJIBAKE_SLD = '<?xml version="1.0" encoding="UTF-8"?><sld><ogc:PropertyName>РљРѕРґ</ogc:PropertyName></sld>'


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/datasets":
            return httpx.Response(200, json={"total_pages": 1, "data": [
                {"id": OLD_ID, "name": SETTLEMENTS, "file_type": "vector", "end_date": "2023-12-31",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
                {"id": NEW_ID, "name": SETTLEMENTS, "file_type": "vector", "end_date": "2025-12-31",
                 "created_at": "2026-02-01T00:00:00Z", "updated_at": "2026-02-01T00:00:00Z"},
                {"id": str(uuid.uuid4()), "name": ROADS, "file_type": "vector", "end_date": "2025-12-31"},
                {"id": RASTER_ID, "name": SALINITY, "file_type": "raster", "end_date": "2025-12-31"},
            ]})
        if path == f"/datasets/{NEW_ID}.geojson":
            return httpx.Response(200, json=GEOJSON)
        if path.endswith("/sld"):
            if NEW_ID in path:
                return httpx.Response(200, content=MOJIBAKE_SLD.encode("utf-8"))
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(404, json={"message": "unexpected"})

    client = httpx.AsyncClient(base_url="http://gis", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(dashboard_geo, "_get_client", lambda: client)
    monkeypatch.setitem(dashboard_geo._catalog, "at", 0.0)
    monkeypatch.setitem(dashboard_geo._catalog, "by_name", {})
    monkeypatch.setattr(dashboard_geo, "_geo_mem", {})
    monkeypatch.setattr(dashboard_geo, "_sld_cache", {})
    monkeypatch.setattr(dashboard_geo.settings, "COG_DIR", str(tmp_path))
    return calls


async def _headers(client: AsyncClient, db, roles, role: str) -> dict:
    username = f"geo_{role}"
    user = User(username=username, email=f"{username}@example.com", password_hash=hash_password(PASSWORD))
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=roles[role].id))
    await db.commit()
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_obl", "akim_ray", "exp_dir"])
async def test_layers_readable_by_all_roles(client, db, roles, upstream, role):
    headers = await _headers(client, db, roles, role)
    resp = await client.get("/api/v1/dashboard-geo/layers", params={"keys": "settlements,roads"}, headers=headers)
    assert resp.status_code == 200
    body = {item["key"]: item for item in resp.json()}
    # Из двух версий набора — самая поздняя по end_date.
    assert body["settlements"]["dataset_id"] == NEW_ID
    assert body["settlements"]["has_sld"] is True
    assert body["roads"]["has_sld"] is False


@pytest.mark.asyncio
async def test_requires_auth(client, upstream):
    resp = await client.get("/api/v1/dashboard-geo/layers", params={"keys": "settlements"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unknown_layer_rejected(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_ray")
    resp = await client.get(f"/api/v1/dashboard-geo/layers/{NEW_ID}/geojson", headers=headers)
    assert resp.status_code == 404
    assert not [c for c in upstream if c.url.path.endswith(".geojson")]


@pytest.mark.asyncio
async def test_missing_dataset_reported_per_layer(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_obl")
    resp = await client.get("/api/v1/dashboard-geo/layers", params={"keys": "settlements,lakes"}, headers=headers)
    body = {item["key"]: item for item in resp.json()}
    assert "error" not in body["settlements"]
    assert "не найден" in body["lakes"]["error"]


@pytest.mark.asyncio
async def test_geojson_simplified_cached_and_trimmed(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_ray")
    first = await client.get("/api/v1/dashboard-geo/layers/settlements/geojson", headers=headers)
    assert first.status_code == 200
    feature = first.json()["features"][0]
    # Служебные атрибуты площади и id отброшены, переносы строк схлопнуты.
    assert feature["properties"] == {"name": "Налобино", "adm2": "Кызылжарский район"}
    # Промежуточная вершина на прямой стороне убрана упрощением.
    assert len(feature["geometry"]["coordinates"][0]) == 5
    second = await client.get("/api/v1/dashboard-geo/layers/settlements/geojson", headers=headers)
    assert second.content == first.content
    assert len([c for c in upstream if c.url.path.endswith(".geojson")]) == 1


@pytest.mark.asyncio
async def test_sld_mojibake_repaired(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_obl")
    resp = await client.get("/api/v1/dashboard-geo/layers/settlements/sld", headers=headers)
    assert resp.status_code == 200
    assert "<ogc:PropertyName>Код</ogc:PropertyName>" in resp.text
    missing = await client.get("/api/v1/dashboard-geo/layers/roads/sld", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_vector_layer_has_no_raster_endpoints(client, db, roles, upstream):
    headers = await _headers(client, db, roles, "akim_obl")
    resp = await client.get("/api/v1/dashboard-geo/layers/settlements/statistics", headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_empty_raster_tile_is_transparent(client, db, roles, upstream, monkeypatch):
    async def fake_tile(dataset_id, *args, **kwargs):
        assert dataset_id == RASTER_ID
        raise HTTPException(status_code=404, detail="tile error")

    monkeypatch.setattr(gis_raster, "get_tile", fake_tile)
    headers = await _headers(client, db, roles, "akim_ray")
    resp = await client.get("/api/v1/dashboard-geo/layers/soil-salinity/tiles/6/10/10.png", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_mojibake_repair_keeps_normal_text():
    fix = dashboard_geo._fix_mojibake
    assert fix("РљРѕРґ") == "Код"
    for text in ("Сёмга", "Река Ишим", "Слабо увлажнённый, тёплый климат (K = 0,8–1,0)"):
        assert fix(text) == text


def test_all_layer_keys_are_url_safe():
    for key, layer in dashboard_geo.LAYERS.items():
        assert key.replace("-", "").isalnum()
        assert layer.kind in ("vector", "raster")
    json.dumps([k for k in dashboard_geo.LAYERS])
