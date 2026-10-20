"""
Декадные климатические данные метеостанций → месяц станции → месяц района.

- формулы месяца: температура — взвешенное по дням среднее (10, 10, N−20), осадки — сумма;
- месяц не считается без всех трёх декад; отклонённая декада удаляет месяц;
- статус месяца: approved только если все декады утверждены;
- район = среднее станций; район без станций — по ближайшей станции;
- разбор xlsx заказчика: разный порядок индексов у T_d{д}_{ММ} и R_d{М}_{0д};
- расчётные показатели нельзя записать вручную;
- API /climate/monthly.
"""
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text

from app.models.data import (
    ClimateRegionStationMap, DataSource, Direction, Indicator, IndicatorValue, MeteoStation, MeteoStationValue, Unit,
)
from app.models.user import Region
from app.services import climate_dekads as cd
from app.services.climate_xlsx import parse_columns, parse_sheet

pytestmark = pytest.mark.asyncio


# ── чистые функции ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("year,month,n", [(2023, 2, 28), (2024, 2, 29), (2024, 4, 30), (2024, 7, 31)])
def test_month_temperature_weights_by_days(year, month, n):
    assert cd.days_in_month(year, month) == n
    assert cd.month_temperature(1.0, 2.0, 3.0, year, month) == pytest.approx((10 + 20 + 3 * (n - 20)) / n)


def test_month_precip_is_sum():
    assert cd.month_precip(5.0, 0.0, 12.5) == 17.5


def test_dekad_bounds_and_lookup():
    assert cd.dekad_bounds(2024, 2, 1) == (date(2024, 2, 1), date(2024, 2, 10))
    assert cd.dekad_bounds(2024, 2, 2) == (date(2024, 2, 11), date(2024, 2, 20))
    assert cd.dekad_bounds(2024, 2, 3) == (date(2024, 2, 21), date(2024, 2, 29))
    assert cd.dekad_bounds(2023, 12, 3) == (date(2023, 12, 21), date(2023, 12, 31))
    assert [cd.dekad_of(date(2024, 1, d)) for d in (1, 11, 21, 12)] == [1, 2, 3, None]
    assert cd.expected_dekad_end(date(2024, 4, 21)) == date(2024, 4, 30)


def test_xlsx_columns_differ_for_temperature_and_precipitation():
    cols = parse_columns(["id", "station", "T_d2_07", "T_mean_07", "R_d7_02", "R_d12_03", "R_mean_07", None])
    assert cols == {2: ("T", 7, 2), 4: ("R", 7, 2), 5: ("R", 12, 3)}


def test_xlsx_sheet_skips_empty_cells():
    rows = [("id", "station", "T_d1_01", "R_d1_01"), (1, "Булаево", -13.2, None), (2, "Есиль", "-9,5", "4")]
    cells = parse_sheet(2010, rows)
    assert [(c.station, c.kind, c.value) for c in cells] == [("Булаево", "T", -13.2), ("Есиль", "T", -9.5),
                                                             ("Есиль", "R", 4.0)]


# ── пересчёт в БД ─────────────────────────────────────────────────────────

