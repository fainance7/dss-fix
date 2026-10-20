"""Контроль качества партии приёма данных до импорта.

Построчная валидация (`intake_service.validate_rows`) проверяет формат строки, но не
правдоподобие данных: повторную загрузку, схлопывание ключей, смену единиц, обрыв
ряда при смене методики, расхождение районов с областью. Эти проверки раньше
делались вручную SQL-запросами перед импортом; модуль выполняет их автоматически и
сохраняет отчёт в партии (`dataset_intake_jobs.quality_report`), а замечания по
конкретным значениям — в `intake_quality_flags`, чтобы эксперт по проверке видел их
и на странице значений.

Уровни замечаний:
- blocker — импорт возможен только с подтверждением и комментарием эксперта;
- warning — требует внимания эксперта;
- info — к сведению.

Все проверки детерминированные: сравниваются строки партии (status ok/imported) с
текущими значениями `indicator_values` (кроме отклонённых). Пороги — в системных
настройках (раздел «Настройки» → «Контроль качества данных»).
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.models.data_intake import DataIntakeJob, IntakeQualityFlag

SETTINGS_KEY = "system:settings"  # = app.api.v1.settings.SETTINGS_KEY
MAX_ITEMS = 40                     # строк-примеров на одно замечание в отчёте


class DataQualitySettings(BaseModel):
    """Пороги контроля качества. Хранятся в системных настройках (Redis)."""

    level_break_ratio: float = 2.5          # скачок год к году, во сколько раз
    widespread_break_share: float = 0.5     # доля районов с обрывом в одном году — «смена методики»
    unit_shift_factor: float = 8.0          # медианное отношение новое/текущее — «смена единиц»
    magnitude_factor: float = 8.0           # новое значение против медианы ряда
    district_sum_tolerance: float = 0.05    # допустимое расхождение суммы районов с областью
    incomplete_year_ratio: float = 0.6      # последний год ниже предыдущего в ... раз
    incomplete_year_share: float = 0.3      # ... у такой доли показателей
    changed_tolerance: float = 0.001        # относительное отличие, считаемое изменением
    # Декады метеостанций
    dekad_temp_min: float = -50.0           # допустимая средняя температура декады, °C
    dekad_temp_max: float = 45.0
    dekad_precip_max: float = 200.0         # осадки за декаду, мм — выше считается подозрительным
    dekad_anomaly_sigma: float = 4.0        # отклонение от многолетнего среднего той же декады, σ


async def load_settings() -> DataQualitySettings:
    try:
        redis = await get_cache()
        raw = await redis.get(SETTINGS_KEY) if redis else None
        if raw:
            data = json.loads(raw).get("data_quality")
            if data:
                return DataQualitySettings.model_validate(data)
    except Exception:  # noqa: BLE001 — недоступный Redis не должен ломать приём данных
        pass
    return DataQualitySettings()


# Показатели, которые складываются по районам (сумма районов = область).
_ADDITIVE_UNITS = {
    "тонна", "тонн", "т", "тыс.тонн", "тыс. тонн", "тыс.т", "га", "тыс.га", "тыс. га",
    "гол.", "голов", "человек", "чел.", "тыс. человек", "тыс.человек", "тыс. чел.",
    "тыс.шт.", "тыс. шт.", "млн.тенге", "млн. тенге", "млрд.тенге", "млрд. тенге",
    "тыс.тенге", "тыс. тенге",
}
_NON_ADDITIVE_WORDS = (
    "средн", "на 1 ", "на 100", "на 1000", "на 10 ", "на душу", "коэффициент", "уровень",
    "индекс", "доля", "урожайност", "удельн", "плотност",
)
# Показатели, которые могут быть отрицательными или менять знак.
_SIGNED_WORDS = ("прирост", "сальдо", "убыль", "изменени", "баланс", "отклонени", "температур")


def _is_additive(name: str, unit: Optional[str]) -> bool:
    n = name.lower()
    return (unit or "").strip().lower() in _ADDITIVE_UNITS and not any(w in n for w in _NON_ADDITIVE_WORDS)


def _is_signed(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in _SIGNED_WORDS)


def _fmt(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(v, 3) if abs(v) < 100 else round(v, 1)


def _num(v: Optional[float]) -> str:
    """Число для текста замечания: 4 967 878, 12,2, 0,054."""
    if v is None:
        return "—"
    digits = 0 if abs(v) >= 100 else (1 if abs(v) >= 1 else 3)
    txt = f"{v:,.{digits}f}".replace(",", "\u00a0").replace(".", ",")  # неразрывный пробел между разрядами
    return txt.rstrip("0").rstrip(",") if "," in txt else txt


class _Finding:
    def __init__(self, code: str, severity: str, title: str, message: str):
        self.code, self.severity, self.title, self.message = code, severity, title, message
        self.items: list[dict[str, Any]] = []
        self.count = 0
        # (indicator_id, region_id, period_type, period_start, короткое сообщение)
        self.flags: list[tuple] = []

    def add(self, item: dict[str, Any], flag: Optional[tuple] = None) -> None:
        self.count += 1
        if len(self.items) < MAX_ITEMS:
            self.items.append(item)
        if flag is not None:
            self.flags.append(flag)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "severity": self.severity, "title": self.title,
            "message": self.message, "count": self.count, "items": self.items,
        }


_ROWS_SQL = """
SELECT r.indicator_id, r.region_id, r.period_type, r.period_start, r.value,
       i.name AS indicator, u.name AS unit, g.name AS region, g.level AS level
