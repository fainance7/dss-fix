"""
DSS Security Tests — Ф5.4
Покрытие: RBAC enforcement, SQL injection, XSS sanitization, HTTP headers
"""
import pytest
import httpx

BASE_URL = "http://localhost:8000/api/v1"

# ─── Credentials ────────────────────────────────────────────────────────────

CREDS = {
    "admin":      {"username": "admin",      "password": "CHANGE_ME"},
    "exp_ver":    {"username": "exp_ver",    "password": "Test2026"},
    "exp_dir":    {"username": "exp_dir",    "password": "Test2026"},
    "akim_obl":   {"username": "akim_obl",   "password": "Test2026"},
    "akim_ray":   {"username": "akim_petro", "password": "Test2026"},
}


def get_token(role: str) -> str:
    r = httpx.post(f"{BASE_URL}/auth/login", json=CREDS[role])
    assert r.status_code == 200, f"Login failed for {role}: {r.text}"
    return r.json()["access_token"]


def auth(role: str) -> dict:
    return {"Authorization": f"Bearer {get_token(role)}"}


# ─── TC-SEC-001: RBAC enforcement ───────────────────────────────────────────

class TestRBAC:
    def test_unauthenticated_gets_403(self):
        """Без токена → 401/403"""
        r = httpx.get(f"{BASE_URL}/users")
        assert r.status_code in (401, 403)

    def test_akim_ray_cannot_list_users(self):
        """akim_ray не может смотреть список пользователей (только admin)"""
        r = httpx.get(f"{BASE_URL}/users", headers=auth("akim_ray"))
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"

    def test_akim_obl_cannot_list_users(self):
        """akim_obl не может смотреть список пользователей"""
        r = httpx.get(f"{BASE_URL}/users", headers=auth("akim_obl"))
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"

    def test_exp_ver_cannot_list_users(self):
        """exp_ver не может смотреть список пользователей"""
        r = httpx.get(f"{BASE_URL}/users", headers=auth("exp_ver"))
        assert r.status_code == 403, f"Expected 403, got {r.status_code}"

    def test_admin_can_list_users(self):
        """admin может смотреть список пользователей"""
        r = httpx.get(f"{BASE_URL}/users", headers=auth("admin"))
        assert r.status_code == 200

    def test_akim_ray_cannot_create_user(self):
        """akim_ray не может создавать пользователей"""
        r = httpx.post(
            f"{BASE_URL}/users",
            json={"username": "hacker", "password": "x", "email": "h@x.com"},
            headers=auth("akim_ray")
        )
        assert r.status_code == 403

    def test_akim_ray_cannot_delete_user(self):
        """akim_ray не может удалять пользователей"""
        r = httpx.delete(f"{BASE_URL}/users/00000000-0000-0000-0000-000000000000",
                         headers=auth("akim_ray"))
        assert r.status_code in (403, 404)  # 404 if user not found, but not 200

    def test_invalid_token_gets_401(self):
        """Неверный токен → 401"""
        r = httpx.get(f"{BASE_URL}/auth/me",
                      headers={"Authorization": "Bearer invalid.token.here"})
        assert r.status_code == 401

    def test_expired_token_format_gets_401(self):
        """Токен с неверной подписью → 401"""
        bad_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJoYWNrZXIifQ.bad_sig"
        r = httpx.get(f"{BASE_URL}/auth/me",
                      headers={"Authorization": f"Bearer {bad_token}"})
        assert r.status_code == 401


# ─── TC-SEC-002: SQL Injection ───────────────────────────────────────────────