async def _world(db):
    climate = (await db.execute(select(Direction).where(Direction.code == "climate"))).scalar_one()
    inds = {}
    for name, unit_name in ((cd.TEMPERATURE, "°C"), (cd.PRECIPITATION, "мм")):
        unit = (await db.execute(select(Unit).where(Unit.name == unit_name))).scalar_one_or_none()
        if unit is None:
            unit = Unit(name=unit_name)
            db.add(unit)
            await db.flush()
        ind = Indicator(direction_id=climate.id, unit_id=unit.id, name=name, period_kind="month", is_computed=True)
        db.add(ind)
        inds[name] = ind
    oblast = Region(code=f"O{uuid.uuid4().hex[:6]}", name="Область", level=1)
    db.add(oblast)
    await db.flush()
    district = Region(code=f"D{uuid.uuid4().hex[:6]}", name="Район А", level=2, parent_id=oblast.id)
    city = Region(code=f"C{uuid.uuid4().hex[:6]}", name="Город", level=2, parent_id=oblast.id)
    db.add_all([district, city])
    await db.flush()
    s1 = MeteoStation(name=f"Станция1-{uuid.uuid4().hex[:4]}", region_id=district.id, lat=54.0, lon=69.0)
    s2 = MeteoStation(name=f"Станция2-{uuid.uuid4().hex[:4]}", region_id=district.id, lat=54.5, lon=69.5)
    db.add_all([s1, s2])
    await db.flush()
    db.add_all([
        ClimateRegionStationMap(region_id=district.id, station_id=s1.id, mode="own"),
        ClimateRegionStationMap(region_id=district.id, station_id=s2.id, mode="own"),
        ClimateRegionStationMap(region_id=city.id, station_id=s1.id, mode="nearest"),
    ])
    await db.flush()
    return inds, district, city, s1, s2


def _dekads(station, ind, year, month, values, status="approved"):
    out = []
    for d, v in enumerate(values, start=1):
        if v is None:
            continue
        start, end = cd.dekad_bounds(year, month, d)
        out.append(MeteoStationValue(station_id=station.id, indicator_id=ind.id, period_type="dekad",
                                     period_start=start, period_end=end, dekad=d, value=v,
                                     validation_status=status))
    return out


async def _station_month(db, station, ind, ps):
    return (await db.execute(select(MeteoStationValue).where(
        MeteoStationValue.station_id == station.id, MeteoStationValue.indicator_id == ind.id,
        MeteoStationValue.period_type == "month", MeteoStationValue.period_start == ps))).scalar_one_or_none()


async def _region_month(db, region, ind, ps):
    return (await db.execute(select(IndicatorValue).where(
        IndicatorValue.region_id == region.id, IndicatorValue.indicator_id == ind.id,
        IndicatorValue.period_type == "month", IndicatorValue.period_start == ps))).scalar_one_or_none()


async def test_recompute_station_and_district_months(db):
    inds, district, city, s1, s2 = await _world(db)
    t, r = inds[cd.TEMPERATURE], inds[cd.PRECIPITATION]
    db.add_all(_dekads(s1, t, 2024, 2, [-10.0, -20.0, -15.0]) + _dekads(s2, t, 2024, 2, [-12.0, -18.0, -16.0])
               + _dekads(s1, r, 2024, 2, [5.0, 0.0, 7.5]))
    await db.flush()
    keys = cd.month_keys_from_values([(s.id, i.id, date(2024, 2, 1)) for s in (s1, s2) for i in (t, r)])
    await cd.recompute(db, keys)

    m1 = await _station_month(db, s1, t, date(2024, 2, 1))
    assert m1.is_derived and m1.validation_status == "approved"
    assert m1.value == pytest.approx((-10 * 10 - 20 * 10 - 15 * 9) / 29)
    assert m1.period_end == date(2024, 2, 29)
    assert (await _station_month(db, s1, r, date(2024, 2, 1))).value == 12.5

    m2 = await _station_month(db, s2, t, date(2024, 2, 1))
    reg = await _region_month(db, district, t, date(2024, 2, 1))
    assert reg.value == pytest.approx((m1.value + m2.value) / 2)
    assert reg.validation_status == "approved"
    # город без станций — по ближайшей станции
    assert (await _region_month(db, city, t, date(2024, 2, 1))).value == pytest.approx(m1.value)
    await db.rollback()


