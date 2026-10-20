from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://dss_user:dss_password@localhost:5434/dss_db"
    REDIS_URL: str = "redis://localhost:6379"
    BACKEND_URL: str = "http://localhost:8000"
    AI_SERVICE_PORT: int = 8001
    # Ключ для внутренней аутентификации между сервисами
    AI_SECRET_KEY: str = "dss_ai_service_key_2026"
    # Горизонт прогноза по умолчанию (ГОДЫ — ряды DSS годовые).
    FORECAST_HORIZON_YEARS: int = 5
    # Устаревший псевдоним; трактуется как число годовых шагов.
    FORECAST_HORIZON_MONTHS: int = 24
    # Синтетический ряд вместо реальных данных — только для локальных тестов.
    # На проде выключено: «прогноз» по выдуманному ряду неотличим от настоящего.
    ALLOW_SYNTHETIC_SERIES: bool = False

    # ── LLM (АВТОНОМНЫЙ inference, без внешних API) ──────────────
    # Любой OpenAI-совместимый сервер: Ollama, vLLM, llama.cpp.
    # В dev — Ollama на :11434 (см. docker-compose.yml service `ollama`).
    LLM_BASE_URL: str = "http://ollama:11434/v1"
    # 3B вместо 7B: на CPU (GPU на проде нет) 7B давал ~1 ток/с и полный отчёт не
    # укладывался в таймаут. 3B в ~2-3 раза быстрее, русский держит. При появлении
    # GPU можно вернуть 7B через env LLM_MODEL.
    LLM_MODEL: str = "qwen2.5:3b-instruct-q4_K_M"
    # Таймаут на ОДИН обычный вызов LLM (health, короткие вызовы).
    LLM_TIMEOUT_SECONDS: int = 1200
    # Таймаут read на ОДНУ секцию нарратива. На слабом CPU (без AVX) секция может
    # идти 15-20 мин — даём большой потолок, чтобы медленная, но ЖИВАЯ генерация не
    # падала в «LLM не ответила». Это лишь верхняя граница на случай зависания;
    # обычная генерация завершается раньше. Connect-таймаут остаётся коротким.
    LLM_NARRATIVE_READ_TIMEOUT_SECONDS: int = 5400  # 90 мин на секцию
    # Держать модель загруженной в память между запросами (иначе выгрузка через
    # 5 мин idle → холодная перезагрузка ~2-5 ГБ на каждый новый запрос).
    LLM_KEEP_ALIVE: str = "24h"
    # Бюджет токенов на ОДНУ секцию нарратива (их три). На CPU держали 350 —
    # иначе секция не укладывалась в таймаут и отчёт обрывался. На GPU лимит
    # поднимается через env (800 и выше), иначе текст выходит обрубком.
    LLM_SECTION_MAX_TOKENS: int = 350
    # Ключ OpenAI-совместимого сервера. vLLM, запущенный с --api-key, без
    # заголовка Authorization отвечает 401; Ollama ключ игнорирует.
    LLM_API_KEY: str | None = None
    # Доп. поля тела запроса (JSON). Специфика конкретной модели без правки кода:
    # для Qwen3 отключение режима рассуждений, иначе <think>…</think> попадает
    # прямо в нарратив:
    #   LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
    LLM_EXTRA_BODY: str | None = None
    # Резервный инференс на случай обрыва канала к GPU-серверу: локальная Ollama
    # с малой моделью. Медленно, но автономно и без внешних сервисов.
    LLM_FALLBACK_BASE_URL: str | None = None
    LLM_FALLBACK_MODEL: str | None = None
    LLM_FALLBACK_API_KEY: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _forbid_insecure_secret_in_prod(self):
        """Fail-fast (аудит безопасности, CR-4): в проде служебный ключ по умолчанию
        публично известен — с ним API Модуля ИИ доступен в обход backend."""
        if self.ENVIRONMENT == "production" and (
            self.AI_SECRET_KEY == "dss_ai_service_key_2026" or len(self.AI_SECRET_KEY) < 32
        ):
            raise ValueError("Небезопасный AI_SECRET_KEY в production: задайте значение >=32 символов в .env")
        return self


settings = Settings()