FROM dataset_intake_rows r
JOIN indicators i ON i.id = r.indicator_id
LEFT JOIN units u ON u.id = i.unit_id
JOIN regions g ON g.id = r.region_id
WHERE r.job_id = CAST(:job AS uuid) AND r.status IN ('ok', 'imported')
  AND r.indicator_id IS NOT NULL AND r.region_id IS NOT NULL
  AND r.period_start IS NOT NULL AND r.value IS NOT NULL
"""

_EXISTING_SQL = """
SELECT iv.indicator_id, iv.region_id, iv.period_type, iv.period_start, iv.value, iv.validation_status
FROM indicator_values iv
WHERE iv.validation_status <> 'rejected'
  AND iv.indicator_id IN (SELECT DISTINCT indicator_id FROM dataset_intake_rows
                          WHERE job_id = CAST(:job AS uuid) AND status IN ('ok', 'imported'))
"""


async def build_report(db: AsyncSession, job: DataIntakeJob) -> tuple[dict[str, Any], list[_Finding]]:
    cfg = await load_settings()
    job_id = str(job.id)
    rows = (await db.execute(text(_ROWS_SQL), {"job": job_id})).mappings().all()
    findings: list[_Finding] = []

    # ── 1. Повторная загрузка ────────────────────────────────────────────
    dup = _Finding(
        "DUPLICATE_JOB", "blocker", "Повторная загрузка",
        "Партия построчно совпадает с другой партией. Импорт повторно запишет те же значения "
        "и снимет утверждение с уже проверенных; одну из партий следует отменить.",
    )
    candidates = (await db.execute(text(
        """SELECT id, source_ref, status, uploaded_at FROM dataset_intake_jobs
           WHERE id <> CAST(:job AS uuid) AND rows_ok = :n AND status IN ('pending','validated','imported')
             AND uploaded_at > now() - interval '180 days'
           ORDER BY uploaded_at DESC LIMIT 10"""), {"job": job_id, "n": job.rows_ok})).mappings().all()
    for c in candidates:
        diff = (await db.execute(text(
            """SELECT count(*) FROM (
                 (SELECT indicator_id, region_id, station_id, period_type, period_start, value FROM dataset_intake_rows
                   WHERE job_id = CAST(:a AS uuid) AND status IN ('ok','imported')
                  EXCEPT SELECT indicator_id, region_id, station_id, period_type, period_start, value FROM dataset_intake_rows
                   WHERE job_id = CAST(:b AS uuid) AND status IN ('ok','imported'))
                 UNION ALL
                 (SELECT indicator_id, region_id, station_id, period_type, period_start, value FROM dataset_intake_rows
                   WHERE job_id = CAST(:b AS uuid) AND status IN ('ok','imported')
                  EXCEPT SELECT indicator_id, region_id, station_id, period_type, period_start, value FROM dataset_intake_rows
                   WHERE job_id = CAST(:a AS uuid) AND status IN ('ok','imported'))) d"""),
            {"a": job_id, "b": str(c["id"])})).scalar_one()
        if diff == 0:
            dup.add({"job_id": str(c["id"]), "source_ref": c["source_ref"], "status": c["status"],
                     "uploaded_at": c["uploaded_at"].isoformat()})
    findings.append(dup)

    # Декады метеостанций — свой набор проверок; годовые/районные к ним не применяются.
    dekad_findings, n_dekads = await _dekad_checks(db, job, cfg)

    if not rows:
        findings += dekad_findings
        return _report(job, cfg, findings, rows_checked=n_dekads), findings

    meta = {r["indicator_id"]: (r["indicator"], r["unit"]) for r in rows}
    region_name = {r["region_id"]: r["region"] for r in rows}
    region_level = {r["region_id"]: r["level"] for r in rows}

    def key(r) -> tuple:
        return (r["indicator_id"], r["region_id"], r["period_type"], r["period_start"])

    def label(ind, reg, pstart) -> dict[str, Any]:
        return {"indicator": meta[ind][0], "unit": meta[ind][1], "region": region_name.get(reg),
                "year": pstart.year, "period_start": pstart.isoformat()}


    # ── 2. Коллизии ключа ────────────────────────────────────────────────
    coll = _Finding(
        "KEY_COLLISION", "blocker", "Совпадающие ключи в партии",
        "Несколько строк партии относятся к одному показателю, региону и периоду. При импорте "
        "останется только одно значение, остальные будут молча затёрты. Обычно это признак "
        "дополнительного разреза (сектор, категория), который надо загружать отдельными показателями.",
    )
    by_key: dict[tuple, list[float]] = defaultdict(list)
    for r in rows:
        by_key[key(r)].append(r["value"])
    for k, vals in by_key.items():
        if len(vals) > 1:
            coll.add({**label(k[0], k[1], k[3]), "rows": len(vals), "values": [_fmt(v) for v in vals[:5]]},
                     (*k, f"В партии {len(vals)} строки на одно значение"))
    findings.append(coll)

    new = {k: v[-1] for k, v in by_key.items()}

    existing_rows = (await db.execute(text(_EXISTING_SQL), {"job": job_id})).mappings().all()
    existing = {key(r): (r["value"], r["validation_status"]) for r in existing_rows}

    # ── 3. Смена единиц и изменение утверждённых значений ────────────────
    unit = _Finding(
        "UNIT_SHIFT", "blocker", "Похоже на смену единиц измерения",
        "Новые значения на порядок и более отличаются от текущих по тем же регионам и годам. "
        "Так бывает, когда данные пришли в других единицах (центнеры вместо тонн, тысячи вместо единиц). "
        "Проверьте, какие значения верные.",
    )
    changed = _Finding(
        "APPROVED_CHANGED", "warning", "Изменение утверждённых значений",
        "Импорт заменит уже утверждённые значения на другие и вернёт их на проверку.",
    )
    reset_same = _Finding(
        "APPROVED_RESET", "info", "Утверждённые значения вернутся на проверку",
        "Значения совпадают с утверждёнными, но после импорта их нужно будет утвердить заново.",
    )
    ratios: dict[UUID, list[tuple[tuple, float, float]]] = defaultdict(list)
    for k, v in new.items():
        if k not in existing:
            continue
        old, status = existing[k]
        if old and v and old > 0 and v > 0:
            ratios[k[0]].append((k, old, v))
        if status != "approved":
            continue
        if abs(v - old) <= cfg.changed_tolerance * max(abs(old), 1e-9):
            reset_same.add(label(k[0], k[1], k[3]))
        else:
            rel = (v - old) / abs(old) if old else None
            changed.add({**label(k[0], k[1], k[3]), "old": _fmt(old), "new": _fmt(v),
                         "change_pct": round(rel * 100, 1) if rel is not None else None},
                        (*k, f"Утверждённое {_num(old)} заменено на {_num(v)}"))
    for ind, pairs in ratios.items():
        if len(pairs) < 2:
            continue
        m = median(math.log10(v / o) for _, o, v in pairs)
        if abs(m) >= math.log10(cfg.unit_shift_factor):
            factor = 10 ** abs(round(m))
            direction = "меньше" if m < 0 else "больше"
            for k, o, v in pairs:
                unit.add({**label(ind, k[1], k[3]), "old": _fmt(o), "new": _fmt(v), "ratio": round(v / o, 4)},
                         (*k, f"Новое значение примерно в {factor:g} раз {direction} текущего — проверьте единицы"))
    findings += [unit, changed, reset_same]

    # Итоговый ряд после импорта: новые значения поверх текущих.
    merged: dict[tuple, dict] = defaultdict(dict)  # (ind, reg, ptype) -> {pstart: (value, is_new)}
    for k, (v, _) in existing.items():
        merged[(k[0], k[1], k[2])][k[3]] = (v, False)
    for k, v in new.items():
        merged[(k[0], k[1], k[2])][k[3]] = (v, True)

    # ── 4. Обрыв ряда ────────────────────────────────────────────────────
    brk = _Finding(
        "LEVEL_BREAK", "warning", "Обрыв ряда",
        f"После импорта значение меняется к предыдущему периоду более чем в {cfg.level_break_ratio:g} раза. "
        "Это может быть реальное событие, ошибка или смена методики учёта.",
    )
    wide = _Finding(
        "WIDESPREAD_BREAK", "warning", "Одновременный обрыв по районам",
        "В одном и том же году ряд резко меняется у большинства районов сразу. "
        "Обычно это смена методики (например, учтены только сельхозпредприятия без хозяйств населения) — "
        "уточните у источника данных.",
    )
    district_breaks: dict[tuple, list] = defaultdict(list)  # (ind, ptype, pstart, dir) -> [(reg, prev, cur)]
    districts_present: dict[tuple, set] = defaultdict(set)    # (ind, ptype, pstart) -> регионы
    for (ind, reg, ptype), series in merged.items():
        if _is_signed(meta[ind][0]):
            continue
        pts = sorted(series.items())
        for (p0, (v0, n0)), (p1, (v1, n1)) in zip(pts, pts[1:]):
            if not (n0 or n1):
                continue
            if region_level.get(reg) == 2 and n1:
                districts_present[(ind, ptype, p1)].add(reg)
            if v0 is None or v1 is None or v0 <= 0 or v1 <= 0:
                continue
            ratio = v1 / v0
            if ratio <= cfg.level_break_ratio and ratio >= 1 / cfg.level_break_ratio:
                continue
            item = {**label(ind, reg, p1), "prev_year": p0.year, "prev": _fmt(v0), "cur": _fmt(v1),
                    "ratio": round(ratio, 3), "source": ("новое" if n0 else "текущее") + " → " + ("новое" if n1 else "текущее")}
            if region_level.get(reg) == 1:
                brk.add(item, (ind, reg, ptype, p1, f"Скачок к {p0.year} г. в {_num(ratio)} раза"))
            elif n1:
                district_breaks[(ind, ptype, p1, ratio > 1)].append((reg, item))
    for (ind, ptype, p1, up), items in district_breaks.items():
        present = len(districts_present[(ind, ptype, p1)])
        if len(items) >= 5 and present and len(items) >= cfg.widespread_break_share * present:
            word = "рост" if up else "падение"
            for reg, item in items:
                wide.add({**item, "districts": f"{len(items)} из {present}"},
                         (ind, reg, ptype, p1, f"{word.capitalize()} сразу у {len(items)} из {present} районов — возможна смена методики"))
    findings += [wide, brk]

    # ── 5. Значение на порядок от ряда ───────────────────────────────────
    mag = _Finding(
        "MAGNITUDE", "warning", "Значение на порядок отличается от ряда",
        f"Новое значение отличается от медианы имеющегося ряда показателя по этому региону более чем в {cfg.magnitude_factor:g} раз.",
    )
    for k, v in new.items():
        if k in existing or v is None or v <= 0:
            continue
        hist = [val for p, (val, is_new) in merged[(k[0], k[1], k[2])].items() if not is_new and val and val > 0]
        if len(hist) < 3:
            continue
        med = median(hist)
        if v / med >= cfg.magnitude_factor or med / v >= cfg.magnitude_factor:
            mag.add({**label(k[0], k[1], k[3]), "new": _fmt(v), "median": _fmt(med), "ratio": round(v / med, 3)},
                    (*k, f"Значение {_num(v)} при медиане ряда {_num(med)}"))
    findings.append(mag)

    # ── 6. Сумма районов и область ───────────────────────────────────────
    dsum = _Finding(
        "DISTRICT_SUM", "warning", "Сумма районов не равна области",
        f"Для суммируемых показателей сумма по районам отличается от значения по области более чем на "
        f"{cfg.district_sum_tolerance * 100:g} %.",
    )
    oblast_ids = {reg for reg, lvl in region_level.items() if lvl == 1}
    district_count = (await db.execute(text("SELECT count(*) FROM regions WHERE level = 2"))).scalar_one()
    sums: dict[tuple, list[float]] = defaultdict(list)
    for k, v in new.items():
        if region_level.get(k[1]) == 2:
            sums[(k[0], k[2], k[3])].append(v)
    for k, v in new.items():
        if k[1] not in oblast_ids or not _is_additive(*meta[k[0]]) or not v:
            continue
        parts = sums.get((k[0], k[2], k[3]), [])
        if len(parts) < max(district_count - 1, 1):
            continue
        total = sum(parts)
        if abs(total / v - 1) > cfg.district_sum_tolerance:
            dsum.add({**label(k[0], k[1], k[3]), "oblast": _fmt(v), "districts_sum": _fmt(total),
                      "share_pct": round(total / v * 100, 1), "districts": len(parts)},
                     (*k, f"Сумма районов {_num(total / v * 100)} % от области"))
    findings.append(dsum)

    # ── 7. Отрицательные и нулевые значения ──────────────────────────────
    neg = _Finding(
        "NEGATIVE", "warning", "Отрицательные значения",
        "Отрицательное значение у показателя, который не может быть отрицательным.",
    )
    zero = _Finding(
        "ZERO", "info", "Нулевые значения",
        "Ноль у показателя, в ряду которого раньше нулей не было: возможно, пропуск данных записан как 0.",
    )
    for k, v in new.items():
        if v < 0 and not _is_signed(meta[k[0]][0]):
            neg.add({**label(k[0], k[1], k[3]), "new": _fmt(v)}, (*k, "Отрицательное значение"))
        elif v == 0:
            hist = [val for p, (val, is_new) in merged[(k[0], k[1], k[2])].items() if not is_new]
            if len(hist) >= 3 and all(h != 0 for h in hist):
                zero.add(label(k[0], k[1], k[3]), (*k, "Ноль в ряду без нулей"))
    findings += [neg, zero]

    # ── 8. Неполный последний год ────────────────────────────────────────
    years = sorted({k[3] for k in new if k[2] == "year"})
    inc = _Finding(
        "INCOMPLETE_YEAR", "warning", "Последний год может быть неполным",
        "У значительной доли показателей значение последнего года заметно ниже предыдущего. "
        "Проверьте, что это годовые данные, а не данные за часть года.",
    )
    if years:
        last = years[-1]
        compared, low = 0, []
        for k, v in new.items():
            if k[1] not in oblast_ids or k[2] != "year" or k[3] != last or _is_signed(meta[k[0]][0]):
                continue
            prev = merged[(k[0], k[1], k[2])].get(last.replace(year=last.year - 1))
            if not prev or not prev[0] or prev[0] <= 0 or v is None:
                continue
            compared += 1
            if v / prev[0] < cfg.incomplete_year_ratio:
                low.append((k, prev[0], v))
        if compared >= 5 and len(low) >= cfg.incomplete_year_share * compared:
            for k, p, v in low:
                inc.add({**label(k[0], k[1], k[3]), "prev": _fmt(p), "new": _fmt(v), "ratio": round(v / p, 3)},
                        (*k, f"Ниже {last.year - 1} г. в {_num(p / v)} раза"))
    findings.append(inc)

    findings += dekad_findings
    return _report(job, cfg, findings, rows_checked=len(rows) + n_dekads), findings


_STATION_ROWS_SQL = """
SELECT r.indicator_id, r.station_id, r.period_start, r.value,
       i.name AS indicator, u.name AS unit, s.name AS station, s.is_active
