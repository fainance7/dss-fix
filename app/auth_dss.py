import os
from fastapi import Request
from jose import JWTError, jwt

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
SSO_COOKIE_NAME = "dss_session"


class NotAuthenticated(Exception):
    def __init__(self, next_path: str = "/upload/"):
        self.next_path = next_path


def require_dss_session(request: Request):
    """Пускает на /upload/* только с валидным JWT DSS (cookie dss_session на .dwh.kz)."""
    token = request.cookies.get(SSO_COOKIE_NAME)
    if not token or not JWT_SECRET_KEY:
        raise NotAuthenticated(next_path=request.url.path)
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise NotAuthenticated(next_path=request.url.path)
    return payload
