# KZ InfoSec Audit — Applied Fixes
**Date:** 2026-10-20
**Scope:** DSS (North Kazakhstan oblast) — 3 subsystems
**Status:** All critical, high, and medium findings fixed (C-1, C-2, H-1, H-2, H-3, M-1, M-2, M-3, M-4)

---

## Files changed

### C-1 — ETL API key empty-value bypass
**`dss-etl-sko-main/app/main.py`**
- `API_KEY` now read via `os.environ.get` (returns `None` instead of crashing)
- Startup guard: `RuntimeError` if `API_SECRET_KEY` is missing or empty
- `verify_api_key` now rejects empty header values explicitly

### C-2 — BGD GIS no auth + default S3 creds
**`BGD_Landscape/gis/internal/config/config.go`**
- New `APIKey` field in `HTTPConfig` (`API_KEY` env var)

**`BGD_Landscape/gis/internal/transport/http/router.go`**
- New `requireAPIKey` middleware using `crypto/subtle.ConstantTimeCompare`
- Applied to all domain routes; `/healthz`, `/readyz`, `/docs`, `/openapi.yaml` exempt
- When `API_KEY` is empty, auth is disabled (local dev only)

**`BGD_Landscape/gis/internal/app/app.go`**
- `APIKey: cfg.HTTP.APIKey` wired into router deps

**`BGD_Landscape/gis/configs/.env.example`**
- `minioadmin` defaults replaced with `change_me_strong_access_key`
- New `API_KEY=` placeholder with comment

**`BGD_Landscape/gis/.env.example`**
- Same as above

**`BGD_Landscape/gis/deployments/docker-compose.yml`**
- `S3_ACCESS_KEY`/`S3_SECRET_KEY` → `${S3_ACCESS_KEY:-change_me_strong_access_key}`
- `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` → env-var driven with rotation warning
- `RABBITMQ_DEFAULT_USER`/`RABBITMQ_DEFAULT_PASS` → env-var driven with rotation warning
- New `API_KEY: ${API_KEY:-}` in app-common environment block

### H-1 — ECP legacy mode allowed in production
**`DSS/backend/app/config.py`**
- `_forbid_insecure_secrets_in_prod` validator now also rejects `ECP_VERIFY_MODE != "ncanode"` when `ENVIRONMENT=production`
- Clear error message pointing at `ECP_VERIFY_MODE` and `NCANODE_URL`

**`DSS/.env.example`**
- New ECP/NCANode block: `ECP_VERIFY_MODE`, `NCANODE_URL`, `NCANODE_TIMEOUT`, `NCANODE_REVOCATION_CHECK`, `ECP_NONCE_TTL_SECONDS`

### H-2 / H-3 — Frontend XSS (innerHTML + dangerouslySetInnerHTML)
**`DSS/frontend/src/lib/sanitize.ts`** (new)
- `sanitizeHtml()` — DOMPurify for markdown-rendered HTML
- `sanitizeSvg()` — DOMPurify with SVG element/attribute allowlist

**`DSS/frontend/src/app/dashboard/data-intake/protocol/page.tsx`**
- `dangerouslySetInnerHTML={{ __html: resp.html }}` → `sanitizeHtml(resp.html)`

**`DSS/frontend/src/components/dashboard/IndicatorView.tsx`**
- `el.innerHTML = donutSVG(...)` → `el.innerHTML = sanitizeSvg(donutSVG(...))`

**`DSS/frontend/src/app/dashboard/direction-map/page.tsx`**
- `el.innerHTML = svgHtml` → `el.innerHTML = sanitizeSvg(svgHtml)`

**`DSS/frontend/package.json`**
- Added `"dompurify": "^3.2.4"` to dependencies

---

### M-3 — Airflow / DB credential fallbacks (12 files)
**Airflow DAGs (8):** `dag_extract_taldau.py`, `dag_build_excel.py`, `dag_gdrive_sync.py`, `dag_extract_historical.py`, `dag_extract_weather.py`, `dag_auto_convert.py`, `dag_stat_gov_scraper.py` + **ETL seed** `load_station_climate_fact.py`
- `DB_CONN = os.environ.get("REPORTS_DB_URL", "postgresql+psycopg2://aldis:***@postgres/reports_db")` → `os.environ["REPORTS_DB_URL"]` (fail-fast)

**Standalone ETL scripts (3):** `load_to_raw_historical.py`, `convert_to_dss_universal.py`, `convert_to_dss.py`
- `DB_CONN = os.environ.get("REPORTS_DB_DSN", "host=localhost ... user=aldis password=***")` → `os.environ["REPORTS_DB_DSN"]`

**DSS intake script (1):** `DSS/scripts/intake_remote_reports.py`
- `"password": os.environ.get("REMOTE_PGPASSWORD", "CHANGE_ME")` → `os.environ["REMOTE_PGPASSWORD"]`

**Not changed (intentional):** test files, `seed_dev.py`, `docker-compose.dev.yml` — legitimate dev/test credentials

---

### M-4 — ETL SSO login rate limiting
**`dss-etl-sko-main/app/routers/dss_login.py`**
- In-memory per-IP rate limiter: 5 failed attempts per 15 minutes, then HTTP 429
- Client IP extracted from `X-Forwarded-For` (Caddy rewrites it to the real client address)
- Failure counted when DSS backend returns non-200; cleared on successful login
- Russian-language error message shown to the user on 429
- No new dependency (in-memory dict, reset on process restart — acceptable for a browser login page)

---

## Remaining (low priority, not yet fixed)
- **L-1** BGD GIS MinIO default creds in config template (addressed in C-2)
- **L-2** `seed_dev.py` test password `Test2026` (dev script only)
- **L-3** Airflow Fernet key not verified to be set (check `AIRFLOW__CORE__FERNET_KEY` in .env)

## Deployment checklist

### Before rebuilding
```bash
# Frontend (H-2/H-3)
cd DSS/frontend && npm install   # pulls in dompurify

# BGD GIS (C-2) — set a strong API key in your .env
# API_KEY=***
# and ensure S3/MinIO/RabbitMQ creds are strong non-defaults

# DSS backend (H-1) — ensure .env has:
# ECP_VERIFY_MODE=ncanode
# NCANODE_URL=http://ncanode:14579
```

### What to verify after deploy
1. BGD GIS: request any `/datasets` endpoint without `X-API-Key` header → expect 401
2. DSS backend: start with `ENVIRONMENT=production ECP_VERIFY_MODE=legacy` → expect startup crash
3. Protocol page: load in browser → confirm content renders normally (sanitizer preserves markdown HTML)
4. Map dashboards: load → confirm donut/pie markers render (sanitizer preserves SVG)
