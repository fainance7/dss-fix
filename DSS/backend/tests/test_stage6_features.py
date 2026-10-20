"""
Доработки интерфейсных функций по итогам предварительных испытаний (Этап 6):
аннотации к значениям показателей (ФFE.1), выгрузка журнала аудита целиком и
отчёта по показателям в Excel (Прил. 2), ресурсы сервера и журнал ошибок модулей
(ФFE.7), фактическое состояние резервного копирования (ФХД.5).
"""
import io
import uuid

import pytest
from httpx import AsyncClient

from tests.test_indicator_values import (
    _create_indicator,
    _create_region,
    _create_unit,
    _get_direction_id,
    _make_value_payload,
)


async def _value(client: AsyncClient, engine, token: str, year: int = 2024) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    unit_id = await _create_unit(client, token, f"unit_{uuid.uuid4().hex[:8]}")
    dir_id = await _get_direction_id(client, token, "economy")
    ind_id = await _create_indicator(client, token, dir_id, unit_id, f"ind_{uuid.uuid4().hex[:8]}")
    region_id = await _create_region(engine, f"R{uuid.uuid4().hex[:6]}", f"reg_{uuid.uuid4().hex[:6]}")
    resp = await client.post("/api/v1/indicator-values", headers=h,
                             json=await _make_value_payload(ind_id, region_id, year=year))
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"], ind_id


@pytest.mark.asyncio
async def test_annotation_on_indicator_value_listed_with_author(client, engine, admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    value_id, _ = await _value(client, engine, admin_token)
    created = await client.post("/api/v1/annotations", headers=h,
                                json={"comment": "Выбивается из ряда", "indicator_value_id": value_id})
    assert created.status_code == 201, created.text
    assert created.json()["author_name"] == "test_admin"
    assert created.json()["longitude"] is None

    listed = await client.get(f"/api/v1/annotations?indicator_value_ids={value_id}", headers=h)
    assert [a["comment"] for a in listed.json()] == ["Выбивается из ряда"]
    assert listed.json()[0]["created_at"]


@pytest.mark.asyncio
async def test_annotation_on_map_point_and_empty_target_rejected(client, admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    point = await client.post("/api/v1/annotations", headers=h,
                              json={"comment": "Проверить", "longitude": 69.1, "latitude": 54.9})
    assert point.status_code == 201
    on_map = await client.get("/api/v1/annotations?on_map=true", headers=h)
    assert len(on_map.json()) == 1 and on_map.json()[0]["latitude"] == pytest.approx(54.9)
    empty = await client.post("/api/v1/annotations", headers=h, json={"comment": "Без цели"})
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_audit_export_contains_all_filtered_records(client, admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    for _ in range(3):
        await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "AdminPass123!"})
    resp = await client.get("/api/v1/audit-log/export?action=login", headers=h)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("Время")
    assert len(lines) - 1 >= 4  # вход фикстуры + три входа выше
    assert all(";login;" in line for line in lines[1:])


@pytest.mark.asyncio
async def test_indicator_values_export_xlsx(client, engine, admin_token):
    from openpyxl import load_workbook

    value_id, ind_id = await _value(client, engine, admin_token, year=2023)
    resp = await client.get(f"/api/v1/indicator-values/export?indicator_id={ind_id}",
                            headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    ws = load_workbook(io.BytesIO(resp.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Показатель"
    assert len(rows) == 2 and rows[1][6] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_monitoring_resources_and_errors(client, admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    res = await client.get("/api/v1/monitoring/resources", headers=h)
    assert res.status_code == 200 and res.json()["available"] is False  # Prometheus в тестах не подключён
    errs = await client.get("/api/v1/monitoring/errors?module=backend", headers=h)
    assert errs.status_code == 200 and isinstance(errs.json(), list)


@pytest.mark.asyncio
async def test_monitoring_admin_only(client, regular_user):
    tok = (await client.post("/api/v1/auth/login",
                             json={"username": "test_expert", "password": "ExpertPass123!"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for path in ("/api/v1/monitoring/resources", "/api/v1/monitoring/errors", "/api/v1/settings/backup/status"):
        assert (await client.get(path, headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_backup_status_reports_archiver(client, admin_token):
    resp = await client.get("/api/v1/settings/backup/status", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["archive_mode"] in ("on", "off", "always")
    assert body["base_ok"] is False or body["base_backups"] > 0
    assert "02:30" in body["schedule"]


@pytest.mark.asyncio
async def test_data_layer_list_reflects_update_immediately(client, admin_token):
    """Список каталога кешируется — после изменения метаданных кеш сбрасывается."""
    from tests.test_data import _create_category, _create_layer, _create_source

    h = {"Authorization": f"Bearer {admin_token}"}
    src = await _create_source(client, admin_token)
    cat = await _create_category(client, admin_token)
    layer = await _create_layer(client, admin_token, cat["id"], src["id"])
    await client.get("/api/v1/data-layers", headers=h)  # наполняем кеш
    await client.patch(f"/api/v1/data-layers/{layer['id']}", headers=h, json={"accuracy_description": "±0,1"})
    items = (await client.get("/api/v1/data-layers", headers=h)).json()["items"]
    assert next(i for i in items if i["id"] == layer["id"])["accuracy_description"] == "±0,1"
