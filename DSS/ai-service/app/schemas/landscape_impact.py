"""Контракты API алгоритма ЗБ-УР (земельный баланс → устойчивое развитие)."""
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# Коды вариантов ландшафтного планирования в каталоге смежной команды.
VARIANT_CODES = ("lp-v1-conservative", "lp-v2-balanced", "lp-v3-development")


class ScenarioRequest(BaseModel):
    """Вариант землепользования для оценки.

    Задаётся одним из двух способов:
      * `target_areas` — целевые площади категорий земель, тыс. га. Работает уже
        сейчас, до появления прогонов ландшафтного планирования;
      * `class_shares` + `class_map` — доли классов растра варианта ЛП и таблица
        соответствия «класс ЛП → категория земель». Таблица запрошена у смежной
        команды как P0; без неё этот путь не запускается.
    """

    name: str = Field(min_length=1, max_length=200)
    variant_code: Optional[str] = Field(default=None, max_length=50)
    target_areas: Optional[dict[str, float]] = None
    class_shares: Optional[dict[str, float]] = None
    class_map: Optional[dict[str, str]] = None
    horizon_years: int = Field(default=10, ge=1, le=30)
    base_year: Optional[int] = Field(default=None, ge=2014, le=2024)
    suitability_shift: float = Field(default=0.0, ge=-3.0, le=3.0)
    coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # [Э] сдвиг угодий внутри земель с/х назначения, тыс. га при полной реализации:
    # {"arable": Δпашни, "pasture": Δпастбищ}. См. GET /landscape-impact/variant-presets.
    land_use_shift: Optional[dict[str, float]] = None
    region_id: Optional[UUID] = None
    created_by: Optional[UUID] = None

    @model_validator(mode="after")
    def _one_input_required(self) -> "ScenarioRequest":
        if self.target_areas is None and self.class_shares is None and not self.land_use_shift:
            raise ValueError(
                "Вариант не задан: укажите target_areas (площади категорий, тыс. га), "
                "land_use_shift (сдвиг угодий) или class_shares вместе с class_map."
            )
        if self.land_use_shift and set(self.land_use_shift) - {"arable", "pasture"}:
            raise ValueError("land_use_shift допускает только ключи arable и pasture")
        if self.class_shares is not None and not self.class_map:
            raise ValueError(
                "Для растрового варианта нужна таблица соответствия «класс ЛП → "
                "категория земель» (class_map). Таблица запрошена у смежной команды (P0-1)."
            )
        return self


class RunCreateRequest(ScenarioRequest):
    """Запрос на полный прогон с Монте-Карло."""

    draws: int = Field(default=5000, ge=500, le=50_000)
    seed: int = Field(default=20260909)


class Band(BaseModel):
    """Оценка с границами без вероятностной трактовки (экспресс-режим)."""

    base: float
    low: float
    high: float
    unit: str
    link: str = Field(description="Метка типа связи: Б — балансовая, С — статистическая, Э — экспертная")


class ExpressEstimate(BaseModel):
    """Экспресс-оценка влияния на рост экономики."""

    scenario: dict[str, Any]
    versions: dict[str, str]
    land_balance_delta: dict[str, float]
    agri_output: Band
    agri_output_rel: Band
    grp: Band
    grp_rel: Band
    transition_violations: list[dict[str, Any]]
    land_use_shift_check: Optional[dict[str, Any]] = None
    district_check: Optional[dict[str, Any]] = None
    disclaimer: str
    unverified_assumptions: list[str]


class RunSummary(BaseModel):
    """Строка списка прогонов."""

    id: UUID
    name: str
    variant_code: Optional[str]
    base_year: int
    horizon_years: int
    status: str
    gate_status: Optional[str]
    verdict: Optional[str]
    algorithm_version: str
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    """Полный результат прогона со всей обвязкой."""

    scenario_input: dict[str, Any]
    result: Optional[dict[str, Any]]
    error: Optional[str]
    assumptions_version: str
    datamart_version: str
    random_seed: int
    draws: int


class CalibrationResponse(BaseModel):
    """Результат калибровки на историческом окне."""

    window: list[int]
    balance_ok: bool
    structural_shares: dict[str, dict[str, float]]
    yield_link: Optional[dict[str, Any]]
    yield_candidates: list[dict[str, Any]]
    yield_candidates_oblast: list[dict[str, Any]] = Field(default_factory=list)
    direct_land_links: dict[str, Any]
    sd_aggregation: dict[str, Any]
    mde: dict[str, float]
    vds_share_observed: dict[str, float]
    notes: list[str]
    versions: dict[str, str]
    data_issues: list[dict[str, Any]]


class AssumptionItem(BaseModel):
    key: str
    label: str
    kind: str
    value: Any
    unit: str
    source: str
    status: str
    verified: bool


class AssumptionsResponse(BaseModel):
    version: str
    algorithm_version: str
    dss_index_version: str
    assumptions: list[AssumptionItem]
    unverified: list[str]
    dss_index_definition: dict[str, Any]


class CompareResponse(BaseModel):
    pairs: list[dict[str, Any]]
    note: str
