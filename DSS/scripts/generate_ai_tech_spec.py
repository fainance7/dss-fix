"""
Генератор технической спецификации Модуля ИИ DSS.

Результат: docs/Техническая_спецификация_Модуль_ИИ_DSS.docx
Запуск:    backend/.venv/bin/python scripts/generate_ai_tech_spec.py
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "Техническая_спецификация_Модуль_ИИ_DSS.docx"


def add_para(doc, text, style="Normal", bold=False):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    if bold:
        run.bold = True


def add_bullets(doc, items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def add_code(doc, text):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.name = "Consolas"
    r.font.size = Pt(9.5)


def add_table(doc, header, rows, col_widths_cm=None):
    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    for i, h in enumerate(header):
        hdr[i].text = h
        for run in hdr[i].paragraphs[0].runs:
            run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = str(value)
    if col_widths_cm:
        for row in table.rows:
            for i, w in enumerate(col_widths_cm):
                row.cells[i].width = Cm(w)
    doc.add_paragraph()


def make_title_page(doc):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(70)
    r = p.add_run("ТЕХНИЧЕСКАЯ СПЕЦИФИКАЦИЯ\nМодуль искусственного интеллекта (ai-service)\n")
    r.bold = True
    r.font.size = Pt(20)

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run(
        "Система Поддержки Принятия Решений (DSS)\n"
        "для устойчивого развития Северо-Казахстанской области"
    )
    r2.font.size = Pt(13)

    doc.add_paragraph()
    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p3.add_run(
        "\nЗаказчик: КНАИУ\nИсполнитель: ТОО GRAPH\n"
        "Дата: 16 июля 2026\nВерсия документа: 1.0"
    ).font.size = Pt(12)

    p5 = doc.add_paragraph()
    p5.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p5.paragraph_format.space_before = Pt(130)
    p5.add_run("Алматы 2026").font.size = Pt(11)
    doc.add_page_break()


def make_toc(doc):
    doc.add_heading("Содержание", level=1)
    items = [
        "1. Назначение и область применения",
        "2. Термины и сокращения",
        "3. Архитектура и компоненты",
        "4. Технологический стек и зависимости",
        "5. Модель данных",
        "6. Спецификация прогнозных моделей",
        "7. Методика сценарного анализа",
        "8. Объяснимость (XAI)",
        "9. Автономный LLM-нарратив",
        "10. Спецификация REST API",
        "11. Планировщик регулярных расчётов",
        "12. Конфигурация (переменные окружения)",
        "13. Развёртывание и требования к среде",
        "14. Безопасность",
        "15. Нефункциональные характеристики",
    ]
    for it in items:
        doc.add_paragraph(it, style="List Bullet")
    doc.add_page_break()


def s1(doc):
    doc.add_heading("1. Назначение и область применения", level=1)
    add_para(
        doc,
        "Модуль ИИ (ai-service) — микросервис Системы поддержки принятия решений (DSS), "
        "выполняющий: прогнозирование показателей развития региона, генерацию расчётных "
        "показателей, формирование сравнительных сценариев развития с управляющими "
        "параметрами, объяснение результатов (Explainable AI) и генерацию текстовых "
        "аналитических заключений при помощи автономной локальной языковой модели.",
    )
    add_para(
        doc,
        "Документ описывает архитектуру, модель данных, алгоритмы, программные "
        "интерфейсы (API), конфигурацию и порядок развёртывания Модуля ИИ. Спецификация "
        "предназначена для разработчиков, интеграторов и администраторов Системы.",
    )
    add_para(
        doc,
        "Область применения — MVP для Северо-Казахстанской области (СКО): 12 районов и "
        "областной центр г. Петропавловск. Архитектура допускает тиражирование на "
        "другие области без изменений.",
    )


def s2(doc):
    doc.add_heading("2. Термины и сокращения", level=1)
    add_table(
        doc,
        ["Термин", "Определение"],
        [
            ("DSS", "Система поддержки принятия решений (Decision Support System)."),
            ("ai-service", "Микросервис Модуля ИИ (FastAPI, порт 8001)."),
            ("KPI", "Итоговый показатель сценария (Key Performance Indicator)."),
            ("XAI", "Объяснимый искусственный интеллект (Explainable AI)."),
            ("LLM", "Большая языковая модель (Large Language Model)."),
            ("MAPE", "Средняя абсолютная процентная ошибка прогноза."),
            ("RBAC", "Ролевая модель доступа (Role-Based Access Control)."),
            ("Направление", "Тематическая область показателей (АПК, экология, вода и т.д.)."),
            ("Рычаг влияния", "Управляющий параметр сценария (инвестиции, инфраструктура и т.п.)."),
        ],
        col_widths_cm=[3.5, 12.5],
    )


def s3(doc):
    doc.add_heading("3. Архитектура и компоненты", level=1)
    add_para(
        doc,
        "Модуль ИИ развёрнут отдельным контейнером dss-ai-service и опубликован за "
        "reverse-proxy на поддомене https://dssai.dwh.kz. Фронтенд обращается к нему "
        "только через ядро (dss-backend), которое аутентифицирует пользователя, "
        "проверяет права и серверно подставляет внутренний ключ доступа.",
    )
    add_code(
        doc,
        "[Frontend] → [dss-backend /api/v1/ai] → [dss-ai-service :8001]\n"
        "                       │                        │\n"
        "                       │                        ├── PostgreSQL (dss_db)\n"
        "                       │                        ├── Redis\n"
        "                       │                        ├── [защищённый канал] → GPU-узел: vLLM (основной LLM)\n"
        "                       │                        └── Ollama :11434 (резервный LLM, автономный)\n"
        "              (JWT + X-AI-Key + RBAC)      (statsmodels / scikit-learn / APScheduler)\n"
        "\n"
        "GPU-узел (RTX PRO 6000, 96 ГБ VRAM):\n"
        "  vLLM :8000 (только loopback) ← обучение моделей, предгенерация нарративов",
    )
    add_para(doc, "Функциональные компоненты сервиса:")
    add_table(
        doc,
        ["Компонент", "Ответственность"],
        [
            ("forecasting", "Загрузка ряда, прогнозные модели, доверительные интервалы."),
            ("scenarios", "KPI, множители сценариев, рычаги влияния, сравнение."),
            ("xai", "Важность факторов, метрики качества, уверенность модели."),
            ("tasks", "Генерация и ведение задач района (RegionalTask)."),
            ("scheduler", "Регулярные прогнозы и пересчёты (APScheduler)."),
            ("llm_client", "Клиент автономной LLM, основной и резервный бэкенд."),
            ("narrative", "Промпты и посекционная сборка нарратива, отпечаток входа."),
            ("series", "Единая загрузка рядов с разделением уровней иерархии."),
            ("geo_context", "Гео-контекст района/направления для нарратива."),
            ("model_registry", "Версионирование моделей (активация/rollback)."),
        ],
        col_widths_cm=[4.0, 12.0],
    )


def s4(doc):
    doc.add_heading("4. Технологический стек и зависимости", level=1)
    add_table(
        doc,
        ["Назначение", "Технология / версия"],
        [
            ("Язык / рантайм", "Python 3.12"),
            ("Веб-фреймворк", "FastAPI 0.109, uvicorn 0.27 (1 воркер)"),
            ("ORM / драйвер БД", "SQLAlchemy 2.0 (async) + asyncpg 0.29"),
            ("Валидация схем", "Pydantic 2.6"),
            ("Прогноз (модели)", "statsmodels 0.14, numpy 1.26, pandas 2.2"),
            ("Объяснимость (XAI)", "scikit-learn 1.4"),
            ("Планировщик", "APScheduler 3.10 (TZ Asia/Almaty)"),
            ("Кэш / состояние", "Redis 7 (redis 5.0)"),
            ("HTTP-клиент", "httpx 0.26"),
            ("LLM-сервер (основной)", "vLLM 0.29 на GPU-узле (OpenAI-совместимый API)"),
            ("LLM-модель (основная)", "Qwen3-32B-FP8, лицензия Apache-2.0"),
            ("LLM-сервер (резервный)", "Ollama 0.4.7 на прод-узле, CPU"),
            ("LLM-модель (резервная)", "qwen2.5:3b-instruct-q4_K_M"),
            ("Ускоритель", "NVIDIA RTX PRO 6000 Blackwell, 96 ГБ VRAM, CUDA 13"),
            ("Тестирование", "pytest 8.0 + pytest-asyncio"),
        ],
        col_widths_cm=[5.0, 11.0],
    )


def s5(doc):
    doc.add_heading("5. Модель данных", level=1)
    add_para(
        doc,
        "Модуль ИИ использует общую БД dss_db (PostgreSQL 15). Таблицы блока ИИ:",
    )
    add_table(
        doc,
        ["Таблица", "Ключевые поля", "Назначение"],
        [
            ("ai_models", "name, version, model_type, indicator_id, category_id, "
                          "parameters(JSONB), metrics(JSONB), model_blob(BYTEA), status, trained_at",
             "Реестр прогнозных моделей и артефактов."),
            ("forecasts", "model_id, indicator_id, region_id, forecast_date, "
                          "target_date_from/to, result_values(JSONB), confidence_scores(JSONB), "
                          "xai_explanation(JSONB), status, triggered_by",
             "Результаты прогнозов + XAI."),
            ("development_scenarios", "name, scenario_type, target_year, region_id, "
                                      "parameters(JSONB), status, narrative, narratives(JSONB), "
                                      "narrative_status/error, approved_by/at, created_by",
             "Сценарии развития и нарративы по направлениям."),
            ("regional_tasks", "scenario_id, region_id, title, description, category, "
                               "target_metric, target_value, unit, deadline, status, "
                               "assigned_to, completion_report",
             "Задачи по районам, порождённые сценарием."),
            ("task_events", "task_id, event_type, old_status, new_status, comment, "
                            "author_id, author_name",
             "Хронология событий задач."),
            ("calculated_indicators", "name, formula_description, category_id, region_id, "
                                       "indicator_date, value, unit, parameters(JSONB)",
             "Производные (расчётные) показатели."),
            ("scenario_comparisons", "(создаётся миграцией 001)",
             "Резерв под материализацию сравнения сценариев."),
        ],
        col_widths_cm=[3.5, 8.5, 4.0],
    )
    add_para(
        doc,
        "Источники исходных данных: indicator_values (фактические значения показателей, "
        "используются только со статусом approved), indicators, directions, units, "
        "regions. Кросс-сервисные внешние ключи (users, regions, data_categories) "
        "заданы на уровне БД. CHECK-ограничения статусов: ai_models "
        "(draft/training/active/deprecated), scenario_type (optimal/stable/risk/custom), "
        "scenario status (draft/calculated/approved/rejected/archived), regional_tasks "
        "(pending/in_progress/completed/cancelled/accepted).",
    )


def s6(doc):
    doc.add_heading("6. Спецификация прогнозных моделей", level=1)
    add_para(doc, "Тип модели задаётся полем model_type. Реализованы три алгоритма:")
    add_table(
        doc,
        ["Тип", "Алгоритм / библиотека", "Параметры и поведение"],
        [
            ("linear", "numpy.polyfit (degree 1)",
             "Линейная экстраполяция. n=0 → нули; n=1 → константа. Используется как "
             "fallback для остальных моделей."),
            ("holt_winters", "statsmodels ExponentialSmoothing",
             "n ≥ 24: trend='add', seasonal='add', seasonal_periods=12; иначе trend='add', "
             "seasonal=None. При исключении → linear."),
            ("arima", "statsmodels ARIMA",
             "n < 4 → linear. Перебор (p,d,q) из {(1,1,1),(1,1,0),(0,1,1),(2,1,1),(1,0,0)}, "
             "выбор по минимуму AIC. При несходимости → linear."),
        ],
        col_widths_cm=[2.5, 5.0, 8.5],
    )
    add_para(doc, "Подготовка ряда (get_historical_series):")
    add_bullets(
        doc,
        [
            "Источник — indicator_values по indicator_id (только approved), сортировка по "
            "периоду; при областном срезе (region_id не задан) — среднее по районам.",
            "Требуется минимум 3 фактические точки, иначе расчёт отклоняется (ValueError).",
            "Для расчёта берутся последние 36 точек.",
        ],
    )
    add_para(doc, "Расчёт прогноза (run_forecast):")
    add_bullets(
        doc,
        [
            "Горизонт по умолчанию 24 месяца (диапазон 1–120).",
            "result_values — помесячный ряд {дата: значение}.",
            "confidence_scores — коридор {low, high}; граница = value ± |value| · "
            "(0.15 + 0.005·i), где i — шаг прогноза.",
            "К прогнозу прикрепляется xai_explanation (раздел 8).",
        ],
    )
    add_para(doc, "Обучение и бэктест:")
    add_bullets(
        doc,
        [
            "train.py — обучение одной модели; hold-out бэктест на последних "
            "min(max(3, n/4), 12) точках; метрики MAE / RMSE / R² / MAPE; минимум 6 точек; "
            "артефакт model_blob (конфиг + хвост ряда) для детерминированного реинференса.",
            "train_batch.py — пакетное обучение по направлениям (climate, labor, "
            "social_demog, economy); модели linear и Holt-Winters, лучшая по MAE → active; "
            "порог качества MAPE ≤ 15 %.",
        ],
    )


def s7(doc):
    doc.add_heading("7. Методика сценарного анализа", level=1)
    add_para(
        doc,
        "Формируются три типовых сценария (optimal, stable, risk) и пользовательский "
        "(custom). Итоговые KPI считаются по реальным показателям (АПК + демография):",
    )
    add_table(
        doc,
        ["KPI (key)", "Показатель", "Ед.", "optimal", "stable", "risk", "custom"],
        [
            ("agri_output", "Валовая продукция СХ", "млрд ₸", "1.30", "1.05", "0.85", "1.10"),
            ("natural_increase", "Естественный прирост", "чел.", "1.25", "1.00", "0.70", "1.05"),
            ("population", "Население (всего)", "чел.", "1.03", "1.00", "0.98", "1.01"),
            ("agri_investment", "Инвестиции в АПК", "млрд ₸", "1.50", "1.08", "0.75", "1.15"),
        ],
        col_widths_cm=[2.8, 3.8, 1.6, 1.6, 1.6, 1.6, 1.6],
    )
    add_para(
        doc,
        "База KPI = последнее фактическое областное значение показателя (при отсутствии "
        "областного ряда — агрегат по районам уровня 2), умноженное на множитель "
        "сценария. Затем применяются рычаги влияния пользователя:",
    )
    add_table(
        doc,
        ["Рычаг (параметр)", "Диапазон", "Формула влияния"],
        [
            ("investment_multiplier", "0.5–2.0", "agri_investment ×= investment_multiplier"),
            ("infrastructure_index", "0.5–2.0", "agri_output ×= infrastructure_index"),
            ("gdp_growth_target", "-0.1…0.2", "agri_output ×= (1 + (target − 0.03))"),
            ("population_growth_rate", "-0.05…0.05", "population ×= (1 + rate · 5)"),
            ("employment_target", "0.7–1.0", "резерв методики"),
        ],
        col_widths_cm=[4.5, 2.5, 9.0],
    )
    add_para(
        doc,
        "Сравнение сценариев выполняется эндпоинтом GET /scenarios/compare (расчёт "
        "различий по KPI на лету). Утверждённый сценарий (status=approved) порождает "
        "задачи по районам (раздел 10, tasks).",
    )


def s8(doc):
    doc.add_heading("8. Объяснимость (XAI)", level=1)
    add_para(
        doc,
        "Важность факторов вычисляется RandomForestRegressor (n_estimators=50, "
        "random_state=42) поверх нормированных (StandardScaler) признаков ряда. Набор "
        "признаков (FEATURE_NAMES):",
    )
    add_bullets(
        doc,
        [
            "year_normalized — нормированное время (тренд);",
            "lag_1 — значение предыдущего периода;",
            "lag_2 — значение позапрошлого периода;",
            "rolling_mean_3 — скользящее среднее за 3 периода;",
            "trend_slope — исторический наклон тренда.",
        ],
    )
    add_para(
        doc,
        "Важности нормируются (сумма = 1.0). Метрики качества: compute_model_metrics → "
        "{mae, rmse, r2, mape, mase}. Уверенность модели выводится из фактического "
        "качества на бэктесте (приоритет MASE, затем MAPE); при отсутствии метрик "
        "возвращается null и явная оговорка «уверенность не оценена» — вместо "
        "прежней формулы, дававшей одно и то же значение для любого прогноза. "
        "Итог — русскоязычная интерпретация и структура xai_explanation "
        "{feature_importance, confidence_interval, model_confidence, "
        "confidence_source, interpretation, meta}.",
    )


def s9(doc):
    doc.add_heading("9. Автономный LLM-нарратив", level=1)
    add_para(
        doc,
        "Текстовые аналитические заключения генерируются автономной LLM через "
        "OpenAI-совместимый Chat Completions. Внешние API не используются: основной "
        "инференс — vLLM на выделенном GPU-узле, доступный только по защищённому "
        "каналу, резервный — локальная Ollama на прод-узле. Готовые заключения "
        "кэшируются в базе вместе с отпечатком входных данных, поэтому открытие "
        "сценария не требует обращения к модели, а недоступность GPU-узла не "
        "останавливает работу системы.",
    )
    add_table(
        doc,
        ["Параметр", "Значение"],
        [
            ("Модель (основная)", "Qwen3-32B-FP8 на vLLM, GPU-узел"),
            ("Модель (резервная)", "qwen2.5:3b-instruct-q4_K_M на Ollama, прод-узел"),
            ("Температура", "0.3"),
            ("Токенов на секцию", "800 (LLM_SECTION_MAX_TOKENS)"),
            ("Секций нарратива", "3 (Текущая ситуация / Прогнозный нарратив / Рекомендации)"),
            ("Скорость генерации", "38,6 ток/с (замер 21.09.2026)"),
            ("Полный отчёт", "≈ 23 с против 30–45 мин на CPU"),
            ("Параллельные запросы", "8–16 (непрерывный батчинг vLLM)"),
            ("Кэш заключений", "по отпечатку входа (sha256 промпта, секций и модели)"),
            ("Язык вывода", "русский (жёсткая системная директива)"),
        ],
        col_widths_cm=[5.5, 10.5],
    )
    add_para(
        doc,
        "Генерация асинхронная, статус нарратива: idle / running / done / failed; "
        "результат сохраняется по каждому направлению (поле narratives). В промпт "
        "добавляется компактный гео-контекст (агрегаты по approved-показателям, "
        "перечень районов) без полной геометрии. При недоступности LLM эндпоинты "
        "возвращают 503. При рестарте сервиса «зависшие» нарративы сбрасываются.",
    )


def s10(doc):
    doc.add_heading("10. Спецификация REST API", level=1)
    add_para(
        doc,
        "Базовый префикс ai-service — /api/v1. Прикладные роутеры требуют внутренний "
        "заголовок X-AI-Key (подставляется ядром). Легитимный путь вызова — через "
        "прокси ядра /api/v1/ai/*.",
    )

    doc.add_heading("10.1. Прогнозы (/forecasts)", level=2)
    add_table(
        doc,
        ["Метод", "Путь", "Описание"],
        [
            ("POST", "/forecasts", "Создать и запустить прогноз (ForecastCreate)."),
            ("GET", "/forecasts", "Список (фильтры region_id, model_id, status; пагинация)."),
            ("GET", "/forecasts/{id}", "Прогноз по идентификатору."),
            ("GET", "/forecasts/{id}/chart-data", "Точки графика {date, value, low, high}."),
        ],
        col_widths_cm=[1.6, 6.4, 8.0],
    )

    doc.add_heading("10.2. Сценарии (/scenarios)", level=2)
    add_table(
        doc,
        ["Метод", "Путь", "Описание"],
        [
            ("POST", "/scenarios", "Создать сценарий."),
            ("POST", "/scenarios/generate", "Сгенерировать 3 сценария + задачи."),
            ("GET", "/scenarios", "Список (фильтры region_id, type, status)."),
            ("GET", "/scenarios/compare", "Сравнение сценариев по KPI."),
            ("GET", "/scenarios/{id}", "Сценарий по идентификатору."),
            ("POST", "/scenarios/{id}/calculate", "Пересчёт KPI сценария."),
            ("PATCH", "/scenarios/{id}/parameters", "Обновить рычаги влияния."),
            ("POST", "/scenarios/{id}/reject", "Отклонить сценарий."),
            ("POST", "/scenarios/{id}/narrative", "Запуск нарратива по направлению."),
            ("POST", "/scenarios/{id}/narrative/all", "Батч-нарратив по списку направлений."),
            ("GET", "/scenarios/{id}/narrative/status", "Статус генерации нарратива."),
        ],
        col_widths_cm=[1.6, 6.9, 7.5],
    )

    doc.add_heading("10.3. Модели (/models)", level=2)
    add_table(
        doc,
        ["Метод", "Путь", "Описание"],
        [
            ("GET", "/models", "Список моделей (фильтры type, status)."),
            ("GET", "/models/{id}", "Модель по идентификатору."),
            ("POST", "/models", "Регистрация модели (status=draft)."),
            ("PUT", "/models/{id}/activate", "Активация (прежние active → deprecated)."),
            ("PUT", "/models/{id}/deprecate", "Снятие модели с эксплуатации."),
            ("POST", "/models/{id}/rollback", "Откат к предыдущей модели типа."),
            ("PUT", "/models/{id}/metrics", "Сохранить метрики модели."),
        ],
        col_widths_cm=[1.6, 6.4, 8.0],
    )

    doc.add_heading("10.4. Задачи района (/tasks)", level=2)
    add_table(
        doc,
        ["Метод", "Путь", "Описание"],
        [
            ("GET", "/tasks", "Список задач (фильтр region_id)."),
            ("GET", "/tasks/{id}", "Задача по идентификатору."),
            ("POST", "/scenarios/{id}/tasks/generate", "Генерация задач по сценарию."),
            ("PATCH / PUT", "/tasks/{id} · /tasks/{id}/status", "Изменение задачи / статуса."),
            ("POST", "/tasks/{id}/comment", "Комментарий к задаче."),
            ("POST", "/tasks/{id}/accept", "Принять выполнение (область)."),
            ("POST", "/tasks/{id}/reject-completion", "Вернуть на доработку."),
            ("GET", "/tasks/{id}/events", "Хронология событий задачи."),
        ],
        col_widths_cm=[2.4, 6.6, 7.0],
    )

    doc.add_heading("10.5. Гео-контекст и интеграция", level=2)
    add_table(
        doc,
        ["Метод", "Путь", "Описание"],
        [
            ("GET", "/geo-context/{direction_code}/{region_id}", "Гео-контекст для нарратива."),
            ("POST", "/internal/forecast-request", "Прогноз по запросу ядра."),
            ("POST", "/internal/scenario-approve", "Утвердить сценарий + сгенерировать задачи."),
            ("GET", "/internal/region-summary/{region_id}", "Сводка по региону."),
            ("GET", "/health", "Проверка сервиса и статуса LLM."),
        ],
        col_widths_cm=[1.8, 7.2, 7.0],
    )


def s11(doc):
    doc.add_heading("11. Планировщик регулярных расчётов", level=1)
    add_table(
        doc,
        ["Задача", "Расписание (Asia/Almaty)", "Действие"],
        [
            ("run_daily_forecasts", "Ежедневно 02:00", "Прогноз по активным моделям × районам."),
            ("run_weekly_indicators", "Вс 03:00", "Пересчёт производных показателей (снимок + прирост г/г)."),
            ("run_scenario_refresh", "Сб 04:00", "Пересчёт черновиков сценариев старше 7 дней."),
        ],
        col_widths_cm=[4.5, 4.0, 7.5],
    )
    add_para(
        doc,
        "run_weekly_indicators защищает от смены единиц измерения: скачок значения "
        "более 500 % отбрасывается. Результаты пишутся в calculated_indicators.",
    )


def s12(doc):
    doc.add_heading("12. Конфигурация (переменные окружения)", level=1)
    add_table(
        doc,
        ["Переменная", "Назначение", "Значение по умолчанию"],
        [
            ("DATABASE_URL", "Подключение к БД (asyncpg)", "postgresql+asyncpg://…/dss_db"),
            ("REDIS_URL", "Подключение к Redis", "redis://localhost:6379"),
            ("BACKEND_URL", "Адрес ядра", "http://localhost:8000"),
            ("AI_SERVICE_PORT", "Порт сервиса", "8001"),
            ("AI_SECRET_KEY", "Внутренний ключ X-AI-Key", "(в prod — из окружения, fail-fast)"),
            ("FORECAST_HORIZON_MONTHS", "Горизонт прогноза", "24"),
            ("LLM_BASE_URL", "URL LLM-сервера", "http://ollama:11434/v1"),
            ("LLM_MODEL", "Имя модели", "qwen2.5:3b-instruct-q4_K_M"),
            ("LLM_TIMEOUT_SECONDS", "Тайм-аут вызова/health", "1200"),
            ("LLM_NARRATIVE_READ_TIMEOUT_SECONDS", "Тайм-аут секции нарратива", "5400"),
            ("LLM_KEEP_ALIVE", "Удержание модели в памяти", "24h"),
            ("LLM_SECTION_MAX_TOKENS", "Токенов на секцию", "350"),
            ("ENVIRONMENT", "Режим (production — Swagger off)", "development"),
        ],
        col_widths_cm=[5.5, 5.5, 5.0],
    )


def s13(doc):
    doc.add_heading("13. Развёртывание и требования к среде", level=1)
    add_para(doc, "Сервисы Docker Compose:")
    add_table(
        doc,
        ["Сервис", "Образ", "Порт", "Особенности prod"],
        [
            ("dss-ai-service", "сборка ai-service/Dockerfile.prod", "8001",
             "ENVIRONMENT=production, лимит памяти 1 ГБ, AI_SECRET_KEY из .env."),
            ("dss-ollama", "ollama/ollama:0.4.7", "11434",
             "volume ollama_data, keep_alive 24ч, OLLAMA_NUM_PARALLEL=1."),
            ("ollama-init", "ollama/ollama:0.4.7 (profile init)", "—",
             "Однократный ollama pull модели."),
        ],
        col_widths_cm=[3.2, 4.8, 1.5, 6.5],
    )
    add_para(doc, "Порядок первичного развёртывания:")
    doc.add_paragraph("Применить миграции БД (001, 004, 005, 009, 017, 018, 019, 026 и др.).", style="List Number")
    doc.add_paragraph("Поднять dss-ai-service и dss-ollama (docker compose up -d).", style="List Number")
    doc.add_paragraph("Загрузить модель: docker compose --profile init up ollama-init.", style="List Number")
    doc.add_paragraph("Обучить/активировать модели (train_batch.py) по наполненным данным.", style="List Number")
    doc.add_paragraph("Проверить GET /health (llm.ready=true при загруженной модели).", style="List Number")
    add_para(
        doc,
        "Требования к среде: интерактивная генерация заключений и обучение моделей "
        "выполняются на выделенном GPU-узле (NVIDIA RTX PRO 6000, 96 ГБ VRAM, "
        "процессор с поддержкой AVX-512). Прод-узел остаётся потребителем "
        "результатов: прогнозы, XAI и выдача сохранённых заключений работают на нём "
        "без ограничений, поэтому недоступность GPU-узла снижает интерактивность, "
        "но не останавливает систему.",
    )


def s14(doc):
    doc.add_heading("14. Безопасность", level=1)
    add_bullets(
        doc,
        [
            "Аутентификация: все прикладные роутеры требуют X-AI-Key; ядро добавляет "
            "ключ серверно, браузеру он не передаётся. Прямой доступ без ключа → 403.",
            "Авторизация: RBAC по тематическим направлениям (exp_dir), утверждение "
            "сценариев — только akim_obl, задачи района — скоуп akim_ray по своему району.",
            "Транспорт: HTTPS (Caddy + Let's Encrypt) на поддомене dssai.dwh.kz.",
            "Автономность: обращений к внешним LLM/облачным сервисам нет — суверенность "
            "данных.",
            "В production отключён Swagger (docs/redoc/openapi), включён fail-fast при "
            "отсутствии секрета.",
            "Аудит: значимые действия фиксируются журналом аудита ядра.",
        ],
    )


def s15(doc):
    doc.add_heading("15. Нефункциональные характеристики", level=1)
    add_table(
        doc,
        ["Характеристика", "Значение / реализация"],
        [
            ("Время отклика (прогноз/сценарий)", "Локальные расчёты, < 3 сек."),
            ("Время отклика (LLM-нарратив)", "Из кэша — мгновенно; новая генерация ≈ 23 с на GPU."),
            ("Отказоустойчивость", "Трёхуровневая деградация LLM: кэш → резервный "
                                    "инференс → явный отказ; fallback моделей на linear; "
                                    "сброс зависших нарративов при рестарте."),
            ("Масштабируемость", "Stateless-микросервис; узкое место (LLM) масштабируется GPU."),
            ("Наблюдаемость", "GET /health со статусом LLM; логи uvicorn."),
            ("Качество прогнозов", "Основной критерий MASE < 1 (лучше наивного прогноза); "
                                    "MAPE ≤ 15 % — вторичный. Показатели, не прошедшие "
                                    "критерий, помечаются как ненадёжные."),
            ("Доверительные интервалы", "Split-conformal по остаткам бэктеста; "
                                        "фактическое покрытие фиксируется в метриках."),
            ("Тестируемость", "pytest-asyncio: прогноз, сценарии, ряды, реестр моделей, "
                              "LLM-клиент, нарратив, фильтры корпуса."),
        ],
        col_widths_cm=[5.5, 10.5],
    )
    doc.add_paragraph()
    sign = doc.add_paragraph()
    r = sign.add_run("Директор ТОО «GRAPH»              _______________________                Р. Бейганов")
    r.bold = True


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    make_title_page(doc)
    make_toc(doc)
    for fn in (s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s12, s13, s14, s15):
        fn(doc)

    doc.save(OUTPUT)
    print(f"Saved: {OUTPUT}  ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
