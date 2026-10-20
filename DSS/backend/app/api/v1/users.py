import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import log_action
from app.core.rbac import get_user_region_scope, require_roles
from app.core.password_policy import password_problem
from app.core.security import hash_password
from app.database import get_db
from app.deps import get_current_active_user
from app.models.data import Direction
from app.models.user import Role, Region, User, UserDirection, UserRole
from pydantic import BaseModel as PydanticBaseModel

from app.schemas.user import (
    AssignDirectionsRequest,
    AssignRoleRequest,
    DirectionBrief,
    UserCreate,
    UserListResponse,
    UserOut,
    UserRoleOut,
    UserUpdate,
)


class UserMinimal(PydanticBaseModel):
    id: UUID
    username: str

router = APIRouter(prefix="/users", tags=["users"])


def _safe_user_directions(user: User) -> list:
    """Return loaded user_directions or [] without triggering lazy load."""
    from sqlalchemy import inspect as sa_inspect
    state = sa_inspect(user)
    if "user_directions" in state.unloaded:
        return []
    return user.user_directions or []


def _build_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        iin=user.iin,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at,
        roles=[
            UserRoleOut(
                role_name=ur.role.name,
                region_id=ur.region_id,
                region_name=ur.region.name if ur.region else None,
                assigned_at=ur.assigned_at,
            )
            for ur in user.user_roles
        ],
        directions=[
            DirectionBrief(id=ud.direction.id, code=ud.direction.code, name=ud.direction.name)
            for ud in _safe_user_directions(user)
        ],
    )


def _revoke_tokens(user: User) -> None:
    """Отзыв всех токенов пользователя: новые права действуют с первого же запроса."""
    user.token_version = (user.token_version or 0) + 1


async def _get_user_or_404(db: AsyncSession, user_id: UUID) -> User:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.user_roles).selectinload(UserRole.role),
            selectinload(User.user_roles).selectinload(UserRole.region),
            selectinload(User.user_directions).selectinload(UserDirection.direction),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/by-region", response_model=list[UserMinimal])
async def users_by_region(
    region_id: UUID = Query(..., description="UUID региона"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "akim_obl", "akim_ray")),
):
    """Список пользователей региона (id+username) — для назначения/отображения
    исполнителей задач. Доступно ролям, работающим с задачами (ADM/AKIM_OBL/AKIM_RAY);
    akim_ray — только по своему району (G9/G11, ТЗ Приложение 2)."""
    scope = get_user_region_scope(current_user)
    if scope is not None and region_id not in scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ только к пользователям своего района",
        )
    result = await db.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .where(UserRole.region_id == region_id, User.is_active == True)
        .distinct()
        .order_by(User.username)
    )
    return [UserMinimal(id=u.id, username=u.username) for u in result.scalars().unique().all()]


