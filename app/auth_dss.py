import hashlib
import os
import time

import httpx
from fastapi import HTTPException, Request
from jose import JWTError, jwt

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
SSO_COOKIE_NAME = "dss_session"
DSS_BACKEND_URL = os.environ.get("DSS_BACKEND_URL", "http://dss-backend:8000")

# Р-11: роли DSS, которым разрешена загрузка данных через /upload.
ETL_ALLOWED_ROLES = {
    r.strip() for r in os.environ.get("ETL_ALLOWED_ROLES", "admin,exp_ver,exp_dir,data_uploader").split(",")
    if r.strip()
}
# Подтверждение сессии в DSS кешируется ненадолго, чтобы не дёргать бэкенд на
# каждый запрос; блокировка/выход в DSS начинают действовать не позже TTL.
_SESSION_TTL_S = 60
_session_cache: dict[str, tuple[float, dict]] = {}


class NotAuthenticated(Exception):
    def __init__(self, next_path: str = "/upload/"):
        self.next_path = next_path


def _confirm_with_dss(token: str) -> dict | None:
    """Проверяет токен в DSS (/auth/me): учитывает token_version, блокировку
    и текущие роли пользователя. None — сессия недействительна."""
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    cached = _session_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    try:
        resp = httpx.get(f"{DSS_BACKEND_URL}/api/v1/auth/me",
                         headers={"Authorization": f"Bearer {token}"}, timeout=5)
    except httpx.HTTPError:
        raise HTTPException(503, "DSS недоступен, попробуйте позже")
    if resp.status_code != 200:
        _session_cache.pop(key, None)
        return None
    me = resp.json()
    _session_cache[key] = (now + _SESSION_TTL_S, me)
    if len(_session_cache) > 1000:
        for k, (exp, _) in list(_session_cache.items()):
            if exp <= now:
                _session_cache.pop(k, None)
    return me


def require_dss_session(request: Request):
    """Пускает на /upload/* только с действующей сессией DSS (cookie dss_session
    на .dwh.kz): подпись JWT проверяется локально, затем DSS подтверждает, что
    пользователь активен, токен не отозван (Р-11) и у него есть роль загрузки."""
    token = request.cookies.get(SSO_COOKIE_NAME)
    if not token or not JWT_SECRET_KEY:
        raise NotAuthenticated(next_path=request.url.path)
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise NotAuthenticated(next_path=request.url.path)
    if payload.get("type") != "access":
        raise NotAuthenticated(next_path=request.url.path)
    me = _confirm_with_dss(token)
    if not me or not me.get("is_active"):
        raise NotAuthenticated(next_path=request.url.path)
    roles = {r.get("name") for r in me.get("roles", [])}
    if not roles & ETL_ALLOWED_ROLES:
        raise HTTPException(403, "Недостаточно прав для загрузки данных")
    return payload
