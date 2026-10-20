from fastapi import Depends, HTTPException, status

from app.deps import get_current_active_user
from app.models.user import User


def require_roles(*role_names: str):
    """Dependency factory: user must have at least one of the given roles."""
    async def checker(current_user: User = Depends(get_current_active_user)) -> User:
        user_role_names = {ur.role.name for ur in current_user.user_roles}
        if not user_role_names.intersection(role_names):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required roles: {list(role_names)}",
            )
        return current_user
    return checker


def require_admin():
    return require_roles("admin")


def get_user_region_scope(user: User) -> set | None:
    """Return the set of region UUIDs the user is limited to on reads, or None if
    unrestricted.

    Restricted ТОЛЬКО для akim_ray без более широкой роли: акимат района видит
    данные/задачи только своего района (ТЗ, Приложение 2). admin, akim_obl и
    эксперты имеют широкий доступ по региону (None). В MVP (одна область СКО)
    akim_obl фактически видит всю область.
    """
    role_names = {ur.role.name for ur in user.user_roles}
    broad = {"admin", "akim_obl", "exp_ver", "exp_dir", "data_uploader"}
    if role_names & broad:
        return None
    if "akim_ray" in role_names:
        return {
            ur.region_id
            for ur in user.user_roles
            if ur.role.name == "akim_ray" and ur.region_id is not None
        }
    return None


def get_user_allowed_directions(user: User) -> list | None:
    """Return list of allowed direction UUIDs, or None if unrestricted.

    Only exp_dir users with non-empty user_directions are restricted.
    All other roles and unrestricted exp_dir users get None (= all directions).
    """
    role_names = {ur.role.name for ur in user.user_roles}
    if 'exp_dir' not in role_names:
        return None
    if not hasattr(user, 'user_directions') or not user.user_directions:
        return None
    return [ud.direction_id for ud in user.user_directions]
