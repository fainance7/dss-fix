"""Реестр экспертных и нормативных коэффициентов алгоритма ЗБ-УР.

Проектное правило: скрытых экспертных констант в коде нет. Всё, что не является
балансовым тождеством или статистической оценкой из данных, лежит в
`reference/assumptions.json` и доступно пользователю через
`GET /api/v1/landscape-impact/assumptions`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REF_DIR = Path(__file__).parent / "reference"
_ASSUMPTIONS_FILE = _REF_DIR / "assumptions.json"

# Статусы, при которых коэффициент считается неподтверждённым заказчиком.
# Прогон, где такой коэффициент влияет на результат, не может получить PUBLISH.
UNVERIFIED_STATUSES = frozenset({"unsigned", "data_derived_unverified"})


@dataclass(frozen=True)
class Assumption:
    """Экспертный коэффициент: диапазон [min, base, max] с источником."""

    key: str
    label: str
    kind: str
    value: Any
    unit: str
    source: str
    status: str

    @property
    def is_triple(self) -> bool:
        return isinstance(self.value, list) and len(self.value) == 3

    @property
    def base(self) -> float:
        if not self.is_triple:
            raise TypeError(f"Коэффициент {self.key} не является тройкой [min, base, max]")
        return float(self.value[1])

    @property
    def low(self) -> float:
        if not self.is_triple:
            raise TypeError(f"Коэффициент {self.key} не является тройкой [min, base, max]")
        return float(self.value[0])

    @property
    def high(self) -> float:
        if not self.is_triple:
            raise TypeError(f"Коэффициент {self.key} не является тройкой [min, base, max]")
        return float(self.value[2])

    @property
    def verified(self) -> bool:
        return self.status not in UNVERIFIED_STATUSES

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "label": self.label, "kind": self.kind,
            "value": self.value, "unit": self.unit, "source": self.source,
            "status": self.status, "verified": self.verified,
        }


@lru_cache(maxsize=1)
def _raw() -> dict[str, Any]:
    with _ASSUMPTIONS_FILE.open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def list_assumptions() -> dict[str, Assumption]:
    """Все коэффициенты, ключ → Assumption."""
    data = _raw()
    return {
        key: Assumption(
            key=key, label=item["label"], kind=item["kind"], value=item["value"],
            unit=item["unit"], source=item["source"], status=item["status"],
        )
        for key, item in data["assumptions"].items()
    }


def get_assumption(key: str) -> Assumption:
    try:
        return list_assumptions()[key]
    except KeyError:
        raise KeyError(f"Экспертный коэффициент '{key}' не определён в assumptions.json") from None


def assumptions_version() -> str:
    return str(_raw().get("version", "0"))


def unverified_keys() -> list[str]:
    """Коэффициенты без подписи экспертов заказчика — основание для warning в гейтах."""
    return sorted(k for k, a in list_assumptions().items() if not a.verified)