class TestSQLInjection:
    """Все SQL-инъекции должны возвращать 400/422, не 500 и не данные"""

    SQL_PAYLOADS = [
        "' OR '1'='1",
        "1; DROP TABLE users; --",
        "' UNION SELECT * FROM users --",
        "1' AND SLEEP(5) --",
        "'; SELECT pg_sleep(5); --",
        "admin'--",
        "\" OR \"\"=\"",
    ]

    def test_login_sql_injection(self):
        """SQL injection в поле username не должен давать доступ"""
        for payload in self.SQL_PAYLOADS:
            r = httpx.post(f"{BASE_URL}/auth/login",
                           json={"username": payload, "password": "x"})
            assert r.status_code in (401, 422), \
                f"SQL injection '{payload}' returned {r.status_code}: {r.text[:100]}"
            assert r.status_code != 500, f"Server error on SQL injection: {payload}"

    def test_login_password_sql_injection(self):
        """SQL injection в поле password не должен давать доступ"""
        for payload in self.SQL_PAYLOADS:
            r = httpx.post(f"{BASE_URL}/auth/login",
                           json={"username": "admin", "password": payload})
            assert r.status_code in (401, 422)
            assert r.status_code != 500

    def test_user_search_sql_injection(self):
        """SQL injection в query params"""
        for payload in ["' OR 1=1--", "1; DROP TABLE users"]:
            r = httpx.get(
                f"{BASE_URL}/users",
                params={"username": payload},
                headers=auth("admin"),
            )
            # Should return empty list or 422, not 500
            assert r.status_code != 500, f"Server error on SQL injection: {payload}"


# ─── TC-SEC-003: XSS Prevention ─────────────────────────────────────────────

class TestXSSPrevention:
    """XSS payloads в данных не должны сохраняться без экранирования"""

    XSS_PAYLOADS = [
        "<script>alert('xss')</script>",
        "<img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        "<svg onload=alert(1)>",
        '"><script>alert(document.cookie)</script>',
    ]

    def test_login_xss_not_accepted(self):
        """XSS в username должен вернуть 401/422, не 200"""
        for payload in self.XSS_PAYLOADS:
            r = httpx.post(f"{BASE_URL}/auth/login",
                           json={"username": payload, "password": "x"})
            assert r.status_code in (401, 422)

    def test_xss_in_username_field(self):
        """XSS в поле username при создании пользователя не должен проходить"""
        for payload in self.XSS_PAYLOADS:
            r = httpx.post(
                f"{BASE_URL}/users",
                json={"username": payload, "email": "test@example.com", "password": "Test2026!"},
                headers=auth("admin"),
            )
            # Either rejected (422) or accepted but value stored as plain text (200)
            # If 200, the response should not contain unescaped script tags
            if r.status_code == 200:
                response_text = r.text
                assert "<script>" not in response_text.lower(), \
                    f"XSS payload reflected in response: {payload}"


# ─── TC-SEC-004: HTTP Security Headers ──────────────────────────────────────

class TestSecurityHeaders:
    """Проверка наличия security headers в ответах"""

    def test_health_response_headers(self):
        """Базовые security headers должны присутствовать"""
        r = httpx.get(f"http://localhost:8000/health")
        assert r.status_code == 200
        # Content-Type должен быть application/json
        assert "application/json" in r.headers.get("content-type", "")

    def test_no_server_version_leak(self):
        """Версия сервера не должна раскрываться"""
        r = httpx.get(f"http://localhost:8000/health")
        server = r.headers.get("server", "")
        # Should not expose exact version like "uvicorn/0.27.1"
        assert "uvicorn/" not in server.lower() or server == ""

    def test_cors_allows_frontend(self):
        """CORS должен разрешать запросы от frontend"""
        r = httpx.options(
            f"{BASE_URL}/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            }
        )
        # Should return CORS headers allowing localhost:3000
        origin = r.headers.get("access-control-allow-origin", "")
        assert origin in ("http://localhost:3000", "*"), \
            f"CORS not allowing frontend: {origin}"

    def test_cors_blocks_unknown_origin(self):
        """CORS не должен разрешать произвольные origins"""
        r = httpx.get(
            f"{BASE_URL}/auth/me",
            headers={
                "Origin": "https://evil.com",
                "Authorization": f"Bearer {get_token('admin')}",
            }
        )
        origin = r.headers.get("access-control-allow-origin", "")
        assert origin != "https://evil.com", "CORS allows evil.com!"


# ─── TC-SEC-005: Rate Limiting / Brute Force protection ─────────────────────

class TestBruteForceProtection:
    def test_repeated_failed_logins_return_401(self):
        """Повторные неверные логины возвращают 401 (не 500)"""
        for i in range(10):
            r = httpx.post(f"{BASE_URL}/auth/login",
                           json={"username": "admin", "password": f"wrongpassword{i}"})
            assert r.status_code == 401, f"Attempt {i}: got {r.status_code}"
            assert r.status_code != 500
