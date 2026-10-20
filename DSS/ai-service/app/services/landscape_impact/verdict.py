"""Шаг 7 алгоритма ЗБ-УР: ответ на вопрос «ведут ли изменения к устойчивому развитию».

Три исхода, и «не подтверждено» — легитимный и ожидаемый из них. Он обязателен,
в частности, когда эффект меньше минимального содержательного (MDE), когда
интервал пересекает ноль, когда провален блокирующий гейт и когда методика
индекса УР не предоставлена, а режим A реконструкции не прошёл.

Вето по экологическому блоку: при вероятности его заметного ухудшения выше
порога ответ не может быть «способствует устойчивому развитию» — только
«улучшает экономику ценой экологического блока». Без такого правила
компенсаторная свёртка официального индекса (итог = среднее трёх блоков)
превращает деградацию экологии в допустимый размен.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.services.landscape_impact.uncertainty import Distribution

VERDICT_SUPPORTS = "supports"
VERDICT_HINDERS = "hinders"
VERDICT_INCONCLUSIVE = "inconclusive"

VERDICT_LABELS = {
    VERDICT_SUPPORTS: "Способствует устойчивому развитию",
    VERDICT_HINDERS: "Препятствует устойчивому развитию",
    VERDICT_INCONCLUSIVE: "Не подтверждено",
}

PROBABILITY_THRESHOLD = 0.90
WIDTH_RATIO_LIMIT = 3.0   # гейт G11


def width_ok(total: Distribution, mde: float) -> bool:
    """Гейт G11: интервал достаточно узок, чтобы по нему что-то утверждать.

    Узким считается интервал не шире утроенной точечной оценки — либо целиком
    лежащий внутри ±MDE. Второе условие нужно для эффектов около нуля: отношение
    ширины к почти нулевой точке бесконечно велико, хотя вывод «эффект меньше
    порога различимости» при таком интервале надёжен.
    """
    lo80, hi80 = total.interval(0.80)
    point = total.median
    if abs(point) < 1e-12 or (hi80 - lo80) <= WIDTH_RATIO_LIMIT * abs(point):
        return True
    return mde > 0 and lo80 > -mde and hi80 < mde


@dataclass
class Verdict:
    code: str
    label: str
    reason: str
    eco_veto: bool
    statement: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "label": self.label, "reason": self.reason,
            "eco_veto": self.eco_veto, "statement": self.statement,
            "details": self.details,
        }


def decide(total: Distribution, ecological: Optional[Distribution], mde: float,
           mde_sigma: float, *, blocking_gates_passed: bool,
           eco_veto_probability: float = 0.20,
           index_source: str = "DSS") -> Verdict:
    """Итоговый вердикт по распределению Δ индекса УР."""
    lo80, hi80 = total.interval(0.80)
    p_pos = total.p_positive
    p_neg = 1.0 - p_pos
    point = total.median

    eco_veto = False
    if ecological is not None:
        p_eco_bad = float((ecological.samples < -mde).mean())
        eco_veto = p_eco_bad > eco_veto_probability
    else:
        p_eco_bad = None

    if not blocking_gates_passed:
        code, reason = VERDICT_INCONCLUSIVE, "Провален хотя бы один блокирующий гейт валидации."
    elif not width_ok(total, mde):
        code, reason = VERDICT_INCONCLUSIVE, (
            f"Ширина 80 % интервала ({hi80 - lo80:.4f}) превышает утроенную точечную "
            f"оценку ({abs(point):.4f}) — гейт G11."
        )
    elif p_pos >= PROBABILITY_THRESHOLD and lo80 > mde and not eco_veto:
        code, reason = VERDICT_SUPPORTS, (
            f"P(Δ > 0) = {p_pos:.2f} ≥ {PROBABILITY_THRESHOLD}; нижняя граница 80 % "
            f"интервала {lo80:.4f} превышает MDE {mde:.4f}; вето не сработало."
        )
    elif p_pos >= PROBABILITY_THRESHOLD and lo80 > mde and eco_veto:
        code, reason = VERDICT_INCONCLUSIVE, (
            f"Экономический эффект положителен, но вероятность заметного ухудшения "
            f"экологического блока {p_eco_bad:.2f} превышает порог {eco_veto_probability:.2f}. "
            f"Ответ: улучшает экономику ценой экологического блока."
        )
    elif p_neg >= PROBABILITY_THRESHOLD and hi80 < -mde:
        code, reason = VERDICT_HINDERS, (
            f"P(Δ < 0) = {p_neg:.2f} ≥ {PROBABILITY_THRESHOLD}; верхняя граница 80 % "
            f"интервала {hi80:.4f} ниже −MDE."
        )
    elif abs(point) < mde:
        code, reason = VERDICT_INCONCLUSIVE, (
            f"|Δ| = {abs(point):.4f} меньше минимального содержательного эффекта {mde:.4f}."
        )
    else:
        code, reason = VERDICT_INCONCLUSIVE, (
            f"Интервал пересекает ноль: 80 % интервал [{lo80:.4f}; {hi80:.4f}], "
            f"P(Δ > 0) = {p_pos:.2f}."
        )

    statement = (
        f"Изменение итогового индекса УР ({index_source}): оценка {point:+.4f} "
        f"при 80 % интервале от {lo80:+.4f} до {hi80:+.4f}. "
        f"Минимальный содержательный эффект {mde:.4f} — половина стандартного "
        f"отклонения годовых приращений индекса за окно калибровки ({mde_sigma:.4f})."
    )
    if code == VERDICT_INCONCLUSIVE and abs(point) < mde:
        statement = (
            f"Изменение итогового индекса УР ({index_source}) не подтверждено: "
            f"оценка {point:+.4f} при интервале от {lo80:+.4f} до {hi80:+.4f} — "
            f"меньше минимального содержательного эффекта {mde:.4f} (половина "
            f"стандартного отклонения годовых приращений индекса за окно калибровки, "
            f"равного {mde_sigma:.4f})."
        )

    return Verdict(
        code=code, label=VERDICT_LABELS[code], reason=reason, eco_veto=eco_veto,
        statement=statement,
        details={
            "point": round(point, 6), "ci80": [round(lo80, 6), round(hi80, 6)],
            "p_positive": round(p_pos, 4), "mde": round(mde, 6),
            "mde_sigma": round(mde_sigma, 6),
            "p_ecological_decline": (None if p_eco_bad is None else round(p_eco_bad, 4)),
            "eco_veto_threshold": eco_veto_probability,
            "index_source": index_source,
        },
    )


def compare(a: Distribution, b: Distribution, name_a: str, name_b: str) -> dict[str, Any]:
    """Попарное сравнение вариантов по распределению разности, а не по точкам.

    Ранжирование точечных оценок при таких интервалах не имеет смысла: варианты,
    отличающиеся на величину меньше ширины интервала, неразличимы.
    """
    n = min(a.samples.size, b.samples.size)
    diff = a.samples[:n] - b.samples[:n]
    p_a_better = float((diff > 0).mean())
    indistinguishable = 0.25 <= p_a_better <= 0.75
    return {
        "variant_a": name_a, "variant_b": name_b,
        "p_a_better": round(p_a_better, 4),
        "median_difference": round(float(__import__("numpy").median(diff)), 6),
        "indistinguishable": indistinguishable,
        "conclusion": (
            f"Различия между вариантами {name_a} и {name_b} не подтверждены "
            f"(P = {p_a_better:.2f} попадает в интервал неразличимости [0,25; 0,75])."
            if indistinguishable else
            f"Вариант {name_a if p_a_better > 0.5 else name_b} предпочтительнее с "
            f"вероятностью {max(p_a_better, 1 - p_a_better):.2f}."
        ),
    }
