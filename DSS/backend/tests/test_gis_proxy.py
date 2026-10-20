"""ГИС-прокси не пропускает ручки ЛП: они доступны только через /api/v1/landscape."""
import pytest
from httpx import AsyncClient

from app.api.v1.gis import _is_landscape_path


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", [
    "landscape/runs",
    "landscape/slots/lp-p-crop",
    "LANDSCAPE/runs",
    "/landscape/runs",
    "datasets/../landscape/runs",
    "./landscape",
])
def test_landscape_paths_detected(path: str):
    assert _is_landscape_path(path)


@pytest.mark.parametrize("path", ["datasets", "datasets/landscape", "landscapes", "categories"])
def test_other_paths_pass(path: str):
    assert not _is_landscape_path(path)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/v1/gis/landscape/runs"),
    ("POST", "/api/v1/gis/landscape/slots/lp-p-crop"),
    ("GET", "/api/v1/gis/landscape/slots"),
])
async def test_proxy_blocks_landscape(client: AsyncClient, expert_token: str, method: str, path: str):
    resp = await client.request(method, path, headers=_h(expert_token), json={})
    assert resp.status_code == 403
    assert "/api/v1/landscape" in resp.json()["detail"]


async def test_proxy_blocks_landscape_for_admin(client: AsyncClient, admin_token: str):
    resp = await client.post("/api/v1/gis/landscape/runs", headers=_h(admin_token), json={})
    assert resp.status_code == 403
