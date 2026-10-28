"""
Исправления по аудиту безопасности (Этап 6): httpOnly-cookie и CSRF (H-1), ограничение
попыток входа (H-2), отзыв токенов (M-7), политика паролей (L-1), район в детальном
чтении пространственных данных (M-1), партии приёма (M-4), SSRF (M-2), очистка HTML
регламента (M-9), заголовки безопасности (L-4).
"""
import uuid

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from app.config import settings
from app.core.security import hash_password
from app.models.user import Region, User, UserDirection, UserRole

COOKIE = {"X-Auth-Mode": "cookie"}
ORIGIN_OK = {"Origin": "https://dss.dwh.kz"}


async def _cookie_login(client: AsyncClient, username="test_admin", password="AdminPass123!"):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password},
                             headers=COOKIE)


# ── H-1: cookie-сеанс ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cookie_login_sets_httponly_cookies_without_tokens_in_body(client, admin_user):
    resp = await _cookie_login(client)
    assert resp.status_code == 200
    assert "access_token" not in resp.json() and "refresh_token" not in resp.json()
    cookies = resp.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith(settings.AUTH_COOKIE_ACCESS + "="))
    refresh = next(c for c in cookies if c.startswith(settings.AUTH_COOKIE_REFRESH + "="))
    for c in (access, refresh):
        assert "HttpOnly" in c and "SameSite=strict" in c
    assert f"Path={settings.AUTH_COOKIE_REFRESH_PATH}" in refresh

    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "test_admin"


@pytest.mark.asyncio
async def test_script_login_still_returns_tokens(client, admin_user):
    resp = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "AdminPass123!"})
    assert resp.status_code == 200
    assert resp.json()["access_token"] and resp.json()["refresh_token"]
    assert not resp.headers.get_list("set-cookie")


@pytest.mark.asyncio
async def test_cookie_mutation_requires_own_origin(client, admin_user):
    await _cookie_login(client)
    body = {"username": "csrf_user", "email": "csrf@example.com", "password": "CsrfPass12345"}
    foreign = await client.post("/api/v1/users", json=body, headers={"Origin": "https://dssgit.dwh.kz"})
    assert foreign.status_code == 403
    cross = await client.post("/api/v1/users", json=body, headers={"Sec-Fetch-Site": "same-site"})
    assert cross.status_code == 403
    own = await client.post("/api/v1/users", json=body, headers=ORIGIN_OK)
    assert own.status_code == 201, own.text


@pytest.mark.asyncio
async def test_refresh_by_cookie_never_returns_token(client, admin_user):
    await _cookie_login(client)
    resp = await client.post("/api/v1/auth/refresh", headers=ORIGIN_OK)
    assert resp.status_code == 200
    assert "access_token" not in resp.json()
    assert any(c.startswith(settings.AUTH_COOKIE_ACCESS + "=") for c in resp.headers.get_list("set-cookie"))


# ── M-7: отзыв токенов ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_revokes_all_tokens(client, admin_user, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_password_change_revokes_old_tokens(client, admin_user):
    login = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "AdminPass123!"})
    old = {"Authorization": f"Bearer {login.json()['access_token']}"}
    refresh_old = login.json()["refresh_token"]
    resp = await client.post("/api/v1/auth/change-password", headers=old,
                             json={"current_password": "AdminPass123!", "new_password": "NewAdminPass2026"})
    assert resp.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=old)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_old})).status_code == 401
    relogin = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "NewAdminPass2026"})
    assert relogin.status_code == 200


@pytest.mark.asyncio
async def test_blocking_user_revokes_tokens(client, admin_token, regular_user):
    login = await client.post("/api/v1/auth/login", json={"username": "test_expert", "password": "ExpertPass123!"})
    expert = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = await client.patch(f"/api/v1/users/{regular_user.id}", json={"is_active": False},
                              headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=expert)).status_code == 401


# ── H-2: ограничение попыток входа ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_account_locked_after_failures_and_failures_audited(client, admin_user, admin_token):
    for _ in range(settings.LOGIN_MAX_FAILS):
        r = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "wrong-pass-1"})
        assert r.status_code == 401
    locked = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "AdminPass123!"})
    assert locked.status_code == 429
    assert locked.headers.get("retry-after")

    log = await client.get("/api/v1/audit-log?action=login_failed",
                           headers={"Authorization": f"Bearer {admin_token}"})
    assert log.status_code == 200
    assert log.json()["total"] >= settings.LOGIN_MAX_FAILS


@pytest.mark.asyncio
async def test_ip_locked_after_spraying_many_accounts(client, admin_user):
    for i in range(settings.LOGIN_IP_MAX_FAILS):
        await client.post("/api/v1/auth/login", json={"username": f"ghost_{i}", "password": "x"})
    r = await client.post("/api/v1/auth/login", json={"username": "test_admin", "password": "AdminPass123!"})
    assert r.status_code == 429


