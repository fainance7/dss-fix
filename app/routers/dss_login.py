import os
import urllib.parse
import httpx
from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/upload", tags=["DSS SSO"])

DSS_BACKEND_URL = os.environ.get("DSS_BACKEND_URL", "http://dss-backend:8000")
SSO_COOKIE_NAME = "dss_session"
SSO_COOKIE_DOMAIN = os.environ.get("SSO_COOKIE_DOMAIN", ".dwh.kz")

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
    return LOGIN_PAGE.format(error_html="", next_path=next_)


@router.post("/login")
async def login_submit(
    username: str = Form(...),
    password: str = Form(...),
    next_: str = Form("/upload/", alias="next"),
):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{DSS_BACKEND_URL}/api/v1/auth/login",
                json={"username": username, "password": password},
            )
    except httpx.HTTPError:
        return HTMLResponse(
            LOGIN_PAGE.format(
                error_html='<div class="err">DSS недоступен, попробуйте позже</div>',
                next_path=next_,
            ),
            status_code=502,
        )

    if resp.status_code != 200:
        return HTMLResponse(
            LOGIN_PAGE.format(
                error_html='<div class="err">Неверный логин или пароль</div>',
                next_path=next_,
            ),
            status_code=401,
        )

    token = resp.json().get("access_token")
    response = RedirectResponse(url=next_ or "/upload/", status_code=303)
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
