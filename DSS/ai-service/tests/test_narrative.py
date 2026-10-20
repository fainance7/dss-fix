"""Тесты сборки нарратива и инвалидации кэша по отпечатку входа (T5)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _prompt(geo="Контекст: район А — 100"):
    from app.services.narrative import build_user_prompt

    return build_user_prompt("Оптимальный", "optimal", 2030, {"investment_multiplier": 1.5}, geo)


def test_user_prompt_contains_scenario_and_context():
    text = _prompt()
    assert "Оптимальный" in text
    assert "optimal" in text
    assert "2030" in text
    assert "investment_multiplier" in text
    assert "район А" in text


def test_input_hash_is_stable_and_sensitive():
    """Одинаковый вход → один хэш; изменение данных или модели → другой."""
    from app.services.narrative import input_hash

    base = input_hash(_prompt(), model="dss-narrative")
    assert base == input_hash(_prompt(), model="dss-narrative")
    assert base != input_hash(_prompt("Контекст: район А — 200"), model="dss-narrative")
    assert base != input_hash(_prompt(), model="qwen2.5:3b-instruct-q4_K_M")


@pytest.mark.asyncio
async def test_generate_sections_assembles_markdown():
    from app.services import narrative

    with patch.object(narrative, "chat_completion", AsyncMock(side_effect=["А", "Б", "В"])):
        text = await narrative.generate_sections("промпт")

    assert text.count("##") == 3
    assert text.startswith("## 1. Текущая ситуация")
    assert "## 3. Рекомендации" in text
    assert text.endswith("В")


@pytest.mark.asyncio
async def test_generate_sections_reports_progress():
    from app.services import narrative

    seen: list[tuple[int, int]] = []
    with patch.object(narrative, "chat_completion", AsyncMock(return_value="текст")):
        await narrative.generate_sections(
            "промпт", on_section=lambda i, total, el: seen.append((i, total)),
        )

    assert seen == [(1, 3), (2, 3), (3, 3)]


def _scenario_with(entry: dict):
    s = MagicMock()
    s.narratives = {"climate": entry}
    return s


def test_cache_considered_fresh_when_hash_matches():
    from app.api.v1 import scenarios
    from app.services.narrative import input_hash

    prompt = _prompt()
    s = _scenario_with({"status": "done", "text": "готово", "input_hash": input_hash(prompt)})
    assert scenarios._is_fresh(s, "climate", prompt) is True


def test_cache_stale_when_data_changed():
    """Данные показателя обновились → отпечаток разошёлся → текст устарел."""
    from app.api.v1 import scenarios
    from app.services.narrative import input_hash

    s = _scenario_with({"status": "done", "text": "старое", "input_hash": input_hash(_prompt())})
    assert scenarios._is_fresh(s, "climate", _prompt("Контекст: район А — 999")) is False


def test_legacy_entry_without_hash_is_fresh():
    """Записи, сделанные до введения отпечатка, не перегенерируются лавиной."""
    from app.api.v1 import scenarios

    s = _scenario_with({"status": "done", "text": "старое"})
    assert scenarios._is_fresh(s, "climate", _prompt()) is True


def test_landscape_block_in_prompt_not_in_params_json():
    """Вариант ЛП идёт в промпт отдельным текстом с оговоркой, а не сырым JSON."""
    from app.services.narrative import build_user_prompt
    snapshot = {
        "variant": "lp-v3-development", "label": "V3 — хозяйственно-ориентированный",
        "legend": [{"value": 4, "label": "пашенное земледелие"}, {"value": 5, "label": "пастбищное использование"}],
        "oblast_pct": {"4": 74.2, "5": 8.1},
        "districts": [{"name": "Акжарский район", "pct": {"4": 65.0, "5": 25.0}}],
    }
    prompt = build_user_prompt("Оптимальный", "optimal", 2030, {"kpis": {"a": 1}, "landscape": snapshot}, "ГЕО")
    assert "V3 — хозяйственно-ориентированный" in prompt
    assert "пашенное земледелие 74 %" in prompt
    assert "Акжарский район: пашенное земледелие 65 %, пастбищное использование 25 %" in prompt
    assert "не приписывайте варианту численный эффект" in prompt
    json_part = prompt.split("```json")[1].split("```")[0]
    assert "landscape" not in json_part and '"kpis"' in json_part


def test_prompt_without_landscape_unchanged_shape():
    from app.services.narrative import build_user_prompt, render_landscape_text
    assert render_landscape_text(None) == ""
    prompt = build_user_prompt("Стабильный", "stable", 2030, {"kpis": {}}, "ГЕО")
    assert "Вариант ландшафтного планирования" not in prompt
    assert prompt.endswith("ГЕО")
