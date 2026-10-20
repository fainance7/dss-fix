"""
Тесты приёма исторических данных через Excel-шаблон.

Покрывает:
- Успешный upload + commit → indicator_values.
- Неизвестный indicator → row.status='invalid'.
- Неизвестный КАТО → row.status='invalid'.
- RBAC: akim_ray не может загружать.
- Идемпотентность: повторный commit не дублирует.
"""
from io import BytesIO
import uuid

import pytest
from httpx import AsyncClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.security import hash_password
from app.models.data import IndicatorValue
from app.models.kato import KatoReference
from app.models.user import Region, Role, User, UserRole

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────

def _build_xlsx(values_rows: list[list], source_row: list | None = None) -> bytes:
    wb = Workbook()
    wb.active.title = "README"
    ws_v = wb.create_sheet("Values")
    ws_v.append([
        "indicator_name", "direction_code", "unit_name", "kato_code",
        "period_type", "period_start", "period_end", "value", "comment",
    ])
    for r in values_rows:
        ws_v.append(r)
    ws_src = wb.create_sheet("Source")
    ws_src.append(["source_name", "url", "description"])
    if source_row:
        ws_src.append(source_row)
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def _setup_indicator_and_region(client: AsyncClient, engine, admin_token: str):
    """Создаёт unit, indicator (direction=economy), регион и kato_reference."""
    unit_name = f"u_{uuid.uuid4().hex[:6]}"
    resp = await client.post(
        "/api/v1/units",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": unit_name, "is_active": True},
    )
    unit_id = resp.json()["id"]

    dir_resp = await client.get(
        "/api/v1/directions",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    economy_dir = next(d for d in dir_resp.json() if d["code"] == "economy")

    indicator_name = f"Показатель_{uuid.uuid4().hex[:6]}"
    ind_resp = await client.post(
        "/api/v1/indicators",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "direction_id": economy_dir["id"],
            "unit_id": unit_id,
            "name": indicator_name,
            "sort_order": 1,
            "is_active": True,
        },
    )
    assert ind_resp.status_code in (200, 201), ind_resp.text

    factory = async_sessionmaker(engine, expire_on_commit=False)
    kato_code = f"5910{uuid.uuid4().hex[:5]}"[:9]
    region_code = f"R{uuid.uuid4().hex[:6]}"
    async with factory() as s:
        # KATO без region_id (FK в kato_reference на regions deferred-aware)
        kato = KatoReference(kato_code=kato_code, name_ru="Тест-район", level=4)
        s.add(kato)
        await s.flush()
        region = Region(code=region_code, name="Тест-район", level=1, kato_code=kato_code)
        s.add(region)
        await s.flush()
        kato.region_id = region.id
        await s.commit()
    return {
        "unit_name": unit_name,
        "indicator_name": indicator_name,
        "kato_code": kato_code,
    }


# ── tests ──────────────────────────────────────────────────────────────────

