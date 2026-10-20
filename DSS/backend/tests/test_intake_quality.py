"""
Контроль качества партии приёма данных (app/services/intake_quality.py).

Сценарии повторяют реальные случаи разбора партий перед импортом:
- повторная загрузка того же файла;
- несколько строк на один ключ (схлопнутся при импорте);
- значения в других единицах (центнеры вместо тонн) против утверждённых;
- одновременный обрыв ряда у большинства районов (смена методики);
- сумма районов не равна области;
- импорт с блокером требует подтверждения и комментария эксперта;
- замечания видны на странице значений и фильтруются.
"""
import uuid
from datetime import date

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.models.data import Direction, Indicator, IndicatorValue, Unit
from app.models.data_intake import DataIntakeJob, DataIntakeRow
from app.models.user import AuditLog, Region
from app.services import intake_quality

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _default_thresholds(monkeypatch):
    """Пороги по умолчанию без Redis: глобальный клиент Redis привязан к циклу
    событий первого теста и ломает соединения в следующих."""
    async def _defaults():
        return intake_quality.DataQualitySettings()
    monkeypatch.setattr(intake_quality, "load_settings", _defaults)


async def _world(db, *, unit_name="тонна", name="Валовой сбор зерновых"):
    direction = (await db.execute(select(Direction).where(Direction.code == "economy"))).scalar_one()
    unit = Unit(name=f"{unit_name}" if unit_name else f"u_{uuid.uuid4().hex[:6]}")
    existing_unit = (await db.execute(select(Unit).where(Unit.name == unit.name))).scalar_one_or_none()
    unit = existing_unit or unit
    if not existing_unit:
        db.add(unit)
        await db.flush()
    ind = Indicator(direction_id=direction.id, unit_id=unit.id, name=f"{name} {uuid.uuid4().hex[:4]}")
    db.add(ind)
    oblast = Region(code=f"O{uuid.uuid4().hex[:6]}", name="Область", level=1)
    db.add(oblast)
    await db.flush()
    districts = []
    for n in range(14):
        r = Region(code=f"D{uuid.uuid4().hex[:6]}", name=f"Район {n}", level=2, parent_id=oblast.id)
        db.add(r)
        districts.append(r)
    await db.flush()
    return ind, oblast, districts


def _job(rows_ok: int) -> DataIntakeJob:
    return DataIntakeJob(source_kind="excel_template", source_ref=f"t_{uuid.uuid4().hex[:6]}.xlsx",
                         status="validated", rows_total=rows_ok, rows_ok=rows_ok)


def _row(job, ind, region, year, value, n) -> DataIntakeRow:
    return DataIntakeRow(job_id=job.id, row_no=n, raw_payload={}, indicator_id=ind.id, region_id=region.id,
                         period_type="year", period_start=date(year, 1, 1), period_end=date(year, 12, 31),
                         value=value, status="ok")


def _iv(ind, region, year, value, status="approved") -> IndicatorValue:
    return IndicatorValue(indicator_id=ind.id, region_id=region.id, period_type="year",
                          period_start=date(year, 1, 1), period_end=date(year, 12, 31),
                          value=value, validation_status=status)


async def _report(db, job):
    report, _ = await intake_quality.build_report(db, job)
    return {f["code"]: f for f in report["findings"]}, report


async def test_clean_job_has_no_findings(db):
    ind, oblast, districts = await _world(db)
    db.add_all([_iv(ind, oblast, y, 4_000_000 + y) for y in (2020, 2021, 2022)])
    job = _job(1)
    db.add(job)
    await db.flush()
    db.add(_row(job, ind, oblast, 2023, 4_100_000, 1))
    await db.flush()
    found, report = await _report(db, job)
    assert found == {}
    assert report["summary"] == {"blocker": 0, "warning": 0, "info": 0}
    await db.rollback()  # закрыть транзакцию в цикле событий теста


async def test_unit_shift_against_approved_is_blocker(db):
    ind, oblast, districts = await _world(db)
    # утверждено в центнерах под видом тонн, новые — в тоннах (в 10 раз меньше)
    db.add_all([_iv(ind, r, 2020, 45_000_000 if r is oblast else 3_000_000) for r in [oblast, *districts[:3]]])
    job = _job(4)
    db.add(job)
    await db.flush()
    db.add_all([_row(job, ind, r, 2020, 4_500_000 if r is oblast else 300_000, n)
                for n, r in enumerate([oblast, *districts[:3]])])
    await db.flush()
    found, report = await _report(db, job)
    assert found["UNIT_SHIFT"]["severity"] == "blocker"
    assert found["UNIT_SHIFT"]["count"] == 4
    assert found["APPROVED_CHANGED"]["count"] == 4
    assert intake_quality.blockers(report) == 1
    await db.rollback()  # закрыть транзакцию в цикле событий теста


