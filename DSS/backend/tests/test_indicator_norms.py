"""
Тесты GET /api/v1/indicator-norms (среднемноголетние нормы показателей).
Норма пишется скриптом импорта (не через API), поэтому строку создаём напрямую
через сессию, а читаем — через эндпоинт.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.data import IndicatorNorm

pytestmark = pytest.mark.asyncio


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
        "/api/v1/directions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    for d in resp.json():
        if d["code"] == code:
            return d["id"]
    raise AssertionError(f"Direction {code} not found")


async def _create_indicator(client, token, direction_id, unit_id, name) -> str:
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
    from app.models.user import Region

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        region = Region(code=code, name=name, level=2)
        s.add(region)
        await s.flush()
        rid = str(region.id)
        await s.commit()
    return rid


async def _insert_norm(engine, indicator_id, region_id, value, method="auto_mean") -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(
            IndicatorNorm(
                indicator_id=uuid.UUID(indicator_id),
                region_id=uuid.UUID(region_id),
                norm_value=value,
                period_label="multiyear_2003_2024",
                method=method,
            )
        )
        await s.commit()


class TestIndicatorNorms:
    async def test_list_and_filter_by_region(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "climate")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        r1 = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Район-1")
        r2 = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Район-2")
        await _insert_norm(engine, ind_id, r1, 0.99)
        await _insert_norm(engine, ind_id, r2, 1.25)

        # все нормы показателя
        resp = await client.get(
            "/api/v1/indicator-norms",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"indicator_id": ind_id},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 2
        assert {round(n["norm_value"], 2) for n in data} == {0.99, 1.25}
        assert data[0]["indicator_name"] is not None

        # фильтр по району
        resp2 = await client.get(
            "/api/v1/indicator-norms",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"indicator_id": ind_id, "region_id": r1},
        )
        assert resp2.status_code == 200, resp2.text
        one = resp2.json()
        assert len(one) == 1
        assert round(one[0]["norm_value"], 2) == 0.99
        assert one[0]["region_id"] == r1

    async def test_unique_per_indicator_region_label(
        self, client: AsyncClient, engine, admin_token: str
    ):
        unit_id = await _create_unit(client, admin_token, f"unit_{uuid.uuid4().hex[:8]}")
        dir_id = await _get_direction_id(client, admin_token, "climate")
        ind_id = await _create_indicator(
            client, admin_token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}"
        )
        r1 = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", "Район-U")
        await _insert_norm(engine, ind_id, r1, 1.0)
        with pytest.raises(Exception):
            await _insert_norm(engine, ind_id, r1, 2.0)  # тот же (ind, region, label)
