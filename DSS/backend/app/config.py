from pydantic import model_validator
from pydantic_settings import BaseSettings

# Значения-плейсхолдеры, которые НЕЛЬЗЯ использовать в проде (fail-fast ниже).
_INSECURE_DEFAULTS = {
    "change_me_to_random_256bit_secret",
    "dss_ai_service_key_2026",
}


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    POSTGRES_DB: str = "dss_db"
    POSTGRES_USER: str = "dss_user"
    POSTGRES_PASSWORD: str = "password"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: str = "change_me_to_random_256bit_secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # H-1: токены браузера — в httpOnly-cookie (недоступны JS → XSS не крадёт их).
    # Cookie host-only (без Domain): фронт ходит в API через rewrite того же домена,
    # поэтому соседние сервисы *.dwh.kz cookie не получают.
    AUTH_COOKIE_ACCESS: str = "dss_access"
    AUTH_COOKIE_REFRESH: str = "dss_refresh"
    AUTH_COOKIE_REFRESH_PATH: str = "/api/v1/auth"

    # H-2: ограничение попыток входа (Redis; без Redis — счётчик в памяти процесса).
    LOGIN_MAX_FAILS: int = 5            # неудач подряд на учётную запись до блокировки
    LOGIN_LOCK_SECONDS: int = 900       # блокировка учётной записи и окно подсчёта, с
    LOGIN_IP_MAX_FAILS: int = 20        # неудачных входов с одного адреса за окно

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    CORS_ORIGINS: list[str] = [
        "https://dss.dwh.kz",
        "https://dssapi.dwh.kz",
        "https://dssai.dwh.kz",
    ]
    # L-5: адреса локальной разработки разрешаются только вне прода.
    CORS_DEV_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3010"]

    @property
    def cors_origins(self) -> list[str]:
        return self.CORS_ORIGINS if self.is_production else self.CORS_ORIGINS + self.CORS_DEV_ORIGINS

    MAPS_DIR: str = "/maps"

    # Подсистема геоданных (смежная команда). Реальный сервис: https://dssgis.dwh.kz.
    # Дефолт — локальный мок для офлайн-разработки; в .env задаётся реальный адрес.
    GIS_SERVICE_URL: str = "http://localhost:8100"
    GIS_SERVICE_TIMEOUT: float = 30.0
    # Ключ X-API-Key для GIS-сервиса (аудит C-2): GIS отклоняет запросы без ключа.
    # Должен совпадать с API_KEY в окружении GIS. В production обязателен.
    GIS_API_KEY: str = ""

    @property
    def gis_headers(self) -> dict[str, str]:
        """Заголовки для всех запросов backend → GIS."""
        return {"X-API-Key": self.GIS_API_KEY} if self.GIS_API_KEY else {}

    # TiTiler для растровых тайлов (COG). Backend проксирует /api/v1/gis/raster/* сюда.
    # COG_DIR — общий том cog_data (backend заливает, titiler читает).
    TITILER_URL: str = "http://dss-titiler:8000"
    COG_DIR: str = "/data/cogs"

    REPORTS_PGHOST: str = "dss-reports-db"
    REPORTS_PGPORT: int = 5432
    REPORTS_PGUSER: str = "postgres"
    REPORTS_PGPASSWORD: str = ""  # CR-3: только из .env, не хардкодить (утечка в git)
    REPORTS_PGDATABASE: str = "reports_db"

    # AI-микросервис (ai-service). Фронт ходит на наш бэкенд (/api/v1/ai/*),
    # бэкенд инжектит X-AI-Key серверно и форвардит на ai-service, проверяя RBAC
    # по направлениям. Ключ больше не светится в браузере. Нарратив теперь
    # асинхронный (start/poll — мгновенные), долгий LLM-инференс ушёл в фон
    # ai-service (LLM_TIMEOUT_SECONDS), поэтому прокси-таймаут вернули к 180с.
    AI_SERVICE_URL: str = "http://localhost:8001"
    AI_SECRET_KEY: str = "dss_ai_service_key_2026"
    AI_SERVICE_TIMEOUT: float = 180.0

    # ЭЦП-верификация (CR-2). NCANode — сервис проверки CMS-подписи НУЦ РК (вкл. ГОСТ):
    # криптопроверка подписи + цепочка до корня + отзыв (OCSP/CRL) + извлечение ИИН.
    # ECP_VERIFY_MODE: "ncanode" — безопасная проверка; "legacy" — старый разбор без
    # проверки подписи (небезопасно, только dev/переходный период).
    ECP_VERIFY_MODE: str = "legacy"
    NCANODE_URL: str = "http://ncanode:14579"
    NCANODE_TIMEOUT: float = 15.0
    NCANODE_REVOCATION_CHECK: str = "OCSP"  # CSV из OCSP,CRL; пусто = без проверки отзыва
    ECP_NONCE_TTL_SECONDS: int = 300

    # Prometheus сервера (node-exporter, docker-stats-exporter) — загрузка ЦП, памяти,
    # диска и контейнеров для страницы мониторинга (ФFE.7). Пусто — раздел скрыт.
    PROMETHEUS_URL: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    @model_validator(mode="after")
    def _forbid_insecure_secrets_in_prod(self):
        """Fail-fast: в проде запрещены дефолтные секреты (JWT/AI) — иначе форж
        токенов известным ключом. Падаем на старте, а не тихо работаем небезопасно."""
        if self.ENVIRONMENT == "production":
            bad = [
                name for name, val in (
                    ("JWT_SECRET_KEY", self.JWT_SECRET_KEY),
                    ("AI_SECRET_KEY", self.AI_SECRET_KEY),
                )
                if val in _INSECURE_DEFAULTS or len(val) < 32
            ]
            if bad:
                raise ValueError(
                    "Небезопасные секреты в production: "
                    + ", ".join(bad)
                    + ". Задайте сильные значения в .env (>=32 символов)."
                )
            # H-1: ЭЦП в режиме legacy — ИИН извлекается из недоверенного содержимого
            # CMS без криптопроверки подписи. В проде только ncanode: NCANode проверяет
            # подпись, цепочку до корня НУЦ РК и отзыв (OCSP/CRL). Падаем на старте.
            # C-2: GIS требует X-API-Key; без ключа backend получит 401 на всех
            # запросах к геоданным — либо GIS оставят открытым. Падаем на старте.
            if len(self.GIS_API_KEY) < 32:
                raise ValueError(
                    "GIS_API_KEY не задан или короче 32 символов в production. "
                    "Задайте в .env то же значение, что API_KEY у GIS-сервиса."
                )
            if self.ECP_VERIFY_MODE != "ncanode":
                raise ValueError(
                    "ECP_VERIFY_MODE=legacy запрещён в production. Задайте "
                    "ECP_VERIFY_MODE=ncanode в .env и убедитесь, что сервис NCANode "
                    "доступен (NCANODE_URL). Legacy-режим допускается только в dev."
                )
        return self


settings = Settings()