@router.get("", response_model=UserListResponse)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    q = select(User).options(
        selectinload(User.user_roles).selectinload(UserRole.role),
        selectinload(User.user_roles).selectinload(UserRole.region),
        selectinload(User.user_directions).selectinload(UserDirection.direction),
    )
    if is_active is not None:
        q = q.where(User.is_active == is_active)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()

    result = await db.execute(q.offset((page - 1) * page_size).limit(page_size))
    users = result.scalars().unique().all()

    return UserListResponse(
        items=[_build_user_out(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    # Check uniqueness
    uniqueness_filter = (User.username == body.username) | (User.email == body.email)
    if body.iin is not None:
        uniqueness_filter = uniqueness_filter | (User.iin == body.iin)
    existing = await db.execute(select(User).where(uniqueness_filter))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username, email or IIN already exists")
    problem = password_problem(body.password, body.username)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)

    user = User(
        username=body.username,
        email=body.email,
        iin=body.iin,
        password_hash=hash_password(body.password),
        is_active=body.is_active,
    )
    db.add(user)
    await db.flush()

    await log_action(
        db,
        user_id=current_user.id,
        action="create_user",
        entity_type="user",
        entity_id=user.id,
        details={"username": body.username, "email": body.email},
    )

    await db.refresh(user, ["user_roles", "user_directions"])
    return _build_user_out(user)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    return _build_user_out(await _get_user_or_404(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    user = await _get_user_or_404(db, user_id)

    changes = {}
    if body.username is not None:
        user.username = body.username
        changes["username"] = body.username
    if body.email is not None:
        user.email = body.email
        changes["email"] = body.email
    if body.is_active is not None:
        user.is_active = body.is_active
        changes["is_active"] = body.is_active
    if body.password is not None:
        problem = password_problem(body.password, body.username or user.username)
        if problem:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=problem)
        user.password_hash = hash_password(body.password)
        changes["password_changed"] = True
    # Блокировка или новый пароль отзывают выданные пользователю токены (M-7).
    if body.is_active is False or body.password is not None:
        _revoke_tokens(user)
    if body.iin is not None:
        collision = await db.execute(
            select(User).where((User.iin == body.iin) & (User.id != user_id))
        )
        if collision.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="IIN already in use")
        user.iin = body.iin
        changes["iin"] = body.iin

    await log_action(
        db,
        user_id=current_user.id,
        action="update_user",
        entity_type="user",
        entity_id=user.id,
        details=changes,
    )
    return _build_user_out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    user = await _get_user_or_404(db, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    await db.delete(user)
    await log_action(
        db,
        user_id=current_user.id,
        action="delete_user",
        entity_type="user",
        entity_id=user_id,
    )


@router.post("/{user_id}/roles", response_model=UserOut, status_code=status.HTTP_200_OK)
async def assign_role(
    user_id: UUID,
    body: AssignRoleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    user = await _get_user_or_404(db, user_id)

    role_result = await db.execute(select(Role).where(Role.name == body.role_name))
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role '{body.role_name}' not found")

    # Check if already assigned
    existing = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role.id,
            UserRole.region_id == body.region_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Role already assigned")

    ur = UserRole(user_id=user_id, role_id=role.id, region_id=body.region_id)
    db.add(ur)

    await log_action(
        db,
        user_id=current_user.id,
        action="assign_role",
        entity_type="user",
        entity_id=user_id,
        details={"role": body.role_name, "region_id": str(body.region_id) if body.region_id else None},
    )

    return _build_user_out(await _get_user_or_404(db, user_id))


@router.delete("/{user_id}/roles/{role_name}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    user_id: UUID,
    role_name: str,
    region_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    role_result = await db.execute(select(Role).where(Role.name == role_name))
    role = role_result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Role '{role_name}' not found")

    ur_result = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role.id,
            UserRole.region_id == region_id,
        )
    )
    ur = ur_result.scalar_one_or_none()
    if not ur:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role assignment not found")

    await db.delete(ur)
    await log_action(
        db,
        user_id=current_user.id,
        action="remove_role",
        entity_type="user",
        entity_id=user_id,
        details={"role": role_name},
    )


@router.put("/{user_id}/directions", response_model=UserOut)
async def assign_directions(
    user_id: UUID,
    body: AssignDirectionsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """Назначить/заменить направления для exp_dir пользователя.
    Пустой список = снять ограничения (доступ ко всем направлениям).
    """
    user = await _get_user_or_404(db, user_id)

    # Verify user has exp_dir role
    role_names = {ur.role.name for ur in user.user_roles}
    if 'exp_dir' not in role_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Direction restrictions can only be set for users with exp_dir role",
        )

    # Validate direction_ids exist
    if body.direction_ids:
        result = await db.execute(
            select(func.count()).select_from(Direction).where(Direction.id.in_(body.direction_ids))
        )
        found = result.scalar_one()
        if found != len(body.direction_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="One or more directions not found")

    # Delete existing assignments
    existing = await db.execute(
        select(UserDirection).where(UserDirection.user_id == user_id)
    )
    for ud in existing.scalars().all():
        await db.delete(ud)

    # Insert new assignments
    for dir_id in body.direction_ids:
        db.add(UserDirection(user_id=user_id, direction_id=dir_id))

    await db.flush()

    await log_action(
        db,
        user_id=current_user.id,
        action="assign_directions",
        entity_type="user",
        entity_id=user_id,
        details={"direction_ids": [str(d) for d in body.direction_ids]},
    )

    # Expunge cached user and re-fetch with fresh eager loads
    db.expunge(user)
    refreshed = await _get_user_or_404(db, user_id)
    return _build_user_out(refreshed)
