import html
import os
import time
import urllib.parse
import httpx
from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/upload", tags=["DSS SSO"])

DSS_BACKEND_URL = os.environ.get("DSS_BACKEND_URL", "http://dss-backend:8000")
SSO_COOKIE_NAME = "dss_session"
SSO_COOKIE_DOMAIN = os.environ.get("SSO_COOKIE_DOMAIN", ".dwh.kz")

# M-4: in-memory rate limiter for the SSO login endpoint. The DSS backend has
# its own Redis-backed limiter, but this proxy endpoint is the one an attacker
# actually hits for brute-force, so it needs its own limit. Keyed by client IP.
# Reset on process restart (acceptable: this is a browser login page, not a
# high-throughput API). 5 failures per IP per 15 minutes, then 429.
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW_S  = 900
_login_fails: dict[str, list[float]] = {}  # ip -> [timestamps of failures]

def _safe_next(next_path: str | None) -> str:
    """Р-10: only local /upload paths are allowed as the post-login target
    (no scheme, no host, no protocol-relative //), otherwise /upload/."""
    if not next_path or not next_path.startswith("/upload") or next_path.startswith("//") \
            or "\\" in next_path or any(c in next_path for c in "\r\n"):
        return "/upload/"
    return next_path


def _page(error_html: str, next_path: str) -> str:
    """Render the login page; next_path is HTML-escaped (Р-10: reflected XSS)."""
    return LOGIN_PAGE.format(error_html=error_html, next_path=html.escape(next_path, quote=True))


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or "unknown"
    return request.client.host if request.client else "unknown"

def _login_blocked(ip: str) -> bool:
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_S
    fails = [t for t in _login_fails.get(ip, []) if t > cutoff]
    _login_fails[ip] = fails
    return len(fails) >= _LOGIN_MAX_FAILS

def _record_login_failure(ip: str) -> None:
    _login_fails.setdefault(ip, []).append(time.time())


LOGIN_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Вход - DSS</title>
<style>
body{{font-family:sans-serif;background:#0f1115;color:#eee;display:flex;height:100vh;align-items:center;justify-content:center;margin:0}}
form{{background:#1a1d24;padding:32px;border-radius:8px;width:320px}}
input{{width:100%;padding:10px;margin:8px 0;border-radius:4px;border:1px solid #333;background:#0f1115;color:#eee;box-sizing:border-box}}
button{{width:100%;padding:10px;border-radius:4px;border:none;background:#4c6ef5;color:#fff;cursor:pointer;margin-top:8px}}
.err{{color:#ff6b6b;margin-bottom:8px}}
h2{{margin-top:0;color:#eee}}
</style></head><body>
<form method="post" action="/upload/login">
<h2>Вход - DSS</h2>
{error_html}
<input type="hidden" name="next" value="{next_path}">
<input type="text" name="username" placeholder="Логин DSS" required autofocus>
<input type="password" name="password" placeholder="Пароль DSS" required>
<button type="submit">Войти</button>
</form>
</body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_form(next_: str = Query("/upload/", alias="next")):
    return _page("", _safe_next(next_))


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_: str = Form("/upload/", alias="next"),
):
    next_ = _safe_next(next_)
    # M-4: rate-limit per client IP before proxying to DSS backend.
    ip = _client_ip(request)
    if _login_blocked(ip):
        return HTMLResponse(
            _page(
                '<div class="err">Слишком много неудачных попыток входа. '
                'Повторите через 15 минут.</div>',
                next_,
            ),
            status_code=429,
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{DSS_BACKEND_URL}/api/v1/auth/login",
                json={"username": username, "password": password},
            )
    except httpx.HTTPError:
        return HTMLResponse(
            _page(
                '<div class="err">DSS недоступен, попробуйте позже</div>',
                next_,
            ),
            status_code=502,
        )

    if resp.status_code != 200:
        _record_login_failure(ip)  # M-4: count this failure for rate limiting
        return HTMLResponse(
            _page(
                '<div class="err">Неверный логин или пароль</div>',
                next_,
            ),
            status_code=401,
        )

    _login_fails.pop(ip, None)  # M-4: successful login clears the failure count
    token = resp.json().get("access_token")
    response = RedirectResponse(url=next_, status_code=303)
    response.set_cookie(
        key=SSO_COOKIE_NAME,
        value=token,
        domain=SSO_COOKIE_DOMAIN,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/upload/login", status_code=303)
    response.delete_cookie(SSO_COOKIE_NAME, domain=SSO_COOKIE_DOMAIN, path="/")
    return response
