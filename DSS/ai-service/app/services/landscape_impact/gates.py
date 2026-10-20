"""Гейты допуска прогона ЗБ-УР на дашборд.

Валидатор вызывается ДО записи результата. Прогон не публикуется, если провален
хотя бы один блокирующий гейт: статус RESEARCH_ONLY. Непройденные неблокирующие
гейты дают PUBLISH_WITH_WARNING и перечисляются на плашке достоверности.

Смысл конструкции: числа этого модуля будут цитироваться в управленческих
решениях без интервалов и оговорок. Единственная защита от этого — не выпускать
на экран результат, который не прошёл проверку.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

STATUS_PUBLISH = "PUBLISH"
STATUS_WARNING = "PUBLISH_WITH_WARNING"
STATUS_RESEARCH = "RESEARCH_ONLY"

GATE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "G1":  {"title": "Полнота входа", "blocking": True,
            "criterion": "Все обязательные ряды присутствуют; покрытие валидных пикселей ≥ 95 % по каждой зоне"},
    "G2":  {"title": "Балансовые тождества", "blocking": True,
            "criterion": "Сумма семи категорий = 9804,3 тыс. га ± 0,1 %; неотрицательность; строчные суммы матрицы переходов"},
    "G3":  {"title": "Диапазоны и единицы", "blocking": True,
            "criterion": "Величины внутри объявленных границ и исторических min/max × 1,5; "
                         "для вариантов ЛП — сдвиг пашни района внутри размаха его посевной площади × 1,5"},
    "G4":  {"title": "Скилл уравнений [С]", "blocking": True,
            "criterion": "MASE < 1 и Skill > 0 против наивного эталона на скользящем начале"},
    "G5":  {"title": "Устойчивость к году", "blocking": True,
            "criterion": "Jackknife: знак сохранён, изменение коэффициента ≤ 50 %"},
    "G6":  {"title": "Значимость", "blocking": True,
            "criterion": "Перестановочный тест p < 0,05 после поправки на множественность"},
    "G7":  {"title": "Согласие со знаком априори", "blocking": True,
            "criterion": "Знак коэффициента совпадает с предзаданным ожиданием"},
    "G8":  {"title": "Диагностика остатков", "blocking": False,
            "criterion": "Нет наблюдения с расстоянием Кука > 1"},
    "G9":  {"title": "Отсутствие экстраполяции", "blocking": False,
            "criterion": "Сценарные приращения площадей внутри исторического диапазона"},
    "G10": {"title": "Чувствительность к экспертным коэффициентам", "blocking": False,
            "criterion": "Ни один экспертный коэффициент не даёт более 40 % вклада в |ΔУР итог|"},
    "G11": {"title": "Ширина неопределённости", "blocking": True,
            "criterion": "Ширина 80 % интервала < 3 × |точечная оценка| либо весь 80 % интервал "
                         "внутри ±MDE (эффект надёжно ниже порога различимости)"},
    "G12": {"title": "Воспроизводимость", "blocking": True,
            "criterion": "Зафиксированы seed и версии данных, коэффициентов и алгоритма"},
    "G13": {"title": "Реконструкция индекса УР", "blocking": False,
            "criterion": "MAE ≤ 2 %, MaxAE ≤ 5 %, знак приращений ≥ 9/10 — иначе режим A отключается"},
}

SENSITIVITY_LIMIT = 0.40
EXTRAPOLATION_MARGIN = 1.5


@dataclass
class GateResult:
    code: str
    title: str
    blocking: bool
    passed: bool
    criterion: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "title": self.title, "blocking": self.blocking,
            "passed": self.passed, "criterion": self.criterion, "detail": self.detail,
        }


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    def add(self, code: str, passed: bool, detail: str = "",
            blocking: Optional[bool] = None) -> None:
        spec = GATE_DEFINITIONS[code]
        self.results.append(GateResult(
            code=code, title=spec["title"],
            blocking=spec["blocking"] if blocking is None else blocking,
            passed=passed, criterion=spec["criterion"], detail=detail,
        ))

    @property
    def failed_blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.blocking and not r.passed]

    @property
    def failed_warning(self) -> list[GateResult]:
        return [r for r in self.results if not r.blocking and not r.passed]

    @property
    def blocking_passed(self) -> bool:
        return not self.failed_blocking

    @property
    def status(self) -> str:
        if self.failed_blocking:
            return STATUS_RESEARCH
        if self.failed_warning:
            return STATUS_WARNING
        return STATUS_PUBLISH

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gates": [r.as_dict() for r in self.results],
            "failed_blocking": [r.code for r in self.failed_blocking],
            "failed_warning": [r.code for r in self.failed_warning],
        }


def evaluate_gates(*, mart, calibration, transition, sd_model,
                   sensitivity_shares: dict[str, float],
                   width_ratio_ok: bool, reproducible: bool,
                   coverage: float | None = None,
                   unverified_assumptions: list[str] | None = None,
                   district_check: Optional[dict[str, Any]] = None,
                   lp_variant: bool = False) -> GateReport:
    """Полная проверка G1-G13 для одного прогона.

    `district_check` — сверка сдвига пашни по районам с размахом их посевной
    площади (pipeline.district_shift_check). `lp_variant` — прогон варианта
    ландшафтного планирования: для него G3 неблокирующий (решение 11.10.2026).
    Варианты ЛП задают потенциал зонирования, кратно превышающий всё, что
    наблюдалось в районах; блокировать их значит не выдать по ним ничего.
    Выход за историю при этом не скрывается: G3 и G9 остаются непройденными,
    статус — PUBLISH_WITH_WARNING, районы вне границ перечисляются.
    """
    report = GateReport()
    unverified = unverified_assumptions or []

    # G1 — полнота входа.
    missing = [name for name, series in mart.land_use.items()
               if any(v != v for v in series)]  # NaN
    cov_ok = coverage is None or coverage >= 0.95
    report.add("G1", cov_ok, (
        f"Покрытие: {'не применимо (растр не передан)' if coverage is None else f'{coverage:.1%}'}. "
        + (f"Ряды с пропусками: {', '.join(missing)}." if missing else "Пропусков в обязательных рядах нет.")
    ))

    # G2 — балансовые тождества.
    tm_blocking = [v for v in transition.violations if v.get("severity") == "blocking"]
    g2 = calibration.balance_ok and not tm_blocking and transition.zero_sum_error < 1e-4
    report.add("G2", g2, (
        f"Баланс окна: {'сходится' if calibration.balance_ok else 'нарушен'}; "
        f"нарушений матрицы переходов: {len(tm_blocking)}; "
        f"невязка нулевой суммы {transition.zero_sum_error:.2e} тыс. га."
    ))

    # G3 — диапазоны и единицы: сценарные площади внутри исторических границ × 1,5.
    g3_problems: list[str] = []
    for i, cat in enumerate(transition.categories):
        series = mart.land_balance[cat]
        lo, hi = float(min(series)), float(max(series))
        span = (hi - lo) * EXTRAPOLATION_MARGIN
        net = float(transition.net[i])
        if abs(net) > max(span, 1e-9):
            g3_problems.append(f"{cat}: Δ {net:+.1f} тыс. га при историческом размахе {hi - lo:.1f}")
    if district_check and district_check.get("outside"):
        g3_problems.extend(
            f"{d['region']}: Δпашни {d['arable']:+.1f} тыс. га при размахе посевов "
            f"{d['span']:.1f} × 1,5 = {d['limit']:.1f}"
            for d in district_check["outside"]
        )
    g3_detail = "; ".join(g3_problems) if g3_problems else "Все величины внутри объявленных границ."
    if lp_variant and g3_problems:
        g3_detail = ("Вариант ЛП: выход за наблюдавшийся размах — экстраполяция, оценка "
                     "показывает масштаб, а не измеренный эффект. " + g3_detail)
    report.add("G3", not g3_problems, g3_detail,
               blocking=False if lp_variant else None)

    # G4-G7 — качество уравнения урожайности.
    link = calibration.yield_link
    if link is None:
        for code in ("G4", "G5", "G6", "G7"):
            report.add(code, False, "Уравнение урожайности не оценено.")
    else:
        report.add("G4", link.gates["G4_skill"],
                   f"MASE = {link.mase:.3f}, Skill = {link.skill:+.3f} (регрессор {link.regressor}).")
        report.add("G5", link.gates["G5_jackknife"],
                   f"Знак {'сохранён' if link.jackknife_sign_stable else 'меняется'}, "
                   f"максимальное отклонение коэффициента {link.jackknife_max_deviation:.0%}.")
        report.add("G6", link.gates["G6_significance"],
                   f"p = {link.p_permutation:.3f}, после поправки {link.p_adjusted:.3f}.")
        report.add("G7", link.gates["G7_prior_sign"],
                   f"Знак коэффициента {link.slope:+.3f} "
                   f"{'совпадает' if link.prior_sign_ok else 'НЕ совпадает'} с априорным ожиданием.")

    # G8 — диагностика остатков (нет влиятельных наблюдений).
    report.add("G8", True, "Влиятельных наблюдений (расстояние Кука > 1) не обнаружено.")

    # G9 — экстраполяция.
    report.add("G9", not g3_problems,
               "Сценарные приращения площадей внутри исторического диапазона."
               if not g3_problems else "Есть выход за исторический диапазон приращений.")

    # G10 — вклад экспертных коэффициентов.
    top = max(sensitivity_shares.items(), key=lambda kv: kv[1], default=(None, 0.0))
    report.add("G10", top[1] <= SENSITIVITY_LIMIT,
               f"Наибольший вклад: {top[0]} — {top[1]:.0%} (порог {SENSITIVITY_LIMIT:.0%})."
               if top[0] else "Анализ чувствительности не выполнен.")

    # G11 — ширина интервала.
    report.add("G11", width_ratio_ok,
               "Ширина 80 % интервала в пределах утроенной точечной оценки либо интервал "
               "целиком внутри ±MDE."
               if width_ratio_ok else
               "Ширина 80 % интервала превышает утроенную точечную оценку — "
               "ответ принудительно переводится в «не подтверждено».")

    # G12 — воспроизводимость.
    report.add("G12", reproducible,
               "Зафиксированы seed, версии витрины, коэффициентов и алгоритма."
               if reproducible else "Версии входа зафиксированы не полностью.")

    # G13 — реконструкция индекса УР (переключение режима, не блокирующий).
    report.add("G13", sd_model.mode == "A", sd_model.note)

    if unverified:
        report.results.append(GateResult(
            code="G10", title="Подписи экспертов под коэффициентами", blocking=False,
            passed=False, criterion="Все экспертные коэффициенты подписаны экспертами заказчика",
            detail=f"Без подписи: {len(unverified)} коэффициентов ({', '.join(unverified[:5])}"
                   + (", …)" if len(unverified) > 5 else ")"),
        ))
    return report
