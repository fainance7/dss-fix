import base64
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.audit import log_action
from app.core.cache import get_cache
from app.core.ecp import ECPError, validate_cms_and_get_iin
from app.core.ecp_ncanode import verify_cms_via_ncanode
from app.core.password_policy import password_problem
from app.core.session import (
    check_csrf,
    clear_auth_cookies,
    client_ip,
    guard_login_attempt,
    register_login_failure,
    register_login_success,
    set_auth_cookies,
    wants_cookie,
)
from app.core.security import (
    create_access_token,
    create_challenge_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.deps import get_current_active_user
from app.models.user import User, UserRole
from app.schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    DirectionInfo,
    ECPChallengeResponse,
    ECPLoginRequest,
    LoginRequest,
    MeResponse,
    ProfileUpdate,
    RefreshRequest,
    RoleInfo,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue(user: User, request: Request, response: Response) -> TokenResponse:
    """Выдаёт пару токенов. Веб-интерфейсу (X-Auth-Mode: cookie) — только в httpOnly-cookie,
    в теле ответа токенов нет; скриптам — в теле, как раньше."""
    tv = user.token_version or 0
    access_token = create_access_token(str(user.id), token_version=tv)
    refresh_token = create_refresh_token(str(user.id), token_version=tv)
    if wants_cookie(request):
        set_auth_cookies(response, access_token, refresh_token)
        return TokenResponse()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse, response_model_exclude_none=True)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    await guard_login_attempt(body.username, ip)

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.user_roles).selectinload(UserRole.role),
            selectinload(User.user_roles).selectinload(UserRole.region),
        )
        .where(User.username == body.username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        fails = await register_login_failure(body.username, ip)
        # Неудачный вход — в журнал аудита (H-2); пароль не пишется.
        await log_action(
            db,
            user_id=user.id if user else None,
            action="login_failed",
            entity_type="user",
            entity_id=user.id if user else None,
            ip_address=ip,
            details={"username": body.username[:100], "consecutive_failures": fails},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    await register_login_success(body.username)
    await log_action(
        db,
        user_id=user.id,
        action="login",
        entity_type="user",
        entity_id=user.id,
        ip_address=ip,
    )
    return _issue(user, request, response)


@router.post("/refresh", response_model=AccessTokenResponse, response_model_exclude_none=True)
async def refresh(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
    )
    from_cookie = not (body and body.refresh_token)
    if from_cookie:
        token = request.cookies.get(settings.AUTH_COOKIE_REFRESH)
        if not token:
            raise credentials_exception
        check_csrf(request)
    else:
        token = body.refresh_token
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise credentials_exception
        user_id = payload.get("sub")
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active or payload.get("tv", 0) != (user.token_version or 0):
        raise credentials_exception

    access_token = create_access_token(str(user.id), token_version=user.token_version or 0)
    # Refresh по cookie никогда не возвращает токен в теле — иначе XSS мог бы его получить.
    if from_cookie:
        set_auth_cookies(response, access_token)
        return AccessTokenResponse()
    return AccessTokenResponse(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Выход: удаляет cookie и отзывает все токены пользователя (M-7)."""
    current_user.token_version = (current_user.token_version or 0) + 1
    await log_action(db, user_id=current_user.id, action="logout", entity_type="user",
                     entity_id=current_user.id)
    clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


_ECP_NONCE_PREFIX = "ecp_nonce:"


@router.get("/ecp-challenge", response_model=ECPChallengeResponse)
async def ecp_challenge():
    """Generate a one-time challenge for ЭЦП login. Valid for 5 minutes.

    Nonce также сохраняется в Redis как ОДНОРАЗОВЫЙ (anti-replay): при входе он
    удаляется (GETDEL), повторное использование той же подписи невозможно."""
    nonce_b64 = base64.b64encode(secrets.token_bytes(32)).decode()
    token = create_challenge_token(nonce_b64)
    redis = await get_cache()
    if redis:
        await redis.set(_ECP_NONCE_PREFIX + nonce_b64, "1", ex=settings.ECP_NONCE_TTL_SECONDS)
    return ECPChallengeResponse(challenge_token=token, nonce=nonce_b64)


@router.post("/login-ecp", response_model=TokenResponse, response_model_exclude_none=True)
async def login_ecp(body: ECPLoginRequest, request: Request, response: Response,
                    db: AsyncSession = Depends(get_db)):
    """Authenticate via ЭЦП: verify NCALayer CMS signature, extract ИИН, return JWT tokens."""
    ip = client_ip(request)
    await guard_login_attempt("ecp:" + (ip or ""), ip)
    # 1. Verify challenge JWT
    try:
        payload = decode_token(body.challenge_token)
        if payload.get("type") != "ecp_challenge":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid challenge token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Challenge token expired or invalid")
    nonce_b64 = payload.get("sub") or ""

    # 2. Parse CMS, validate certificate, extract ИИН
    _ECP_ERROR_MAP: dict[str, tuple[str, int]] = {
        "cert_expired": ("Срок действия сертификата истёк", 401),
        "cert_not_yet_valid": ("Сертификат ещё не действителен", 401),
        "cert_untrusted_issuer": ("Сертификат выдан неизвестным удостоверяющим центром", 401),
        "cert_invalid": ("Сертификат недействителен", 401),
        "cert_revoked": ("Сертификат отозван", 401),
        "iin_not_in_cert": ("ИИН не найден в сертификате ЭЦП", 401),
        "ecp_invalid_signature": ("Подпись ЭЦП недействительна", 401),
        "ecp_nonce_mismatch": ("Подписан неверный запрос (повторный вход невозможен)", 401),
        "ecp_no_cert": ("В подписи нет сертификата", 400),
        "ncanode_unavailable": ("Сервис проверки ЭЦП недоступен", 503),
    }
    try:
        if settings.ECP_VERIFY_MODE == "ncanode":
            # Одноразовый nonce (anti-replay): изымаем из Redis; отсутствует → replay/истёк.
            redis = await get_cache()
            if redis is not None:
                try:
                    used = await redis.getdel(_ECP_NONCE_PREFIX + nonce_b64)
                except AttributeError:
                    used = await redis.get(_ECP_NONCE_PREFIX + nonce_b64)
                    await redis.delete(_ECP_NONCE_PREFIX + nonce_b64)
                if not used:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Challenge истёк или уже использован",
                    )
            iin = await verify_cms_via_ncanode(body.cms_signature, nonce_b64)
        else:
            iin = validate_cms_and_get_iin(body.cms_signature)
    except ECPError as exc:
        detail, http_status = _ECP_ERROR_MAP.get(exc.error_code, (str(exc), 400))
        if http_status == 401:
            await register_login_failure("ecp:" + (ip or ""), ip)
            await log_action(db, user_id=None, action="login_failed", entity_type="user",
                             ip_address=ip, details={"method": "ecp", "reason": exc.error_code})
            await db.commit()
        raise HTTPException(status_code=http_status, detail=detail)

    # 3. Look up user by ИИН
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.user_roles).selectinload(UserRole.role),
            selectinload(User.user_roles).selectinload(UserRole.region),
        )
        .where(User.iin == iin)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь с таким ИИН не зарегистрирован в системе",
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is disabled")

    # 4. Issue JWT tokens
    await log_action(
        db,
        user_id=user.id,
        action="login_ecp",
        entity_type="user",
        entity_id=user.id,
        ip_address=ip,
        details={"iin": iin},
    )
    return _issue(user, request, response)


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_active_user)):
    roles = [
        RoleInfo(
            name=ur.role.name,
            region_id=str(ur.region_id) if ur.region_id else None,
            region_name=ur.region.name if ur.region else None,
        )
        for ur in current_user.user_roles
    ]
    directions = [
        DirectionInfo(
            id=str(ud.direction.id),
            code=ud.direction.code,
            name=ud.direction.name,
        )
        for ud in (current_user.user_directions if hasattr(current_user, 'user_directions') and current_user.user_directions else [])
    ]
    return MeResponse(
        id=str(current_user.id),
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        roles=roles,
        directions=directions,
    )


@router.patch("/me", response_model=MeResponse)
async def update_me(
    body: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    changes = {}
    if body.email is not None:
        current_user.email = body.email
        changes["email"] = body.email
    if body.iin is not None:
        collision = await db.execute(
            select(User).where((User.iin == body.iin) & (User.id != current_user.id))
        )
        if collision.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="ИИН уже используется другим пользователем")
        current_user.iin = body.iin or None
        changes["iin"] = body.iin

    if changes:
        await log_action(
            db,
            user_id=current_user.id,
            action="update_profile",
            entity_type="user",
            entity_id=current_user.id,
            details=changes,
        )

    roles = [
        RoleInfo(
            name=ur.role.name,
            region_id=str(ur.region_id) if ur.region_id else None,
            region_name=ur.region.name if ur.region else None,
        )
        for ur in current_user.user_roles
    ]
    directions = [
        DirectionInfo(
            id=str(ud.direction.id),
            code=ud.direction.code,
            name=ud.direction.name,
        )
        for ud in (current_user.user_directions if hasattr(current_user, 'user_directions') and current_user.user_directions else [])
    ]
    return MeResponse(
        id=str(current_user.id),
        username=current_user.username,
        email=current_user.email,
        is_active=current_user.is_active,
        roles=roles,
        directions=directions,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неверный текущий пароль")
    problem = password_problem(body.new_password, current_user.username)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
    current_user.password_hash = hash_password(body.new_password)
    # Смена пароля отзывает все прежние токены (M-7); текущему сеансу выдаются новые.
    current_user.token_version = (current_user.token_version or 0) + 1
    if request.cookies.get(settings.AUTH_COOKIE_ACCESS):
        tv = current_user.token_version
        set_auth_cookies(response, create_access_token(str(current_user.id), token_version=tv),
                         create_refresh_token(str(current_user.id), token_version=tv))
    await log_action(
        db,
        user_id=current_user.id,
        action="change_password",
        entity_type="user",
        entity_id=current_user.id,
    )