FROM dataset_intake_rows r
JOIN indicators i ON i.id = r.indicator_id
LEFT JOIN units u ON u.id = i.unit_id
JOIN meteo_stations s ON s.id = r.station_id
WHERE r.job_id = CAST(:job AS uuid) AND r.status IN ('ok', 'imported') AND r.station_id IS NOT NULL
  AND r.period_start IS NOT NULL AND r.value IS NOT NULL
"""


async def _dekad_checks(db: AsyncSession, job: DataIntakeJob,
                        cfg: DataQualitySettings) -> tuple[list[_Finding], int]:
    """Проверки декадных данных метеостанций (замечания — только в отчёте партии)."""
    from app.services import climate_dekads as cd

    rows = (await db.execute(text(_STATION_ROWS_SQL), {"job": str(job.id)})).mappings().all()
    if not rows:
        return [], 0

    def lab(r) -> dict[str, Any]:
        ps = r["period_start"]
        return {"indicator": r["indicator"], "region": f"метеостанция {r['station']}", "year": ps.year,
                "month": ps.month, "dekad": cd.dekad_of(ps), "period_start": ps.isoformat()}

    is_temp = {r["indicator_id"]: r["indicator"] == cd.TEMPERATURE for r in rows}

    dup = _Finding("DEKAD_DUP_IN_BATCH", "blocker", "Декада повторяется в партии",
                   "Несколько строк на одну станцию, показатель и декаду — при импорте останется одна.")
    temp = _Finding("TEMP_RANGE", "blocker", "Недопустимая температура декады",
                    f"Средняя температура декады вне диапазона {cfg.dekad_temp_min:g}…{cfg.dekad_temp_max:g} °C — "
                    "вероятна ошибка ввода или единиц.")
    neg = _Finding("PRECIP_NEGATIVE", "blocker", "Отрицательные осадки",
                   "Сумма осадков за декаду не может быть отрицательной.")
    big = _Finding("PRECIP_RANGE", "warning", "Очень большие осадки за декаду",
                   f"Сумма осадков за декаду больше {cfg.dekad_precip_max:g} мм — проверьте значение.")
    inactive = _Finding("STATION_INACTIVE", "warning", "Станция помечена неактивной",
                        "Данные загружены по станции, отключённой в справочнике.")
    anomaly = _Finding("TEMP_ANOMALY", "warning", "Температура далека от многолетней нормы декады",
                       f"Отклонение больше {cfg.dekad_anomaly_sigma:g} σ от среднего этой декады на станции "
                       "(по истории не менее 10 лет).")
    incomplete = _Finding("DEKAD_INCOMPLETE_MONTH", "warning", "Месяц не будет рассчитан",
                          "Для станции и месяца нет всех трёх декад (в партии и в Системе) — "
                          "месячное значение появится, когда будут загружены недостающие декады.")

    seen: dict[tuple, int] = defaultdict(int)
    for r in rows:
        seen[(r["indicator_id"], r["station_id"], r["period_start"])] += 1
    for r in rows:
        k = (r["indicator_id"], r["station_id"], r["period_start"])
        if seen[k] > 1:
            dup.add({**lab(r), "rows": seen[k]})
            seen[k] = 0
        v = r["value"]
        if is_temp[r["indicator_id"]]:
            if not cfg.dekad_temp_min <= v <= cfg.dekad_temp_max:
                temp.add({**lab(r), "new": _fmt(v)})
        else:
            if v < 0:
                neg.add({**lab(r), "new": _fmt(v)})
            elif v > cfg.dekad_precip_max:
                big.add({**lab(r), "new": _fmt(v)})
        if not r["is_active"]:
            inactive.add(lab(r))

    # Полнота месяцев: декады партии + уже имеющиеся (не отклонённые)
    months = {(r["indicator_id"], r["station_id"], r["period_start"].replace(day=1)) for r in rows}
    have: dict[tuple, set] = defaultdict(set)
    for r in rows:
        have[(r["indicator_id"], r["station_id"], r["period_start"].replace(day=1))].add(r["period_start"].day)
    existing = (await db.execute(text("""
        SELECT indicator_id, station_id, date_trunc('month', period_start)::date AS m, extract(day FROM period_start)::int AS d
        FROM meteo_station_values
        WHERE period_type = 'dekad' AND validation_status <> 'rejected'
          AND (indicator_id, station_id, date_trunc('month', period_start)::date) IN
              (SELECT * FROM unnest(CAST(:ind AS uuid[]), CAST(:st AS uuid[]), CAST(:m AS date[])))
    """), {"ind": [m[0] for m in months], "st": [m[1] for m in months], "m": [m[2] for m in months]})).all()
    for ind, st, m, d in existing:
        have[(ind, st, m)].add(d)
    names = {r["station_id"]: r["station"] for r in rows}
    ind_names = {r["indicator_id"]: r["indicator"] for r in rows}
    for (ind, st, m), days in sorted(have.items(), key=lambda x: (str(x[0][1]), x[0][2])):
        if len(days & {1, 11, 21}) < 3:
            missing = [str(n) for n, d in ((1, 1), (2, 11), (3, 21)) if d not in days]
            incomplete.add({"indicator": ind_names[ind], "region": f"метеостанция {names[st]}", "year": m.year,
                            "month": m.month, "values": f"нет декад: {', '.join(missing)}"})

    # Аномалия температуры против многолетнего среднего той же декады станции
    temp_rows = [r for r in rows if is_temp[r["indicator_id"]]]
    if temp_rows:
        hist = (await db.execute(text("""
            SELECT station_id, extract(month FROM period_start)::int AS mo, extract(day FROM period_start)::int AS d,
                   avg(value) AS mu, stddev_samp(value) AS sd, count(*) AS n
            FROM meteo_station_values
            WHERE period_type = 'dekad' AND validation_status = 'approved' AND indicator_id = ANY(:ind)
              AND station_id = ANY(:st) AND (job_id IS NULL OR job_id <> CAST(:job AS uuid))
            GROUP BY 1, 2, 3
        """), {"ind": list({r["indicator_id"] for r in temp_rows}), "st": list({r["station_id"] for r in temp_rows}),
               "job": str(job.id)})).all()
        stats = {(h.station_id, h.mo, h.d): h for h in hist}
        for r in temp_rows:
            h = stats.get((r["station_id"], r["period_start"].month, r["period_start"].day))
            if h and h.n >= 10 and h.sd and abs(r["value"] - h.mu) > cfg.dekad_anomaly_sigma * h.sd:
                anomaly.add({**lab(r), "new": _fmt(r["value"]), "median": _fmt(h.mu),
                             "ratio": round((r["value"] - h.mu) / h.sd, 1)})

    return [dup, temp, neg, big, inactive, anomaly, incomplete], len(rows)


def _report(job: DataIntakeJob, cfg: DataQualitySettings, findings: list[_Finding], rows_checked: int) -> dict:
    active = [f for f in findings if f.count]
    order = {"blocker": 0, "warning": 1, "info": 2}
    active.sort(key=lambda f: (order[f.severity], -f.count))
    summary = {s: sum(1 for f in active if f.severity == s) for s in order}
    return {
        "job_id": str(job.id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows_checked": rows_checked,
        "summary": summary,
        "findings": [f.to_dict() for f in active],
        "thresholds": cfg.model_dump(),
    }


async def refresh(db: AsyncSession, job: DataIntakeJob) -> dict[str, Any]:
    """Пересчитать отчёт партии и её замечания по значениям (сохраняется в БД, без commit)."""
    report, findings = await build_report(db, job)
    previous = job.quality_report or {}
    if previous.get("acknowledged"):
        report["acknowledged"] = previous["acknowledged"]
    job.quality_report = report
    await db.execute(delete(IntakeQualityFlag).where(IntakeQualityFlag.job_id == job.id))
    seen: set[tuple] = set()
    for f in findings:
        if f.severity == "info":
            continue
        for ind, reg, ptype, pstart, msg in f.flags:
            if (ind, reg, ptype, pstart, f.code) in seen:
                continue
            seen.add((ind, reg, ptype, pstart, f.code))
            db.add(IntakeQualityFlag(job_id=job.id, indicator_id=ind, region_id=reg, period_type=ptype,
                                     period_start=pstart, code=f.code, severity=f.severity, message=msg))
    await db.flush()
    return report


async def safe_refresh(db: AsyncSession, job: DataIntakeJob) -> None:
    """Пересчёт при загрузке/перепроверке: сбой контроля качества не должен ломать приём."""
    try:
        async with db.begin_nested():
            await refresh(db, job)
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception("Контроль качества партии %s не выполнен", job.id)


def blockers(report: Optional[dict]) -> int:
    return int(((report or {}).get("summary") or {}).get("blocker", 0))