class TestExcelIntake:
    async def test_upload_validates_and_commits(
        self, client: AsyncClient, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(
            values_rows=[
                [
                    ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2023-01-01", "2023-12-31", 100.0, "тест",
                ],
                [
                    ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2024-01-01", "2024-12-31", 150.0, "",
                ],
            ],
            source_row=["Тестовый источник", "https://x.kz", "описание"],
        )
        files = {"file": ("test.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job"]["rows_total"] == 2
        assert body["job"]["rows_ok"] == 2
        assert body["job"]["rows_invalid"] == 0
        assert body["job"]["status"] == "validated"

        job_id = body["job"]["id"]

        commit_resp = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert commit_resp.status_code == 200, commit_resp.text
        c = commit_resp.json()
        assert c["inserted_or_updated"] == 2
        assert c["skipped"] == 0

        # Verify rows in indicator_values
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            count = (
                await s.execute(select(IndicatorValue))
            ).scalars().all()
            assert len(count) == 2
            for v in count:
                assert v.validation_status == "pending"

    async def test_unknown_indicator_marked_invalid(
        self, client: AsyncClient, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(
            values_rows=[
                [
                    "Несуществующий показатель", "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2023-01-01", "2023-12-31", 1.0, "",
                ],
            ],
        )
        files = {"file": ("bad.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["job"]["rows_invalid"] == 1
        assert body["rows"][0]["status"] == "invalid"
        assert "не найден" in body["rows"][0]["error_message"]

    async def test_unknown_kato_marked_invalid(
        self, client: AsyncClient, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(
            values_rows=[
                [
                    ctx["indicator_name"], "economy", ctx["unit_name"], "999999999",
                    "year", "2023-01-01", "2023-12-31", 1.0, "",
                ],
            ],
        )
        files = {"file": ("nokato.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["job"]["rows_invalid"] == 1
        assert "КАТО" in body["rows"][0]["error_message"]

    async def test_akim_ray_forbidden(
        self, client: AsyncClient, engine, admin_token: str, db
    ):
        # create akim_ray user
        from sqlalchemy import select as sel
        roles = (await db.execute(sel(Role))).scalars().all()
        akim_role = next(r for r in roles if r.name == "akim_ray")
        akim_user = User(
            username="akim_intake_test",
            email="akim_intake@example.com",
            password_hash=hash_password("Pass1234!"),
        )
        db.add(akim_user)
        await db.flush()
        db.add(UserRole(user_id=akim_user.id, role_id=akim_role.id, region_id=None))
        await db.commit()

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "akim_intake_test", "password": "Pass1234!"},
        )
        token = login.json()["access_token"]

        xlsx = _build_xlsx(values_rows=[])
        files = {"file": ("any.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
        )
        assert resp.status_code == 403

    async def test_download_template(
        self, client: AsyncClient, admin_token: str, tmp_path, monkeypatch
    ):
        from openpyxl import Workbook
        from app.api.v1 import data_intake as di_mod
        wb = Workbook()
        wb.active.title = "Values"
        wb.active.append(["indicator_name"])
        fake = tmp_path / "tpl.xlsx"
        wb.save(fake)
        monkeypatch.setattr(di_mod, "TEMPLATE_PATH", fake)

        resp = await client.get(
            "/api/v1/data-intake/template",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert "data_intake_template.xlsx" in resp.headers.get("content-disposition", "")

    async def test_template_404_when_missing(
        self, client: AsyncClient, admin_token: str, tmp_path, monkeypatch
    ):
        from app.api.v1 import data_intake as di_mod
        monkeypatch.setattr(di_mod, "TEMPLATE_PATH", tmp_path / "nope.xlsx")
        resp = await client.get(
            "/api/v1/data-intake/template",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    async def test_revalidate_after_adding_indicator(
        self, client: AsyncClient, engine, admin_token: str
    ):
        """invalid-строки партии резолвятся после добавления показателя — без re-upload."""
        ctx = await _setup_indicator_and_region(client, engine, admin_token)

        new_indicator_name = f"Новый_{uuid.uuid4().hex[:6]}"
        xlsx = _build_xlsx(
            values_rows=[
                [
                    ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2023-01-01", "2023-12-31", 10.0, "",
                ],
                [
                    new_indicator_name, "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2024-01-01", "2024-12-31", 20.0, "",
                ],
            ],
        )
        files = {"file": ("mixed.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        upload = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        assert upload.status_code == 200
        body = upload.json()
        assert body["job"]["rows_ok"] == 1
        assert body["job"]["rows_invalid"] == 1
        assert body["job"]["status"] == "pending"
        job_id = body["job"]["id"]

        # Ранний revalidate — показателя нет, ничего не починилось.
        early = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/revalidate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert early.status_code == 200, early.text
        assert early.json()["became_ok"] == 0
        assert early.json()["still_invalid"] == 1

        # Добавляем недостающий показатель в справочник.
        dir_resp = await client.get(
            "/api/v1/directions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        economy_dir = next(d for d in dir_resp.json() if d["code"] == "economy")
        units_resp = await client.get(
            "/api/v1/units",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        units_payload = units_resp.json()
        units_list = units_payload if isinstance(units_payload, list) else units_payload.get("items", [])
        unit = next(u for u in units_list if u["name"] == ctx["unit_name"])
        await client.post(
            "/api/v1/indicators",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "direction_id": economy_dir["id"],
                "unit_id": unit["id"],
                "name": new_indicator_name,
                "sort_order": 2,
                "is_active": True,
            },
        )

        # Повторный revalidate — invalid-строка должна стать ok, job → validated.
        again = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/revalidate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert again.status_code == 200, again.text
        result = again.json()
        assert result["rechecked"] == 1
        assert result["became_ok"] == 1
        assert result["still_invalid"] == 0
        assert result["job"]["rows_ok"] == 2
        assert result["job"]["rows_invalid"] == 0
        assert result["job"]["status"] == "validated"

        # И теперь commit проходит для обеих строк.
        commit = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert commit.status_code == 200, commit.text
        assert commit.json()["inserted_or_updated"] == 2

    async def test_revalidate_blocked_after_commit(
        self, client: AsyncClient, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(
            values_rows=[[
                ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
                "year", "2026-01-01", "2026-12-31", 5.0, "",
            ]],
        )
        files = {"file": ("rev.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        up = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        job_id = up.json()["job"]["id"]
        await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/revalidate",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409

    async def test_revalidate_allowed_for_data_uploader(
        self, client: AsyncClient, engine, admin_token: str, db
    ):
        # data_uploader может загружать и перепроверять ошибочные строки (но не импортировать).
        from sqlalchemy import select as sel
        roles = (await db.execute(sel(Role))).scalars().all()
        upl_role = next(r for r in roles if r.name == "data_uploader")
        upl_user = User(
            username="upl_revalidate_test",
            email="upl_revalidate@example.com",
            password_hash=hash_password("Pass1234!"),
        )
        db.add(upl_user)
        await db.flush()
        db.add(UserRole(user_id=upl_user.id, role_id=upl_role.id, region_id=None))
        await db.commit()

        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "upl_revalidate_test", "password": "Pass1234!"},
        )
        token = login.json()["access_token"]

        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(values_rows=[[
            ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
            "year", "2027-01-01", "2027-12-31", 1.0, "",
        ]])
        files = {"file": ("rbac.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        up = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {token}"},
            files=files,
        )
        job_id = up.json()["job"]["id"]
        resp = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/revalidate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        commit = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert commit.status_code == 403

    async def test_commit_idempotent(
        self, client: AsyncClient, engine, admin_token: str
    ):
        ctx = await _setup_indicator_and_region(client, engine, admin_token)
        xlsx = _build_xlsx(
            values_rows=[
                [
                    ctx["indicator_name"], "economy", ctx["unit_name"], ctx["kato_code"],
                    "year", "2025-01-01", "2025-12-31", 42.0, "",
                ],
            ],
        )
        files = {"file": ("idem.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files,
        )
        job_id = resp.json()["job"]["id"]
        # first commit
        c1 = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert c1.json()["inserted_or_updated"] == 1
        # second commit on same job → 409
        c2 = await client.post(
            f"/api/v1/data-intake/jobs/{job_id}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert c2.status_code == 409

        # Upload same file again, commit → UPSERT not duplicate
        files2 = {"file": ("idem2.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp2 = await client.post(
            "/api/v1/data-intake/excel",
            headers={"Authorization": f"Bearer {admin_token}"},
            files=files2,
        )
        # Повторная загрузка того же файла — блокер контроля качества:
        # без подтверждения эксперта импорт не выполняется.
        blocked = await client.post(
            f"/api/v1/data-intake/jobs/{resp2.json()['job']['id']}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["blockers"] == 1
        c3 = await client.post(
            f"/api/v1/data-intake/jobs/{resp2.json()['job']['id']}/commit",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"acknowledge": True, "comment": "Повторная загрузка для проверки UPSERT"},
        )
        assert c3.status_code == 200
        # all good — final count must be 1, not 2 (UPSERT on iv_unique_value)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            rows = (await s.execute(select(IndicatorValue))).scalars().all()
            assert len(rows) == 1
            assert rows[0].value == 42.0
