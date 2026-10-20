from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.security import decode_token
from app.core.session import check_csrf
from app.database import get_db
from app.models.user import User, UserRole, UserDirection, Role, Region

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Заголовок Authorization — для скриптов и интеграций; cookie — для веб-интерфейса (H-1).
    if credentials is not None:
        token = credentials.credentials
    else:
        token = request.cookies.get(settings.AUTH_COOKIE_ACCESS)
        if not token:
            # 401, а не 403: веб-интерфейс на 401 обновляет сеанс по refresh-cookie.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated",
                                headers={"WWW-Authenticate": "Bearer"})
        check_csrf(request)
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exception
        user_id: str = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.user_roles)
            .selectinload(UserRole.role),
            selectinload(User.user_roles)
            .selectinload(UserRole.region),
            selectinload(User.user_directions)
            .selectinload(UserDirection.direction),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    # Токен выпущен до смены пароля, выхода или блокировки — отозван (M-7).
    if user is None or payload.get("tv", 0) != (user.token_version or 0):
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return current_user
