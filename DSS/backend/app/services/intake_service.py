"""
Сервис приёма исторических данных (data intake).

Шаги:
1. parse_excel_template — читает 5-листный шаблон → list[ParsedRow].
2. validate_rows — резолвит indicator/region/unit, проверяет period_*, помечает status.
3. persist_job_with_rows — создаёт DataIntakeJob + DataIntakeRow записи в одной транзакции.
4. commit_job — UPSERT ok-строк в indicator_values, ставит status='imported'.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO
from typing import Any, Optional
from uuid import UUID

from openpyxl import load_workbook
from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.data import DataSource, Indicator, IndicatorValue, Unit
from app.models.data_intake import DataIntakeJob, DataIntakeRow
from app.models.kato import KatoReference
from app.models.user import Region, User
from app.schemas.data_intake import IntakePreviewItem, IntakePreviewResult
from app.services import intake_quality


VALUES_SHEET = "Values"
SOURCE_SHEET = "Source"
# Значения расчётных показателей (Indicator.is_computed) считает Система.
COMPUTED_INDICATOR_ERROR = (
    "Показатель «{}» рассчитывается Системой (месяц из декад метеостанций) — "
    "загружать его значения нельзя"
)
PERIOD_TYPES = ("year", "quarter", "month", "day")
# В приёме данных дополнительно — декада, только для строк по метеостанциям.
INTAKE_PERIOD_TYPES = (*PERIOD_TYPES, "dekad")
DEKAD_NEEDS_STATION_ERROR = (
    "Декадные данные (period_type=dekad) принимаются только по метеостанциям: "
    "заполните колонку station"
)

REQUIRED_COLUMNS = (
    "indicator_name",
    "direction_code",
    "unit_name",
    "kato_code",
    "period_type",
    "period_start",
    "period_end",
    "value",
)


@dataclass
class ParsedRow:
    row_no: int
    raw: dict[str, Any]
    indicator_id: Optional[UUID] = None
    region_id: Optional[UUID] = None
    station_id: Optional[UUID] = None
    period_type: Optional[str] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    value: Optional[float] = None
    error: Optional[str] = None
    status: str = "invalid"


@dataclass
class ParsedTemplate:
    rows: list[ParsedRow] = field(default_factory=list)
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_description: Optional[str] = None


MONTH_SHAPE_ERROR = (
    "Для period_type=month период — с 1-го по последнее число месяца "
    "(например, 2025-02-01 … 2025-02-28)"
)


def _is_full_month(start: Optional[date], end: Optional[date]) -> bool:
    import calendar

    return (start is not None and end is not None and start.day == 1
            and end == start.replace(day=calendar.monthrange(start.year, start.month)[1]))


# ── Декады по метеостанциям ──────────────────────────────────────

def dekad_row_error(ind: Indicator, period_type: Optional[str],
                    start: Optional[date], end: Optional[date]) -> Optional[str]:
    """Правила строки по метеостанции: показатель, тип периода, границы декады."""
    from app.services import climate_dekads as cd

    if ind.name not in cd.AGGREGATION:
        return "По метеостанциям принимаются только показатели: " + "; ".join(f"«{n}»" for n in cd.AGGREGATION)
    if period_type != "dekad":
        return "Для метеостанции period_type должен быть dekad (месяц Система считает сама из декад)"
    if start is None or end is None:
        return "period_start / period_end обязательны и должны быть датой YYYY-MM-DD"
    if cd.dekad_of(start) is None:
        return f"Декада начинается 1, 11 или 21 числа, а не {start:%d.%m.%Y}"
    expected = cd.expected_dekad_end(start)
    if end != expected:
        return f"Для декады с {start:%d.%m.%Y} period_end должен быть {expected:%Y-%m-%d}"
    return None


async def stations_lookup(db: AsyncSession) -> dict[str, UUID]:
    """Станция по названию (без учёта регистра, ё→е) или по id."""
    from app.models.data import MeteoStation
    from app.services.climate_xlsx import norm_station

    out: dict[str, UUID] = {}
    for st in (await db.execute(select(MeteoStation))).scalars().all():
        out[norm_station(st.name)] = st.id
        out[str(st.id)] = st.id
    return out


def _resolve_station(lookup: dict[str, UUID], raw_value: Any) -> Optional[UUID]:
    from app.services.climate_xlsx import norm_station

    v = _str_or_none(raw_value)
    return (lookup.get(v) or lookup.get(norm_station(v))) if v else None


# ── Excel parsing ────────────────────────────────────────────────

def parse_excel_template(content: bytes) -> ParsedTemplate:
    """Читает .xlsx-шаблон. Возвращает строки и метаданные источника.

    Не валидирует против БД — только формат.
    """
    wb = load_workbook(BytesIO(content), data_only=True, read_only=True)
    if VALUES_SHEET not in wb.sheetnames:
        raise ValueError(f"Лист «{VALUES_SHEET}» не найден в шаблоне")

    parsed = ParsedTemplate()

    # Source sheet (optional)
    if SOURCE_SHEET in wb.sheetnames:
        s = wb[SOURCE_SHEET]
        rows_iter = s.iter_rows(min_row=2, values_only=True)
        first = next(rows_iter, None)
        if first:
            parsed.source_name = _str_or_none(first[0]) if len(first) > 0 else None
            parsed.source_url = _str_or_none(first[1]) if len(first) > 1 else None
            parsed.source_description = _str_or_none(first[2]) if len(first) > 2 else None

    ws = wb[VALUES_SHEET]
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not header_row:
        raise ValueError(f"Лист «{VALUES_SHEET}» пуст")
    headers = [_str_or_none(h) for h in header_row]

    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ValueError(f"В шаблоне отсутствуют обязательные колонки: {', '.join(missing)}")

    col_idx = {name: headers.index(name) for name in headers if name}

    for excel_row_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row is None or all(v is None or v == "" for v in row):
            continue
        raw = {h: row[i] for h, i in col_idx.items() if i < len(row)}
        pr = ParsedRow(row_no=excel_row_no, raw=_jsonable(raw))
        try:
            pr.period_type = _str_or_none(raw.get("period_type"))
            pr.period_start = _to_date(raw.get("period_start"))
            pr.period_end = _to_date(raw.get("period_end"))
            value = raw.get("value")
            pr.value = float(value) if value is not None and value != "" else None

            if pr.period_type not in INTAKE_PERIOD_TYPES:
                pr.error = f"period_type должен быть одним из: {', '.join(INTAKE_PERIOD_TYPES)}"
            elif pr.period_start is None or pr.period_end is None:
                pr.error = "period_start / period_end обязательны и должны быть датой YYYY-MM-DD"
            elif pr.period_end < pr.period_start:
                pr.error = "period_end < period_start"
            elif pr.period_type == "month" and not _is_full_month(pr.period_start, pr.period_end):
                pr.error = MONTH_SHAPE_ERROR
            elif pr.value is None:
                pr.error = "value обязателен и должен быть числом"
        except (ValueError, TypeError) as e:
            pr.error = f"Ошибка типа: {e}"
        parsed.rows.append(pr)

    return parsed


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _to_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        return date.fromisoformat(v[:10])
    raise ValueError(f"Неверный тип даты: {v!r}")


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ── Resolve / validate against DB ────────────────────────────────

async def validate_rows(
    db: AsyncSession,
    parsed: ParsedTemplate,
) -> ParsedTemplate:
    """Резолвит indicator_id (по direction_code+name), region_id (по kato_code).

    Заполняет error для невалидных строк. Помечает status='ok' или 'invalid'.
    """
    indicator_keys: set[tuple[str, str]] = set()
    kato_codes: set[str] = set()
    unit_names: set[str] = set()

    for r in parsed.rows:
        dc = _str_or_none(r.raw.get("direction_code"))
        ind_name = _str_or_none(r.raw.get("indicator_name"))
        kc = _str_or_none(r.raw.get("kato_code"))
        un = _str_or_none(r.raw.get("unit_name"))
        if dc and ind_name:
            indicator_keys.add((dc, ind_name))
        if kc:
            kato_codes.add(kc)
        if un:
            unit_names.add(un)

    indicators_by_key: dict[tuple[str, str], Indicator] = {}
    if indicator_keys:
        from app.models.data import Direction
        rows = (
            await db.execute(
                select(Indicator, Direction.code, Unit.name)
                .join(Direction, Direction.id == Indicator.direction_id)
                .join(Unit, Unit.id == Indicator.unit_id)
            )
        ).all()
        for ind, dcode, uname in rows:
            indicators_by_key[(dcode, ind.name)] = ind

    region_by_kato: dict[str, Region] = {}
    if kato_codes:
        # Связь kato_reference → regions идёт через kato_reference.region_id (а не
        # regions.kato_code, который указывает только на «опорную» КАТО-точку региона).
        rows = (
            await db.execute(
                select(KatoReference.kato_code, Region)
                .join(Region, Region.id == KatoReference.region_id)
                .where(KatoReference.kato_code.in_(kato_codes))
            )
        ).all()
        for code, region in rows:
            region_by_kato[code] = region

    unit_by_name: dict[str, Unit] = {}
    if unit_names:
        rows = (
            await db.execute(select(Unit).where(Unit.name.in_(unit_names)))
        ).scalars().all()
        unit_by_name = {u.name: u for u in rows}

    stations = await stations_lookup(db) if any(_str_or_none(r.raw.get("station")) for r in parsed.rows) else {}

    for r in parsed.rows:
        if r.error:
            r.status = "invalid"
            continue
        dc = _str_or_none(r.raw.get("direction_code"))
        ind_name = _str_or_none(r.raw.get("indicator_name"))
        kc = _str_or_none(r.raw.get("kato_code"))
        un = _str_or_none(r.raw.get("unit_name"))

        if not dc or not ind_name:
            r.error = "indicator_name и direction_code обязательны"
            r.status = "invalid"
            continue
        ind = indicators_by_key.get((dc, ind_name))
        if ind is None:
            unit_part = f" с ед.изм. «{un}»" if un else ""
            r.error = (
                f"Показатель «{ind_name}»{unit_part} в направлении «{dc}» "
                "не найден в справочнике"
            )
            r.status = "invalid"
            continue
        if un and un in unit_by_name and unit_by_name[un].id != ind.unit_id:
            r.error = (
                f"unit_name «{un}» не совпадает с единицей показателя в справочнике"
            )
            r.status = "invalid"
            continue
        station_raw = _str_or_none(r.raw.get("station"))
        if station_raw:
            station_id = _resolve_station(stations, station_raw)
            r.error = (f"Метеостанция «{station_raw}» не найдена в справочнике" if station_id is None
                       else dekad_row_error(ind, r.period_type, r.period_start, r.period_end))
            if r.error:
                r.status = "invalid"
                continue
            r.indicator_id, r.station_id, r.region_id = ind.id, station_id, None
            r.status = "ok"
            continue
        if r.period_type == "dekad":
            r.error = DEKAD_NEEDS_STATION_ERROR
            r.status = "invalid"
            continue
        if ind.is_computed:
            r.error = COMPUTED_INDICATOR_ERROR.format(ind.name)
            r.status = "invalid"
            continue
        if not kc:
            r.error = "kato_code обязателен"
            r.status = "invalid"
            continue
        region = region_by_kato.get(kc)
        if region is None:
            r.error = f"КАТО «{kc}» не привязан к региону в DSS"
            r.status = "invalid"
            continue
        r.indicator_id = ind.id
        r.region_id = region.id
        r.status = "ok"
    return parsed


# ── Persistence ──────────────────────────────────────────────────

async def persist_job_with_rows(
    db: AsyncSession,
    *,
    parsed: ParsedTemplate,
    source_kind: str,
    source_ref: str,
    user: User,
) -> DataIntakeJob:
    """Создаёт DataIntakeJob + строки. Создаёт DataSource при наличии source_name."""
    data_source_id: Optional[UUID] = None
    if parsed.source_name:
        existing = (
            await db.execute(
                select(DataSource).where(DataSource.name == parsed.source_name)
            )
        ).scalar_one_or_none()
        if existing:
            data_source_id = existing.id
        else:
            ds = DataSource(
                name=parsed.source_name,
                description=parsed.source_description,
                url=parsed.source_url,
            )
            db.add(ds)
            await db.flush()
            data_source_id = ds.id

    rows_ok = sum(1 for r in parsed.rows if r.status == "ok")
    rows_invalid = sum(1 for r in parsed.rows if r.status == "invalid")
    summary = _build_summary(parsed.rows)

    job = DataIntakeJob(
        source_kind=source_kind,
        source_ref=source_ref,
        uploaded_by=user.id,
        status="validated" if rows_invalid == 0 and rows_ok > 0 else "pending",
        rows_total=len(parsed.rows),
        rows_ok=rows_ok,
        rows_invalid=rows_invalid,
        rows_imported=0,
        validation_summary=summary,
        data_source_id=data_source_id,
    )
    db.add(job)
    await db.flush()

    for pr in parsed.rows:
        db.add(
            DataIntakeRow(
                job_id=job.id,
                row_no=pr.row_no,
                raw_payload=pr.raw,
                indicator_id=pr.indicator_id,
                region_id=pr.region_id,
                station_id=pr.station_id,
                period_type=pr.period_type,
                period_start=pr.period_start,
                period_end=pr.period_end,
                value=pr.value,
                error_message=pr.error,
                status=pr.status,
            )
        )
    await db.flush()
    await intake_quality.safe_refresh(db, job)
    return job


def _build_summary(rows: list[ParsedRow]) -> dict[str, Any]:
    errors_top: dict[str, int] = {}
    for r in rows:
        if r.error:
            errors_top[r.error] = errors_top.get(r.error, 0) + 1
    return {
        "total": len(rows),
        "ok": sum(1 for r in rows if r.status == "ok"),
        "invalid": sum(1 for r in rows if r.status == "invalid"),
        "top_errors": sorted(errors_top.items(), key=lambda x: -x[1])[:10],
    }


# ── JSON batch ───────────────────────────────────────────────────


async def create_job_from_rows(
    db: AsyncSession,
    user: User,
    payload,
):
    """Создаёт ``DataIntakeJob`` из JSON-payload (intake portal).

    Логика:
      * Каждая строка содержит уже резолвленные ``indicator_id``, ``region_id``
        — резолвинг по справочникам не нужен.
      * Валидация по БД: indicator существует и активен, region существует.
        Несовпадающие строки помечаются ``status='invalid'`` (как и в Excel-пути).
      * Возвращает ``IntakeUploadResponse`` (тот же, что для Excel).

    ``source_kind='json_api'``; ``source_ref`` — из payload или ``"intake-portal:json"``.
    """
    from app.schemas.data_intake import (  # импорт здесь, чтобы избежать циклов
        IntakeJobOut,
        IntakeRowOut,
        IntakeUploadResponse,
    )

    raw_rows = list(payload.rows)
    indicator_ids = {r.indicator_id for r in raw_rows}
    region_ids = {r.region_id for r in raw_rows if r.region_id is not None}
    stations = await stations_lookup(db) if any(r.station_id for r in raw_rows) else {}

    indicators_map: dict = {}
    if indicator_ids:
        rows = (
            await db.execute(select(Indicator).where(Indicator.id.in_(indicator_ids)))
        ).scalars().all()
        indicators_map = {ind.id: ind for ind in rows}

    regions_map: dict = {}
    if region_ids:
        rows = (
            await db.execute(select(Region).where(Region.id.in_(region_ids)))
        ).scalars().all()
        regions_map = {r.id: r for r in rows}

    parsed = ParsedTemplate()
    for idx, r in enumerate(raw_rows, start=1):
        raw_payload = {
            "indicator_id": str(r.indicator_id),
            "region_id": str(r.region_id) if r.region_id else None,
            "station_id": str(r.station_id) if r.station_id else None,
            "period_type": r.period_type,
            "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(),
            "value": r.value,
        }
        if r.source_id is not None:
            raw_payload["source_id"] = str(r.source_id)

        pr = ParsedRow(row_no=idx, raw=raw_payload)
        pr.period_type = r.period_type
        pr.period_start = r.period_start
        pr.period_end = r.period_end
        pr.value = r.value

        # Базовые правила (как в Excel-пути).
        if r.period_type not in INTAKE_PERIOD_TYPES:
            pr.error = f"period_type должен быть одним из: {', '.join(INTAKE_PERIOD_TYPES)}"
        elif r.period_end < r.period_start:
            pr.error = "period_end < period_start"
        elif r.period_type == "month" and not _is_full_month(r.period_start, r.period_end):
            pr.error = MONTH_SHAPE_ERROR
        elif r.station_id is not None:
            ind = indicators_map.get(r.indicator_id)
            station_id = stations.get(str(r.station_id))
            if ind is None:
                pr.error = f"Показатель {r.indicator_id} не найден"
            elif station_id is None:
                pr.error = f"Метеостанция {r.station_id} не найдена"
            else:
                pr.error = dekad_row_error(ind, r.period_type, r.period_start, r.period_end)
                if pr.error is None:
                    pr.indicator_id, pr.station_id = ind.id, station_id
                    pr.status = "ok"
        elif r.region_id is None:
            pr.error = "Укажите region_id (район) или station_id (метеостанция)"
        elif r.period_type == "dekad":
            pr.error = DEKAD_NEEDS_STATION_ERROR
        else:
            ind = indicators_map.get(r.indicator_id)
            if ind is None:
                pr.error = f"Показатель {r.indicator_id} не найден"
            elif not getattr(ind, "is_active", True):
                pr.error = f"Показатель {r.indicator_id} деактивирован"
            elif getattr(ind, "is_computed", False):
                pr.error = COMPUTED_INDICATOR_ERROR.format(ind.name)
            else:
                region = regions_map.get(r.region_id)
                if region is None:
                    pr.error = f"Регион {r.region_id} не найден"
                else:
                    pr.indicator_id = r.indicator_id
                    pr.region_id = r.region_id
                    pr.status = "ok"
        if pr.error and pr.status != "ok":
            pr.status = "invalid"
        parsed.rows.append(pr)

    # Опциональный source_id из первой строки → DataSource не создаём,
    # а используем как ``data_source_id`` у job, если все строки указывают один и тот же source.
    source_ids = {r.source_id for r in raw_rows if r.source_id is not None}
    rows_ok = sum(1 for r in parsed.rows if r.status == "ok")
    rows_invalid = sum(1 for r in parsed.rows if r.status == "invalid")
    summary = _build_summary(parsed.rows)

    data_source_id = None
    if len(source_ids) == 1:
        data_source_id = next(iter(source_ids))

    job = DataIntakeJob(
        source_kind="json_api",
        source_ref=(payload.source_ref or "intake-portal:json"),
        uploaded_by=user.id,
        status="validated" if rows_invalid == 0 and rows_ok > 0 else "pending",
        rows_total=len(parsed.rows),
        rows_ok=rows_ok,
        rows_invalid=rows_invalid,
        rows_imported=0,
        validation_summary=summary,
        data_source_id=data_source_id,
    )
    db.add(job)
    await db.flush()

    for pr in parsed.rows:
        db.add(
            DataIntakeRow(
                job_id=job.id,
                row_no=pr.row_no,
                raw_payload=pr.raw,
                indicator_id=pr.indicator_id,
                region_id=pr.region_id,
                station_id=pr.station_id,
                period_type=pr.period_type,
                period_start=pr.period_start,
                period_end=pr.period_end,
                value=pr.value,
                error_message=pr.error,
                status=pr.status,
            )
        )
    await db.flush()
    await intake_quality.safe_refresh(db, job)

    # Подгружаем строки с relations для вывода (по той же схеме, что Excel-эндпоинт).
    out_rows = (
        await db.execute(
            select(DataIntakeRow)
            .where(DataIntakeRow.job_id == job.id)
            .order_by(DataIntakeRow.row_no)
        )
    ).scalars().all()

    def _row_to_out(r: DataIntakeRow) -> IntakeRowOut:
        return IntakeRowOut(
            id=r.id,
            row_no=r.row_no,
            status=r.status,
            indicator_id=r.indicator_id,
            indicator_name=None,
            region_id=r.region_id,
            region_name=None,
            period_type=r.period_type,
            period_start=r.period_start,
            period_end=r.period_end,
            value=r.value,
            error_message=r.error_message,
            raw_payload=r.raw_payload or {},
        )

    return IntakeUploadResponse(
        job=IntakeJobOut.model_validate(job),
        rows=[_row_to_out(r) for r in out_rows],
        errors_total=job.rows_invalid,
    )


async def revalidate_job(
    db: AsyncSession,
    *,
    job: DataIntakeJob,
) -> tuple[int, int, int]:
    """Перерезолвит invalid-строки партии после обновлений в справочниках.

    Использует ``raw_payload`` каждой строки, не требуя повторной загрузки файла.
    Поддерживает ``source_kind='excel_template'`` (резолв по name/code) и
    ``'json_api'`` (проверка существования по UUID). Строки со ``status='ok'`` /
    ``'imported'`` не трогаются. Ошибки парсинга периода/значения ревалидацией
    не исправляются (raw уже потерял исходный тип) — такие строки остаются invalid.

    После прохода пересчитывает ``rows_ok``/``rows_invalid``/``validation_summary``
    и переводит ``job.status`` в ``validated`` (если invalid=0 и ok>0) или ``pending``.

    Returns:
        (rechecked, became_ok, still_invalid) — для invalid-подмножества.
    """
    if job.status == "imported":
        raise ValueError("Партия уже импортирована — перепроверка недоступна")

    invalid_rows = (
        await db.execute(
            select(DataIntakeRow).where(
                DataIntakeRow.job_id == job.id,
                DataIntakeRow.status == "invalid",
            )
        )
    ).scalars().all()

    rechecked = len(invalid_rows)

    if invalid_rows:
        if job.source_kind == "excel_template":
            await _revalidate_excel_rows(db, invalid_rows)
        else:  # json_api
            await _revalidate_json_rows(db, invalid_rows)

    became_ok = sum(1 for r in invalid_rows if r.status == "ok")

    all_rows = (
        await db.execute(
            select(DataIntakeRow).where(DataIntakeRow.job_id == job.id)
        )
    ).scalars().all()
    rows_ok = sum(1 for r in all_rows if r.status == "ok")
    rows_invalid = sum(1 for r in all_rows if r.status == "invalid")

    errors_top: dict[str, int] = {}
    for r in all_rows:
        if r.error_message:
            errors_top[r.error_message] = errors_top.get(r.error_message, 0) + 1

    job.rows_ok = rows_ok
    job.rows_invalid = rows_invalid
    job.validation_summary = {
        "total": len(all_rows),
        "ok": rows_ok,
        "invalid": rows_invalid,
        "top_errors": sorted(errors_top.items(), key=lambda x: -x[1])[:10],
    }
    if job.status != "imported":
        job.status = "validated" if rows_invalid == 0 and rows_ok > 0 else "pending"

    await db.flush()
    if job.status != "imported":
        await intake_quality.safe_refresh(db, job)
    return rechecked, became_ok, rows_invalid


def _row_has_period_value_error(r: DataIntakeRow) -> bool:
    return (
        r.period_type is None
        or r.period_start is None
        or r.period_end is None
        or r.value is None
    )


async def _revalidate_excel_rows(
    db: AsyncSession, rows: list[DataIntakeRow]
) -> None:
    from app.models.data import Direction

    indicator_keys: set[tuple[str, str]] = set()
    kato_codes: set[str] = set()
    unit_names: set[str] = set()
    for r in rows:
        raw = r.raw_payload or {}
        dc = _str_or_none(raw.get("direction_code"))
        ind_name = _str_or_none(raw.get("indicator_name"))
        kc = _str_or_none(raw.get("kato_code"))
        un = _str_or_none(raw.get("unit_name"))
        if dc and ind_name:
            indicator_keys.add((dc, ind_name))
        if kc:
            kato_codes.add(kc)
        if un:
            unit_names.add(un)

    indicators_by_key: dict[tuple[str, str], Indicator] = {}
    if indicator_keys:
        result = (
            await db.execute(
                select(Indicator, Direction.code)
                .join(Direction, Direction.id == Indicator.direction_id)
                .join(Unit, Unit.id == Indicator.unit_id)
            )
        ).all()
        for ind, dcode in result:
            indicators_by_key[(dcode, ind.name)] = ind

    region_by_kato: dict[str, Region] = {}
    if kato_codes:
        result = (
            await db.execute(
                select(KatoReference.kato_code, Region)
                .join(Region, Region.id == KatoReference.region_id)
                .where(KatoReference.kato_code.in_(kato_codes))
            )
        ).all()
        for code, region in result:
            region_by_kato[code] = region

    unit_by_name: dict[str, Unit] = {}
    if unit_names:
        result = (
            await db.execute(select(Unit).where(Unit.name.in_(unit_names)))
        ).scalars().all()
        unit_by_name = {u.name: u for u in result}

    stations = await stations_lookup(db)

    for r in rows:
        if _row_has_period_value_error(r):
            continue
        raw = r.raw_payload or {}
        dc = _str_or_none(raw.get("direction_code"))
        ind_name = _str_or_none(raw.get("indicator_name"))
        kc = _str_or_none(raw.get("kato_code"))
        un = _str_or_none(raw.get("unit_name"))

        if not dc or not ind_name:
            r.error_message = "indicator_name и direction_code обязательны"
            continue
        ind = indicators_by_key.get((dc, ind_name))
        if ind is None:
            unit_part = f" с ед.изм. «{un}»" if un else ""
            r.error_message = (
                f"Показатель «{ind_name}»{unit_part} в направлении «{dc}» "
                "не найден в справочнике"
            )
            continue
        if un and un in unit_by_name and unit_by_name[un].id != ind.unit_id:
            r.error_message = (
                f"unit_name «{un}» не совпадает с единицей показателя в справочнике"
            )
            continue
        station_raw = _str_or_none(raw.get("station"))
        if station_raw:
            station_id = _resolve_station(stations, station_raw)
            err = (f"Метеостанция «{station_raw}» не найдена в справочнике" if station_id is None
                   else dekad_row_error(ind, r.period_type, r.period_start, r.period_end))
            if err:
                r.error_message = err
                continue
            r.indicator_id, r.station_id, r.region_id = ind.id, station_id, None
            r.status = "ok"
            r.error_message = None
            continue
        if r.period_type == "dekad":
            r.error_message = DEKAD_NEEDS_STATION_ERROR
            continue
        if ind.is_computed:
            r.error_message = COMPUTED_INDICATOR_ERROR.format(ind.name)
            continue
        if not kc:
            r.error_message = "kato_code обязателен"
            continue
        region = region_by_kato.get(kc)
        if region is None:
            r.error_message = f"КАТО «{kc}» не привязан к региону в DSS"
            continue
        r.indicator_id = ind.id
        r.region_id = region.id
        r.status = "ok"
        r.error_message = None


async def _revalidate_json_rows(
    db: AsyncSession, rows: list[DataIntakeRow]
) -> None:
    def _to_uuid(v: Any) -> Optional[UUID]:
        if v is None or v == "":
            return None
        if isinstance(v, UUID):
            return v
        try:
            return _uuid.UUID(str(v))
        except (ValueError, TypeError):
            return None

    indicator_ids: set[UUID] = set()
    region_ids: set[UUID] = set()
    for r in rows:
        raw = r.raw_payload or {}
        iid = _to_uuid(raw.get("indicator_id"))
        rid = _to_uuid(raw.get("region_id"))
        if iid:
            indicator_ids.add(iid)
        if rid:
            region_ids.add(rid)

    indicators_map: dict[UUID, Indicator] = {}
    if indicator_ids:
        result = (
            await db.execute(
                select(Indicator).where(Indicator.id.in_(indicator_ids))
            )
        ).scalars().all()
        indicators_map = {ind.id: ind for ind in result}

    regions_map: dict[UUID, Region] = {}
    if region_ids:
        result = (
            await db.execute(select(Region).where(Region.id.in_(region_ids)))
        ).scalars().all()
        regions_map = {reg.id: reg for reg in result}

    stations = await stations_lookup(db)
    for r in rows:
        if _row_has_period_value_error(r):
            continue
        raw = r.raw_payload or {}
        iid = _to_uuid(raw.get("indicator_id"))
        rid = _to_uuid(raw.get("region_id"))
        if iid is None:
            r.error_message = "indicator_id отсутствует или некорректен"
            continue
        sid = _to_uuid(raw.get("station_id"))
        if sid is not None:
            ind = indicators_map.get(iid)
            if ind is None:
                r.error_message = f"Показатель {iid} не найден"
                continue
            if stations.get(str(sid)) is None:
                r.error_message = f"Метеостанция {sid} не найдена"
                continue
            err = dekad_row_error(ind, r.period_type, r.period_start, r.period_end)
            if err:
                r.error_message = err
                continue
            r.indicator_id, r.station_id, r.region_id = iid, sid, None
            r.status = "ok"
            r.error_message = None
            continue
        if rid is None:
            r.error_message = "region_id отсутствует или некорректен"
            continue
        ind = indicators_map.get(iid)
        if ind is None:
            r.error_message = f"Показатель {iid} не найден"
            continue
        if not getattr(ind, "is_active", True):
            r.error_message = f"Показатель {iid} деактивирован"
            continue
        region = regions_map.get(rid)
        if region is None:
            r.error_message = f"Регион {rid} не найден"
            continue
        r.indicator_id = iid
        r.region_id = rid
        r.status = "ok"
        r.error_message = None


async def preview_job(db: AsyncSession, *, job: DataIntakeJob) -> IntakePreviewResult:
    """Dry-run diff ok-строк job против indicator_values (ничего не пишет).

    Классифицирует каждую ok-строку по unique-ключу iv_unique_value
    (indicator_id, region_id, period_type, period_start):
      new       — совпадения по ключу нет;
      changed   — совпадение есть, но value отличается;
      unchanged — совпадение есть, value совпадает.
    resets_approved — существующая запись сейчас approved: коммит (UPSERT) сбросит
    её validation_status на pending независимо от того, поменялось ли значение.
    """
    rows = (
        await db.execute(
            select(DataIntakeRow)
            .options(
                selectinload(DataIntakeRow.indicator),
                selectinload(DataIntakeRow.region),
            )
            .where(
                DataIntakeRow.job_id == job.id,
                DataIntakeRow.status == "ok",
            )
            .order_by(DataIntakeRow.row_no)
        )
    ).scalars().all()

    # Строки по метеостанциям сравниваются со значениями станций (meteo_station_values);
    # в общем словаре их ключ — (показатель, «station:<id>», тип, начало).
    from app.models.data import MeteoStation, MeteoStationValue

    station_rows = [r for r in rows if r.station_id is not None]
    station_names: dict = {}
    if station_rows:
        station_names = {
            st.id: st.name for st in (await db.execute(
                select(MeteoStation).where(MeteoStation.id.in_({r.station_id for r in station_rows}))
            )).scalars().all()
        }

    # Батч-фетч текущих значений по составному ключу одним запросом.
    keys = [
        (r.indicator_id, r.region_id, r.period_type, r.period_start)
        for r in rows if r.station_id is None
    ]
    existing: dict[tuple, tuple[Optional[float], Optional[str]]] = {}
    if keys:
        found = (
            await db.execute(
                select(
                    IndicatorValue.indicator_id,
                    IndicatorValue.region_id,
                    IndicatorValue.period_type,
                    IndicatorValue.period_start,
                    IndicatorValue.value,
                    IndicatorValue.validation_status,
                ).where(
                    tuple_(
                        IndicatorValue.indicator_id,
                        IndicatorValue.region_id,
                        IndicatorValue.period_type,
                        IndicatorValue.period_start,
                    ).in_(keys)
                )
            )
        ).all()
        for iid, rid, ptype, pstart, val, vstatus in found:
            existing[(iid, rid, ptype, pstart)] = (val, vstatus)
    if station_rows:
        found = (
            await db.execute(
                select(
                    MeteoStationValue.indicator_id, MeteoStationValue.station_id,
                    MeteoStationValue.period_type, MeteoStationValue.period_start,
                    MeteoStationValue.value, MeteoStationValue.validation_status,
                ).where(
                    tuple_(
                        MeteoStationValue.indicator_id, MeteoStationValue.station_id,
                        MeteoStationValue.period_type, MeteoStationValue.period_start,
                    ).in_([(r.indicator_id, r.station_id, r.period_type, r.period_start) for r in station_rows])
                )
            )
        ).all()
        for iid, sid, ptype, pstart, val, vstatus in found:
            existing[(iid, f"station:{sid}", ptype, pstart)] = (val, vstatus)

    items: list[IntakePreviewItem] = []
    new_count = changed_count = unchanged_count = resets_approved_count = 0

    for r in rows:
        place = f"station:{r.station_id}" if r.station_id else r.region_id
        key = (r.indicator_id, place, r.period_type, r.period_start)
        match = existing.get(key)
        if match is None:
            change_type = "new"
            old_value = None
            resets_approved = False
            new_count += 1
        else:
            old_value, vstatus = match
            resets_approved = vstatus == "approved"
            if old_value != r.value:
                change_type = "changed"
                changed_count += 1
            else:
                change_type = "unchanged"
                unchanged_count += 1
        if resets_approved:
            resets_approved_count += 1

        # Детализация: new/changed, а также unchanged-строки, которые всё равно
        # сбросят approved (чтобы таблица объясняла resets_approved_count).
        # Обычные unchanged — только в счётчике, чтобы не раздувать ответ.
        if change_type != "unchanged" or resets_approved:
            items.append(
                IntakePreviewItem(
                    row_no=r.row_no,
                    indicator_name=r.indicator.name if r.indicator else None,
                    region_name=(f"метеостанция {station_names.get(r.station_id, '')}".strip()
                                 if r.station_id else (r.region.name if r.region else None)),
                    period_type=r.period_type,
                    period_start=r.period_start,
                    change_type=change_type,
                    old_value=old_value,
                    new_value=r.value,
                    resets_approved=resets_approved,
                )
            )

    return IntakePreviewResult(
        job_id=job.id,
        total=len(rows),
        new_count=new_count,
        changed_count=changed_count,
        unchanged_count=unchanged_count,
        resets_approved_count=resets_approved_count,
        items=items,
    )


async def commit_job(db: AsyncSession, *, job: DataIntakeJob, user: User) -> tuple[int, int, list[str]]:
    """UPSERT ok-строк job в indicator_values. Меняет status у строк и job."""
    rows = (
        await db.execute(
            select(DataIntakeRow).where(
                DataIntakeRow.job_id == job.id,
                DataIntakeRow.status == "ok",
            )
        )
    ).scalars().all()

    from app.models.data import MeteoStationValue
    from app.services import climate_dekads

    written = 0
    skipped = 0
    errors: list[str] = []
    month_keys: set = set()

    for r in rows:
        if r.station_id is not None:
            # Декада метеостанции → meteo_station_values; месяц пересчитывается ниже.
            stmt = pg_insert(MeteoStationValue).values(
                station_id=r.station_id,
                indicator_id=r.indicator_id,
                period_type="dekad",
                period_start=r.period_start,
                period_end=r.period_end,
                dekad=climate_dekads.dekad_of(r.period_start),
                value=r.value,
                source_id=job.data_source_id,
                job_id=job.id,
                validation_status="pending",
                created_by=user.id,
            ).on_conflict_do_update(
                constraint="msv_unique",
                set_={
                    "value": r.value,
                    "source_id": job.data_source_id,
                    "job_id": job.id,
                    "validation_status": "pending",
                    "validated_by": None,
                    "validated_at": None,
                    "created_by": user.id,
                },
            )
            try:
                await db.execute(stmt)
                r.status = "imported"
                written += 1
                month_keys.add((r.station_id, r.indicator_id, r.period_start.replace(day=1)))
            except IntegrityError as e:
                await db.rollback()
                errors.append(f"row #{r.row_no}: {e.orig}")
                skipped += 1
            continue
        stmt = pg_insert(IndicatorValue).values(
            indicator_id=r.indicator_id,
            region_id=r.region_id,
            period_type=r.period_type,
            period_start=r.period_start,
            period_end=r.period_end,
            value=r.value,
            source_id=job.data_source_id,
            validation_status="pending",
            created_by=user.id,
        ).on_conflict_do_update(
            constraint="iv_unique_value",
            set_={
                "value": r.value,
                "period_end": r.period_end,
                "source_id": job.data_source_id,
                # Новые данные → новая валидация: сбрасываем прошлый approve/reject.
                "validation_status": "pending",
                "validated_by": None,
                "validated_at": None,
                "created_by": user.id,
            },
        )
        try:
            await db.execute(stmt)
            r.status = "imported"
            written += 1
        except IntegrityError as e:
            await db.rollback()
            errors.append(f"row #{r.row_no}: {e.orig}")
            skipped += 1

    if month_keys:
        await climate_dekads.recompute(db, month_keys)

    job.rows_imported = written
    job.status = "imported" if written > 0 and skipped == 0 else ("failed" if skipped and not written else "imported")
    job.committed_at = datetime.utcnow()
    job.committed_by = user.id
    await db.flush()
    return written, skipped, errors