async def test_month_needs_all_dekads_and_follows_status(db):
    inds, district, city, s1, s2 = await _world(db)
    t = inds[cd.TEMPERATURE]
    db.add_all(_dekads(s1, t, 2024, 3, [1.0, 2.0, None]))
    await db.flush()
    key = {(s1.id, t.id, date(2024, 3, 1))}
    await cd.recompute(db, key)
    assert await _station_month(db, s1, t, date(2024, 3, 1)) is None
    assert await _region_month(db, district, t, date(2024, 3, 1)) is None

    # третья декада на проверке → месяц есть, но pending
    start, end = cd.dekad_bounds(2024, 3, 3)
    d3 = MeteoStationValue(station_id=s1.id, indicator_id=t.id, period_type="dekad", period_start=start,
                           period_end=end, dekad=3, value=3.0, validation_status="pending")
    db.add(d3)
    await db.flush()
    await cd.recompute(db, key)
    month = await _station_month(db, s1, t, date(2024, 3, 1))
    assert month.validation_status == "pending"
    assert (await _region_month(db, district, t, date(2024, 3, 1))).validation_status == "pending"

    # декаду отклонили → расчётный месяц станции и района удаляется
    d3.validation_status = "rejected"
    await db.flush()
    ids = SimpleNamespace(s1=SimpleNamespace(id=s1.id), t=SimpleNamespace(id=t.id),
                          district=SimpleNamespace(id=district.id))
    await cd.recompute(db, key)
    db.expire_all()  # месяц удалён сырым SQL — сбросить кэш сессии
    assert await _station_month(db, ids.s1, ids.t, date(2024, 3, 1)) is None
    assert await _region_month(db, ids.district, ids.t, date(2024, 3, 1)) is None
    await db.rollback()


