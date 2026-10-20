"""Шаг 6 алгоритма ЗБ-УР: показатели → индексы устойчивого развития.

Что установлено на данных заказчика (окно 2014-2024):
  * итоговый индекс = среднее арифметическое трёх блоков; правило подтверждено на
    всех 11 годах, максимальное отклонение 0,00033 (округление до трёх знаков);
  * ни один из трёх блоков не покрывает отрезок [0; 1] (социальный 0,560-0,635;
    экономический 0,147-0,568; экологический 0,822-0,864). Значит, нормировка
    внутри блоков сделана по внешним (нормативным) границам, а не по фактическому
    диапазону выборки. Восстановить эти границы из самих значений невозможно.

Отсюда два режима:

Режим A — реконструкция официального индекса. Проверяются заранее сформулированные
гипотезы о конструкции блоков. Критерий приёмки: MAE ≤ 2 %, MaxAE ≤ 5 %, совпадение
знака годовых приращений не менее чем в 9 из 10 переходов. Не прошло — режим A
отключается, и это фиксируется в отчёте.

Режим B — нормативный индекс DSS с полностью открытой конструкцией: нормировка по
референсным границам (а не по выборке — иначе индексы разных сценариев
несопоставимы), внутриблочная свёртка — среднее, межблочная — ГЕОМЕТРИЧЕСКОЕ
среднее, чтобы провал одного блока не компенсировался ростом другого.

Подмена официального индекса собственным без явной пометки запрещена.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from app.services.landscape_impact import ur_official
from app.services.landscape_impact.datamart import DataMart

logger = logging.getLogger(__name__)

_INDEX_FILE = Path(__file__).parent / "reference" / "dss_index.json"

BLOCKS: tuple[str, ...] = ("social", "economic", "ecological")

MODE_A_MAE = 0.02
MODE_A_MAX_AE = 0.05
MODE_A_MIN_SIGN_MATCHES = 9


@dataclass
class ReconstructionResult:
    hypothesis: str
    mae: float
    max_ae: float
    sign_matches: int
    sign_total: int
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis, "mae": round(self.mae, 5),
            "max_ae": round(self.max_ae, 5), "sign_matches": self.sign_matches,
            "sign_total": self.sign_total, "passed": self.passed,
        }


@dataclass
class SDIndexModel:
    """Действующая модель индекса УР: режим и способ пересчёта блоков."""

    mode: str                       # "A" | "B"
    reconstruction: list[ReconstructionResult]
    reference_bounds: dict[str, dict[str, float]]
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reconstruction": [r.as_dict() for r in self.reconstruction],
            "reference_bounds": self.reference_bounds,
            "note": self.note,
        }


def _minmax(values: np.ndarray) -> np.ndarray:
    v = np.asarray(values, float)
    rng = v.max() - v.min()
    return (v - v.min()) / rng if rng > 0 else np.zeros_like(v)


def _score(candidate: np.ndarray, actual: np.ndarray) -> ReconstructionResult:
    cand, act = np.asarray(candidate, float), np.asarray(actual, float)
    denom = np.where(np.abs(act) > 1e-12, np.abs(act), 1.0)
    rel = np.abs(cand - act) / denom
    sign_matches = int(np.sum(np.sign(np.diff(cand)) == np.sign(np.diff(act))))
    return ReconstructionResult(
        hypothesis="", mae=float(rel.mean()), max_ae=float(rel.max()),
        sign_matches=sign_matches, sign_total=len(act) - 1, passed=False,
    )


def try_reconstruct(mart: DataMart) -> list[ReconstructionResult]:
    """Режим A: проверка заранее заданных гипотез о конструкции блоков.

    Гипотезы фиксированы до расчёта. Перебор конструкций с выбором лучшей по
    подгонке здесь недопустим: он гарантированно найдёт «объяснение» на 11 точках
    и не переживёт первой же проверки.
    """
    results: list[ReconstructionResult] = []
    actual_total = mart.sd_index["total"]

    hypotheses: dict[str, Callable[[], Optional[np.ndarray]]] = {
        "H1: итог = среднее арифметическое трёх блоков":
            lambda: (mart.sd_index["social"] + mart.sd_index["economic"]
                     + mart.sd_index["ecological"]) / 3.0,
        "H2: итог = геометрическое среднее трёх блоков":
            lambda: np.cbrt(mart.sd_index["social"] * mart.sd_index["economic"]
                            * mart.sd_index["ecological"]),
        "H3: экономический блок = min-max ВРП по окну 2014-2024":
            lambda: _minmax(mart.economy["grp"]),
        "H4: экономический блок = ВРП, нормированный базой 2014 года":
            lambda: np.asarray(mart.economy["grp"], float) / float(mart.economy["grp"][0]) / 10.0,
    }
    targets = {
        "H1: итог = среднее арифметическое трёх блоков": actual_total,
        "H2: итог = геометрическое среднее трёх блоков": actual_total,
        "H3: экономический блок = min-max ВРП по окну 2014-2024": mart.sd_index["economic"],
        "H4: экономический блок = ВРП, нормированный базой 2014 года": mart.sd_index["economic"],
    }
    for name, fn in hypotheses.items():
        candidate = fn()
        if candidate is None:
            continue
        res = _score(candidate, targets[name])
        res.hypothesis = name
        res.passed = (
            res.mae <= MODE_A_MAE and res.max_ae <= MODE_A_MAX_AE
            and res.sign_matches >= MODE_A_MIN_SIGN_MATCHES
        )
        results.append(res)
    return results


def _official_available() -> tuple[bool, str]:
    """Доступен ли справочник официального индекса и насколько точно он сходится."""
    try:
        model = ur_official.load_model()
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return False, ""
    report = ur_official.verify_against_published(model)
    worst = max(
        (rep["max_abs_error"] for rep in report.values() if rep["max_abs_error"] is not None),
        default=None,
    )
    if worst is None or worst > 0.005:
        return False, f"сверка с опубликованными значениями не прошла (макс. {worst})"
    return True, f"редакция границ {model.bounds_edition}, макс. отклонение {worst:.1e}"


def build_model(mart: DataMart, base_year: Optional[int] = None) -> SDIndexModel:
    """Выбор режима индекса УР.

    Режим A означает, что конструкция официального индекса известна и он
    пересчитывается. До 22.09.2026 методики не было, и единственным способом
    получить режим A была реконструкция — подбор гипотезы, воспроизводящей
    опубликованные значения. Сейчас получена расчётная книга заказчика, индекс
    воспроизводится точно, и гадать больше не нужно: режим A включается по
    наличию справочника `ur_official.json`. Реконструкция сохранена как
    диагностика — она показывает, что подбором такую конструкцию было не угадать.

    Важно: режим A **не** переводит вердикт на официальный индекс. Землепользование
    затрагивает в нём один показатель из четырнадцати, и эффект любого варианта
    оказывается в семнадцать раз ниже порога различимости — ответ всегда был бы
    «различие не подтверждено». Именно поэтому заказчик разделил индексы (ответ
    на замечание 5), и вердикт по вариантам строится на индексе DSS.
    """
    results = try_reconstruct(mart)
    official_ok, detail = _official_available()
    if official_ok:
        mode, note = "A", (
            "Официальный индекс УР считается по методике заказчика из расчётной "
            f"книги ({detail}); воспроизведение опубликованных значений точное. "
            "Вердикт по вариантам землепользования строится на индексе DSS: в "
            "официальном составе показателей землепользование затрагивает один "
            "показатель из четырнадцати, и эффект ниже порога различимости."
        )
    else:
        mode, note = "B", (
            "Справочник официального индекса недоступен, ни одна из проверенных "
            "гипотез конструкции блоков не воспроизводит опубликованные значения. "
            "Публикуется индекс DSS; сопоставление с официальными значениями невозможно."
        )
    return SDIndexModel(
        mode=mode, reconstruction=results,
        reference_bounds=_reference_bounds(mart, base_year), note=note,
    )


@lru_cache(maxsize=1)
def index_definition() -> dict[str, Any]:
    """Конструкция индекса DSS: состав блоков, полярность, границы, свёртка."""
    with _INDEX_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


def index_version() -> str:
    return str(index_definition()["version"])


def _reference_bounds(mart: DataMart, base_year: Optional[int] = None) -> dict[str, dict[str, float]]:
    """Референсные (нормативные) границы нормировки индекса DSS.

    Границы берутся из `reference/dss_index.json` и не зависят от фактического
    диапазона выборки. Это принципиально: нормировка по min-max окна делает
    лучший и худший годы окна равными 1 и 0 по построению, индексы разных
    сценариев становятся несопоставимыми, а чувствительность индекса к малому
    изменению площади — произвольно высокой.
    """
    definition = index_definition()
    total = float(definition["region_total_area"])
    i = mart.year_index(base_year) if base_year is not None else mart.n_years - 1

    base_values = {
        "agri_output": float(mart.economy["agri_output"][i]),
        "grp": float(mart.economy["grp"][i]),
    }

    bounds: dict[str, dict[str, float]] = {}
    for name, spec in definition["components"].items():
        kind = spec["bound_kind"]
        if kind == "share_of_region":
            lo = float(spec["low_share"]) * total
            hi = float(spec["high_share"]) * total
        elif kind == "absolute":
            lo, hi = float(spec["low"]), float(spec["high"])
        elif kind == "relative_to_base_year":
            base = base_values[name]
            lo = float(spec["low_factor"]) * base
            hi = float(spec["high_factor"]) * base
        else:
            raise ValueError(f"Неизвестный тип границ '{kind}' у компонента {name}")
        bounds[name] = {"low": round(lo, 4), "high": round(hi, 4),
                        "polarity": int(spec["polarity"]), "label": spec["label"],
                        "bound_kind": kind}
    return bounds


def _normalize(value: float, bound: dict[str, float]) -> float:
    lo, hi = bound["low"], bound["high"]
    if hi <= lo:
        return 0.5
    x = (float(value) - lo) / (hi - lo)
    x = min(1.0, max(0.0, x))
    return x if bound["polarity"] > 0 else 1.0 - x


def dss_index(components: dict[str, float], bounds: dict[str, dict[str, float]]) -> dict[str, float]:
    """Режим B: нормативный индекс DSS с некомпенсаторной межблочной свёрткой.

    Внутриблочная свёртка — среднее арифметическое, межблочная — геометрическое
    среднее. Разница принципиальна: при арифметической свёртке (как в индексе
    заказчика) провал экологического блока полностью компенсируется ростом
    экономического, и деградация территории читается как устойчивое развитие.
    """
    definition = index_definition()
    blocks: dict[str, float] = {}
    for block, spec in definition["blocks"].items():
        vals = [_normalize(components[k], bounds[k])
                for k in spec["components"] if k in components and k in bounds]
        blocks[block] = float(np.mean(vals)) if vals else 0.0

    product = 1.0
    for b in BLOCKS:
        product *= max(blocks[b], 1e-9)
    blocks["total"] = float(product ** (1.0 / len(BLOCKS)))
    return blocks


def official_index_delta(mart: DataMart, base_year: int,
                         grp_rel: float, agri_rel: float,
                         mode: str) -> dict[str, float]:
    """Δ официального индекса УР от относительного изменения продукции СХ.

    С 22.09.2026 доступна расчётная книга заказчика, поэтому индекс считается
    ТОЧНО — пересчётом показателя, нормированием по фиксированным границам и
    пересборкой блоков (`ur_official.scenario_delta`). Прежняя линеаризация
    вокруг базы больше не нужна.

    Если справочник официального индекса отсутствует (например, на окружении,
    куда его не выкладывали), поведение прежнее: нули с признаком
    `applicable = 0`, а ответ строится на индексе DSS.
    """
    try:
        model = ur_official.load_model()
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("Справочник официального индекса недоступен: %s", exc)
        return {"social": 0.0, "economic": 0.0, "ecological": 0.0, "total": 0.0,
                "applicable": 0.0}

    if base_year not in model.years:
        # Год вне опубликованного окна: границы нормирования закреплены за
        # редакцией 2014–2024, расширять окно самовольно нельзя.
        return {"social": 0.0, "economic": 0.0, "ecological": 0.0, "total": 0.0,
                "applicable": 0.0, "reason": f"{base_year} вне окна методики"}

    delta = ur_official.scenario_delta(model, base_year, agri_rel)
    if not delta.get("applicable"):
        return {"social": 0.0, "economic": 0.0, "ecological": 0.0, "total": 0.0,
                "applicable": 0.0, "reason": delta.get("reason")}
    return {
        "social": 0.0,
        "economic": float(delta["economic"]),
        "ecological": 0.0,
        "total": float(delta["total"]),
        "applicable": 1.0,
        "base_total": float(delta["base_total"]),
        "bounds_edition": model.bounds_edition,
        "assumption": delta.get("assumption"),
    }
