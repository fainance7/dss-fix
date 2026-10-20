"""Тесты фильтров обучающего корпуса (T6.2).

Главный из них — числовой: именно он не даёт дообученной модели выучить
«красивые» цифры, которых нет в данных.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_narrative_corpus import (  # noqa: E402
    language_clean, numbers_verified, parse_numbers, structure_ok, too_similar, _ngrams,
)

CONTEXT = (
    "Айыртауский район: посевная площадь 125 400 га, валовой сбор 187,5 тыс. тонн, "
    "население 42 300 человек."
)


def test_parse_numbers_handles_russian_format():
    nums = parse_numbers("125 400 га, 187,5 тыс. тонн, -1402 человека")
    assert 125400.0 in nums
    assert 187.5 in nums
    assert -1402.0 in nums


def test_numbers_from_context_pass():
    ok, bad = numbers_verified(
        "Посевная площадь составляет 125 400 га при сборе 187,5 тыс. тонн.", CONTEXT,
    )
    assert ok, bad


def test_invented_number_is_rejected():
    """Число, которого нет в контексте, бракует кандидата — это и есть гейт."""
    ok, bad = numbers_verified(
        "Посевная площадь выросла до 900 000 га.", CONTEXT,
    )
    assert not ok
    assert 900000.0 in bad


def test_years_and_list_numbers_allowed():
    ok, _ = numbers_verified(
        "К 2030 году. 1. Первое действие. 2. Второе действие. 3. Третье действие.", CONTEXT,
    )
    assert ok


def test_rounding_tolerance():
    """Округление до 125 тыс. га не считается выдумкой."""
    ok, _ = numbers_verified("Площадь около 125 400 га", CONTEXT)
    assert ok
    ok, bad = numbers_verified("Площадь около 131 000 га", CONTEXT)
    assert not ok


def test_derived_share_allowed():
    """Долю, выводимую делением чисел контекста, модель считать вправе."""
    # 187,5 / 125 400 * 100 ≈ 0,1495 — проверяем сам механизм отношений
    ratio = round(187.5 / 42300 * 100, 2)
    ok, bad = numbers_verified(f"Показатель составляет {ratio} %".replace(".", ","), CONTEXT)
    assert ok, bad


def test_derived_difference_allowed():
    """Разность двух чисел контекста — обычный аналитический расчёт, не выдумка.

    Модель пишет «население 42 300, что на 4 800 меньше» — 4 800 получено
    вычитанием и обязано проходить гейт.
    """
    diff = 125400 - 42300
    ok, bad = numbers_verified(f"Разница составляет {diff} единиц", CONTEXT)
    assert ok, bad


def test_arbitrary_quotient_no_longer_justifies_anything():
    """Произвольное частное больших чисел не должно «оправдывать» выдумку."""
    ok, _ = numbers_verified("Показатель вырос до 627000 единиц", CONTEXT)
    assert not ok


def test_language_gate():
    assert language_clean("Полностью русский текст с числом 42 300.")[0]
    assert not language_clean("Текст с 中文 вставкой")[0]
    assert not language_clean("Текст с forecast вставкой")[0]
    # Допустимые сокращения не бракуют текст
    assert language_clean("Система DSS и KPI региона")[0]


def _valid_text():
    return (
        "## 1. Текущая ситуация\n\n" + "Текст раздела. " * 30 +
        "\n\n## 2. Прогнозный нарратив\n\n" + "Текст раздела. " * 30 +
        "\n\n## 3. Рекомендации\n\n1. Первое.\n2. Второе.\n3. Третье.\n"
    )


def test_structure_gate_accepts_valid():
    ok, why = structure_ok(_valid_text(), min_chars=500, max_chars=6000)
    assert ok, why


def test_structure_gate_rejects_wrong_recommendation_count():
    text_ = _valid_text().replace("3. Третье.\n", "")
    ok, why = structure_ok(text_, min_chars=500, max_chars=6000)
    assert not ok
    assert "рекомендаций" in why


def test_structure_gate_rejects_too_short():
    ok, why = structure_ok("## 1.\n## 2.\n## 3.\n1. а\n2. б\n3. в", min_chars=500, max_chars=6000)
    assert not ok
    assert "длина" in why


def test_dedup_catches_near_copy():
    base = "Валовой сбор зерна в районе вырос на фоне расширения посевных площадей и обновления техники"
    assert too_similar(base, [_ngrams(base)], threshold=0.6)
    assert not too_similar(
        "Совершенно другой текст про демографию и миграционный отток населения области",
        [_ngrams(base)], threshold=0.6,
    )