# ── L-1: политика паролей ─────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["Short1", "onlyletterspassword", "1234567890123", "weakuser_2026x"])
async def test_password_policy_on_user_create(client, admin_token, password):
    resp = await client.post(
        "/api/v1/users",
        json={"username": "weakuser", "email": "weak@example.com", "password": password},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


# ── M-1: район в детальном чтении пространственных данных ────────────────────

@pytest.mark.asyncio
async def test_akim_ray_cannot_read_foreign_spatial_object(client, admin_token, db, roles):
    from tests.test_data import _create_category, _create_layer, _create_source, _create_spatial

    src = await _create_source(client, admin_token)
    cat = await _create_category(client, admin_token)
    layer = await _create_layer(client, admin_token, cat["id"], src["id"])
    sd = await _create_spatial(client, admin_token, layer["id"])  # без района

    region = Region(code=f"R{uuid.uuid4().hex[:6]}", name=f"reg_{uuid.uuid4().hex[:6]}", level=2)
    db.add(region)
    await db.flush()
    ray = User(username="akim_ray_m1", email="ray_m1@example.com", password_hash=hash_password("RayPass12345"))
    db.add(ray)
    await db.flush()
    db.add(UserRole(user_id=ray.id, role_id=roles["akim_ray"].id, region_id=region.id))
    await db.commit()
    tok = (await client.post("/api/v1/auth/login",
                             json={"username": "akim_ray_m1", "password": "RayPass12345"})).json()["access_token"]

    assert (await client.get(f"/api/v1/spatial-data/{sd['id']}",
                             headers={"Authorization": f"Bearer {tok}"})).status_code == 404
    assert (await client.get(f"/api/v1/spatial-data/{sd['id']}",
                             headers={"Authorization": f"Bearer {admin_token}"})).status_code == 200


# ── M-4: партии приёма у эксперта с ограничением по направлениям ─────────────

@pytest.mark.asyncio
async def test_restricted_expert_sees_only_own_intake_jobs(client, admin_user, db, roles):
    from sqlalchemy import select

    from app.models.data_intake import DataIntakeJob
    from app.models.data import Direction

    direction = (await db.execute(select(Direction).limit(1))).scalar_one()
    exp = User(username="exp_dir_m4", email="exp_m4@example.com", password_hash=hash_password("ExpPass12345"))
    db.add(exp)
    await db.flush()
    db.add(UserRole(user_id=exp.id, role_id=roles["exp_dir"].id, region_id=None))
    db.add(UserDirection(user_id=exp.id, direction_id=direction.id))
    own = DataIntakeJob(source_kind="excel_template", source_ref="own.xlsx", uploaded_by=exp.id)
    foreign = DataIntakeJob(source_kind="excel_template", source_ref="foreign.xlsx", uploaded_by=admin_user.id)
    db.add_all([own, foreign])
    await db.commit()

    tok = (await client.post("/api/v1/auth/login",
                             json={"username": "exp_dir_m4", "password": "ExpPass12345"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    listed = (await client.get("/api/v1/data-intake/jobs", headers=h)).json()
    assert [j["id"] for j in listed["items"]] == [str(own.id)]
    assert (await client.get(f"/api/v1/data-intake/jobs/{foreign.id}", headers=h)).status_code == 404
    assert (await client.get(f"/api/v1/data-intake/jobs/{foreign.id}/quality", headers=h)).status_code == 404
    assert (await client.get(f"/api/v1/data-intake/jobs/{own.id}", headers=h)).status_code == 200


# ── M-2, M-9: проверка адреса источника и очистка HTML ───────────────────────

@pytest.mark.parametrize("url", ["http://example.com/api", "https://127.0.0.1/x", "https://10.0.0.5/",
                                 "https://169.254.169.254/latest", "ftp://example.com"])
@pytest.mark.asyncio
async def test_source_url_check_rejects_internal_and_plain(url):
    from app.api.v1.settings import _check_source_url

    with pytest.raises(HTTPException):
        _check_source_url(url)


@pytest.mark.asyncio
async def test_protocol_markdown_is_sanitized():
    from app.api.v1.data_intake import _safe_markdown_html

    html = _safe_markdown_html("<script>alert(1)</script>\n\n[x](javascript:alert(1)) [ok](https://dss.dwh.kz)")
    assert "<script>" not in html
    assert "javascript:" not in html
    assert 'href="https://dss.dwh.kz"' in html


# ── L-4: заголовки безопасности ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_security_headers_present(client):
    resp = await client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]


# ── Р-12: ИИН (привязка ЭЦП) меняет только администратор ─────────────────────

@pytest.mark.asyncio
async def test_non_admin_cannot_bind_iin_to_own_account(client, expert_token):
    headers = {"Authorization": f"Bearer {expert_token}"}
    resp = await client.patch("/api/v1/auth/me", json={"iin": "900101300123"}, headers=headers)
    assert resp.status_code == 403
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_non_admin_can_still_update_email(client, expert_token):
    headers = {"Authorization": f"Bearer {expert_token}"}
    resp = await client.patch("/api/v1/auth/me", json={"email": "expert-new@dss.kz"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["email"] == "expert-new@dss.kz"


@pytest.mark.asyncio
async def test_admin_can_bind_own_iin(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    resp = await client.patch("/api/v1/auth/me", json={"iin": "900101300124"}, headers=headers)
    assert resp.status_code == 200