async def test_monthly_api_and_computed_indicator_is_read_only(client: AsyncClient, db, admin_token):
    inds, district, city, s1, s2 = await _world(db)
    t = inds[cd.TEMPERATURE]
    for y in (2022, 2023, 2024):
        db.add_all(_dekads(s1, t, y, 7, [18.0 + y - 2022, 20.0, 19.0]))
    await db.flush()
    await cd.recompute_all(db)
    await db.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = await client.get(f"/api/v1/climate/monthly?indicator=temperature&station_id={s1.id}&year=2024",
                            headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["years"] == [2022, 2023, 2024]
    july = body["months"][6]
    assert july["value"] == pytest.approx((20 * 10 + 20 * 10 + 19 * 11) / 31)
    # Норма — 2003–2025 гг., выбранный год входит в базовый период
    assert body["base_period"] == [2003, 2025]
    assert july["norm"] == pytest.approx(mean_of(body, 2022, 2023, 2024))
    assert len(july["dekads"]) == 3

    city_resp = await client.get(f"/api/v1/climate/monthly?indicator=temperature&region_id={city.id}&year=2024",
                                 headers=headers)
    assert city_resp.json()["scope"]["basis"] == "nearest_station"

    write = await client.post("/api/v1/indicator-values", headers=headers, json={
        "indicator_id": str(t.id), "region_id": str(district.id), "period_type": "month",
        "period_start": "2024-08-01", "period_end": "2024-08-31", "value": 1.0})
    assert write.status_code == 400
    assert "рассчитывается Системой" in write.json()["detail"]

    await db.execute(text("DELETE FROM indicator_values"))
    await db.execute(text("DELETE FROM meteo_station_values"))
    await db.commit()


def mean_of(body, *years):
    """Норма июля = среднее рассчитанных июлей за указанные годы."""
    return sum((18.0 + y - 2022) * 10 + 20 * 10 + 19 * 11 for y in years) / 31 / len(years)


# ── приём декад через Excel ───────────────────────────────────────────────

def _xlsx(rows: list[list]) -> bytes:
    from io import BytesIO
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "README"
    ws = wb.create_sheet("Values")
    ws.append(["indicator_name", "direction_code", "unit_name", "kato_code", "station",
               "period_type", "period_start", "period_end", "value", "comment"])
    for r in rows:
        ws.append(r)
    src = wb.create_sheet("Source")
    src.append(["source_name", "url", "description"])
    src.append(["Казгидромет (тест)", "", ""])
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


async def _upload(client, headers, rows):
    files = {"file": ("dekads.xlsx", _xlsx(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = await client.post("/api/v1/data-intake/excel", headers=headers, files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_intake_station_dekads_validation_commit_and_recompute(client: AsyncClient, db, admin_token):
    inds, district, city, s1, s2 = await _world(db)
    await db.commit()
    s1_id = s1.id
    headers = {"Authorization": f"Bearer {admin_token}"}
    T = cd.TEMPERATURE
    rows = [
        [T, "climate", "°C", None, s1.name, "dekad", "2024-07-01", "2024-07-10", 20.0, ""],
        [T, "climate", "°C", None, s1.name.upper(), "dekad", "2024-07-11", "2024-07-20", 21.0, ""],
        [T, "climate", "°C", None, str(s1.id), "dekad", "2024-07-21", "2024-07-31", 19.0, ""],
        [T, "climate", "°C", None, s1.name, "dekad", "2024-07-12", "2024-07-21", 1.0, ""],     # не декада
        [T, "climate", "°C", None, s1.name, "dekad", "2024-06-21", "2024-06-31", 1.0, ""],     # дата
        [T, "climate", "°C", None, s1.name, "dekad", "2024-06-21", "2024-06-29", 1.0, ""],     # конец декады
        [T, "climate", "°C", None, "Нет такой", "dekad", "2024-06-01", "2024-06-10", 1.0, ""],
        [T, "climate", "°C", None, s1.name, "month", "2024-06-01", "2024-06-30", 1.0, ""],
        [T, "climate", "°C", "591000000", None, "dekad", "2024-06-01", "2024-06-10", 1.0, ""],
        [T, "climate", "°C", "591000000", None, "month", "2024-06-01", "2024-06-30", 1.0, ""],
    ]
    body = await _upload(client, headers, rows)
    by_row = {r["row_no"]: r for r in body["rows"]}
    assert [by_row[n]["status"] for n in (2, 3, 4)] == ["ok", "ok", "ok"]
    errors = [by_row[n]["error_message"] or "" for n in range(5, 12)]
    assert "1, 11 или 21" in errors[0]
    assert "Ошибка типа" in errors[1]  # 31 июня — несуществующая дата
    assert "2024-06-30" in errors[2]
    assert "не найдена" in errors[3]
    assert "dekad" in errors[4]
    assert "только по метеостанциям" in errors[5]
    assert "рассчитывается Системой" in errors[6]

    job_id = body["job"]["id"]
    quality = (await client.get(f"/api/v1/data-intake/jobs/{job_id}/quality", headers=headers)).json()
    assert quality["summary"]["blocker"] == 0

    preview = (await client.get(f"/api/v1/data-intake/jobs/{job_id}/preview", headers=headers)).json()
    assert preview["new_count"] == 3

    commit = await client.post(f"/api/v1/data-intake/jobs/{job_id}/commit", headers=headers)
    assert commit.status_code == 200, commit.text
    assert commit.json()["inserted_or_updated"] == 3

    month = (await db.execute(select(MeteoStationValue).where(
        MeteoStationValue.station_id == s1.id, MeteoStationValue.period_type == "month",
        MeteoStationValue.period_start == date(2024, 7, 1)))).scalar_one()
    assert month.value == pytest.approx((200 + 210 + 19 * 11) / 31)
    assert month.validation_status == "pending"

    # утверждение партии утверждает декады и пересчитывает месяц
    v = await client.post("/api/v1/indicator-values/validate-bulk", headers=headers,
                          json={"job_id": job_id, "status": "approved"})
    assert v.status_code == 200, v.text
    db.expire_all()
    month = (await db.execute(select(MeteoStationValue).where(
        MeteoStationValue.station_id == s1_id, MeteoStationValue.period_type == "month",
        MeteoStationValue.period_start == date(2024, 7, 1)))).scalar_one()
    assert month.validation_status == "approved"
    await db.execute(text("DELETE FROM indicator_values"))
    await db.execute(text("DELETE FROM meteo_station_values"))
    await db.commit()


async def test_intake_dekad_quality_checks(client: AsyncClient, db, admin_token):
    inds, district, city, s1, s2 = await _world(db)
    await db.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}
    T, R = cd.TEMPERATURE, cd.PRECIPITATION
    body = await _upload(client, headers, [
        [T, "climate", "°C", None, s1.name, "dekad", "2024-01-01", "2024-01-10", -150.0, ""],  # TEMP_RANGE
        [R, "climate", "мм", None, s1.name, "dekad", "2024-01-01", "2024-01-10", -2.0, ""],    # PRECIP_NEGATIVE
        [R, "climate", "мм", None, s1.name, "dekad", "2024-01-11", "2024-01-20", 350.0, ""],   # PRECIP_RANGE
    ])
    quality = (await client.get(f"/api/v1/data-intake/jobs/{body['job']['id']}/quality", headers=headers)).json()
    codes = {f["code"]: f for f in quality["findings"]}
    assert codes["TEMP_RANGE"]["severity"] == "blocker"
    assert codes["PRECIP_NEGATIVE"]["severity"] == "blocker"
    assert codes["PRECIP_RANGE"]["severity"] == "warning"
    assert codes["DEKAD_INCOMPLETE_MONTH"]["count"] == 2
    # годовые/районные проверки к декадам не применяются
    assert not {"LEVEL_BREAK", "MAGNITUDE", "NEGATIVE", "DISTRICT_SUM"} & set(codes)
    await db.rollback()


async def test_month_value_must_cover_whole_month(client: AsyncClient, db, admin_token):
    """«Месяц» с 11-го по 20-е (так в ряд осадков попадали декады) не принимается."""
    climate = (await db.execute(select(Direction).where(Direction.code == "climate"))).scalar_one()
    unit = Unit(name=f"u_{uuid.uuid4().hex[:6]}")
    db.add(unit)
    await db.flush()
    ind = Indicator(direction_id=climate.id, unit_id=unit.id, name=f"Месячный {uuid.uuid4().hex[:4]}",
                    period_kind="month")
    region = Region(code=f"R{uuid.uuid4().hex[:6]}", name="Район", level=2)
    db.add_all([ind, region])
    await db.commit()
    headers = {"Authorization": f"Bearer {admin_token}"}
    bad = await client.post("/api/v1/indicator-values", headers=headers, json={
        "indicator_id": str(ind.id), "region_id": str(region.id), "period_type": "month",
        "period_start": "2024-07-11", "period_end": "2024-07-20", "value": 5.0})
    assert bad.status_code == 400
    assert "с 1-го по последнее число" in bad.json()["detail"]
    ok = await client.post("/api/v1/indicator-values", headers=headers, json={
        "indicator_id": str(ind.id), "region_id": str(region.id), "period_type": "month",
        "period_start": "2024-02-01", "period_end": "2024-02-29", "value": 5.0})
    assert ok.status_code == 201, ok.text
    await db.execute(text("DELETE FROM indicator_values"))
    await db.commit()


def test_station_aliases_and_substitution_rows():
    from openpyxl import Workbook
    from app.services.climate_xlsx import norm_station, parse_substitutions

    assert norm_station(" Новоишинский ") == "новоишимский"
    assert norm_station("Талшик") == "талшык"
    wb = Workbook()
    ws = wb.active
    ws.title = "T_R_2025"
    ws.append(["id", "station", "adm_2"])
    ws.append([1, "Пресновка", "Жамбылский"])
    ws.append([None, "Пресновка (по Благовещенке)", None])
    ws.append([None, "Талшик (по Возвышенка)", None])
    ws.append([None, "Тепловая шкала (по чему-то)", None])
    known = ["пресновка", "благовещенка", "талшык", "возвышенка"]
    assert parse_substitutions(wb, known) == {(2025, "пресновка"): "благовещенка",
                                              (2025, "талшык"): "возвышенка"}


# ── годовые и сезонные показатели ─────────────────────────────────────────

def test_season_value_cold_period_spans_previous_year():
    months = cd.YEARLY["Сумма осадков за холодный период"][2]
    ser = {(2023, 11): (10.0, "approved"), (2023, 12): (20.0, "approved"),
           (2024, 1): (5.0, "approved"), (2024, 2): (5.0, "pending"), (2024, 3): (10.0, "approved")}
    assert cd.season_value("sum", months, 2024, ser) == (50.0, "pending")
    assert cd.season_value("sum", months, 2025, ser) is None  # нет ноября–декабря 2024 г.
    assert cd.years_from_month_keys({(None, None, date(2023, 12, 1)), (None, None, date(2024, 5, 1))}) == \
        {2023, 2024}


async def test_recompute_yearly_replaces_foreign_values(db):
    inds, district, city, s1, s2 = await _world(db)
    climate_id, mm_id = inds[cd.PRECIPITATION].direction_id, inds[cd.PRECIPITATION].unit_id
    yearly = {}
    for name in ("Годовая сумма осадков", "Сумма осадков за весенний период (апрель–май)",
                 "Сумма осадков за холодный период"):
        yearly[name] = Indicator(direction_id=climate_id, unit_id=mm_id, name=name, is_computed=True)
        db.add(yearly[name])
    db.add(DataSource(name=cd.CALC_SOURCE))
    await db.flush()
    p = inds[cd.PRECIPITATION]
    # Станция 1: 2023 — только ноябрь–декабрь, 2024 — весь год; станция 2 — апрель–май 2024 г.
    for y, months in ((2023, (11, 12)), (2024, range(1, 13))):
        for m in months:
            db.add_all(_dekads(s1, p, y, m, [1.0, 2.0, 3.0]))
    for m in (4, 5):
        db.add_all(_dekads(s2, p, 2024, m, [3.0, 3.0, 3.0]))
    # Прежнее значение из реанализа — уходит в архив.
    db.add(IndicatorValue(indicator_id=yearly["Годовая сумма осадков"].id, region_id=district.id,
                          period_type="year", period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
                          value=999.0, validation_status="approved"))
    await db.flush()
    dist_id, city_id = district.id, city.id
    ids = {n: i.id for n, i in yearly.items()}

    stats = await cd.recompute_all(db)
    assert stats["archived"] == 1

    async def year_value(name, region_id, y):
        return (await db.execute(text("""
            SELECT value FROM indicator_values WHERE indicator_id = :i AND region_id = :r
              AND period_type = 'year' AND period_start = :ps
        """), {"i": ids[name], "r": region_id, "ps": date(y, 1, 1)})).scalar_one_or_none()

    # Район = среднее станций по месяцам: апрель и май — (6 + 9) / 2 = 7,5 каждый
    assert await year_value("Сумма осадков за весенний период (апрель–май)", dist_id, 2024) == pytest.approx(15.0)
    # Годовая сумма района: 10 месяцев по 6 мм (только станция 1) + апрель и май по 7,5 мм
    assert await year_value("Годовая сумма осадков", dist_id, 2024) == pytest.approx(6.0 * 10 + 7.5 * 2)
    assert await year_value("Годовая сумма осадков", dist_id, 2023) is None  # неполный год
    # Холодный период 2024 г.: ноябрь–декабрь 2023 г. + январь–март 2024 г.
    assert await year_value("Сумма осадков за холодный период", city_id, 2024) == pytest.approx(30.0)
    archived = (await db.execute(text("SELECT value FROM indicator_values_archive WHERE indicator_id = :i"),
                                 {"i": ids["Годовая сумма осадков"]})).scalars().all()
    assert archived == [999.0]
    await db.rollback()


async def test_climate_reference_tables(client: AsyncClient, admin_token):
    resp = await client.get("/api/v1/climate/reference", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base_period"] == [2003, 2024]
    assert len(body["transitions"]["rows"]) == 13
    assert body["probability"]["levels"][-1] == 100
    kz = next(r for r in body["active_sums"]["rows"] if r["region"] == "Кызылжарский район")
    assert (kz["t5"], kz["t10"], kz["t15"]) == (2621, 2334, 1738)