async def test_duplicate_job_and_key_collision(db):
    ind, oblast, _ = await _world(db)
    first, second = _job(2), _job(2)
    db.add_all([first, second])
    await db.flush()
    for job in (first, second):
        db.add_all([_row(job, ind, oblast, 2023, 10.0, 1), _row(job, ind, oblast, 2024, 11.0, 2)])
    collide = _job(2)
    db.add(collide)
    await db.flush()
    db.add_all([_row(collide, ind, oblast, 2025, 1.0, 1), _row(collide, ind, oblast, 2025, 2.0, 2)])
    await db.flush()

    found, _ = await _report(db, second)
    assert found["DUPLICATE_JOB"]["items"][0]["job_id"] == str(first.id)

    found, _ = await _report(db, collide)
    assert found["KEY_COLLISION"]["severity"] == "blocker"
    assert found["KEY_COLLISION"]["items"][0]["rows"] == 2
    await db.rollback()  # закрыть транзакцию в цикле событий теста


async def test_widespread_district_break_looks_like_methodology_change(db):
    ind, oblast, districts = await _world(db, unit_name="тыс.га", name="Посевная площадь картофеля")
    job = _job(0)
    db.add(job)
    await db.flush()
    n = 0
    for r in districts:
        for year, v in ((2022, 2.2), (2023, 2.0), (2024, 0.5)):
            n += 1
            db.add(_row(job, ind, r, year, v, n))
    for year, v in ((2022, 31.0), (2023, 28.0), (2024, 7.5)):
        n += 1
        db.add(_row(job, ind, oblast, year, v, n))
    await db.flush()
    found, _ = await _report(db, job)
    assert found["WIDESPREAD_BREAK"]["count"] == 14
    assert found["WIDESPREAD_BREAK"]["items"][0]["districts"] == "14 из 14"
    assert any(i["year"] == 2024 for i in found["LEVEL_BREAK"]["items"])
    await db.rollback()  # закрыть транзакцию в цикле событий теста


async def test_district_sum_mismatch(db):
    ind, oblast, districts = await _world(db, unit_name="тонна")
    job = _job(0)
    db.add(job)
    await db.flush()
    db.add(_row(job, ind, oblast, 2009, 5_620_820, 0))
    db.add_all([_row(job, ind, r, 2009, 4_652_692 / 14, n + 1) for n, r in enumerate(districts)])
    await db.flush()
    found, _ = await _report(db, job)
    assert found["DISTRICT_SUM"]["items"][0]["share_pct"] == pytest.approx(82.8, abs=0.1)
    await db.rollback()  # закрыть транзакцию в цикле событий теста


async def test_commit_with_blocker_requires_acknowledgement(client: AsyncClient, db, admin_token):
    ind, oblast, districts = await _world(db)
    db.add_all([_iv(ind, r, 2020, 45_000_000) for r in (oblast, districts[0])])
    job = _job(2)
    db.add(job)
    await db.flush()
    db.add_all([_row(job, ind, oblast, 2020, 4_500_000, 1), _row(job, ind, districts[0], 2020, 4_500_000, 2)])
    await db.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}

    rep = await client.get(f"/api/v1/data-intake/jobs/{job.id}/quality", headers=headers)
    assert rep.status_code == 200
    assert rep.json()["summary"]["blocker"] == 1

    lst = await client.get("/api/v1/data-intake/jobs", headers=headers)
    item = next(j for j in lst.json()["items"] if j["id"] == str(job.id))
    assert item["quality_summary"]["blocker"] == 1

    no_ack = await client.post(f"/api/v1/data-intake/jobs/{job.id}/commit", headers=headers)
    assert no_ack.status_code == 409
    short = await client.post(f"/api/v1/data-intake/jobs/{job.id}/commit", headers=headers,
                              json={"acknowledge": True, "comment": "ок"})
    assert short.status_code == 409

    ok = await client.post(f"/api/v1/data-intake/jobs/{job.id}/commit", headers=headers,
                           json={"acknowledge": True, "comment": "Старые значения были в центнерах, новые верные"})
    assert ok.status_code == 200, ok.text

    stored = await client.get(f"/api/v1/data-intake/jobs/{job.id}/quality", headers=headers)
    assert stored.json()["acknowledged"]["comment"].startswith("Старые значения")

    audit = (await db.execute(
        select(AuditLog).where(AuditLog.action == "commit", AuditLog.entity_id == job.id))).scalar_one()
    assert audit.details["quality"]["blocker"] == 1
    assert audit.details["quality_comment"].startswith("Старые значения")

    # замечания видны на странице значений и фильтруются
    vals = await client.get(f"/api/v1/indicator-values?indicator_id={ind.id}&has_quality_flags=true",
                            headers=headers)
    assert vals.status_code == 200
    items = vals.json()["items"]
    assert len(items) == 2
    assert {f["code"] for f in items[0]["quality_flags"]} >= {"UNIT_SHIFT", "APPROVED_CHANGED"}
    await db.rollback()  # закрыть транзакцию в цикле событий теста
