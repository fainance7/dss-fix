"""
Pytest fixtures for DSS backend tests.
Requires PostgreSQL running with POSTGRES_* env vars set.
"""
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.security import hash_password
from app.database import Base, get_db
from app.main import app
from app.models.user import AuditLog, Role, User, UserRole

# Use test database — NullPool avoids event-loop binding issues in async tests
TEST_DATABASE_URL = settings.DATABASE_URL


@pytest_asyncio.fixture(scope="session")
async def engine():
    from sqlalchemy import text
    eng = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
    async with eng.begin() as conn:
        # PostGIS extension required for geometry columns
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
        # Seed 8 fixed directions (required for data_layers.direction_id NOT NULL)
        # Model has Python-side UUID default, raw SQL must supply id explicitly
        await conn.execute(text("""
            INSERT INTO directions (id, code, name) VALUES
                (uuid_generate_v4(), 'labor',        'Трудовые ресурсы'),
                (uuid_generate_v4(), 'social_demog', 'Социально-демографическое'),
                (uuid_generate_v4(), 'water',        'Водные ресурсы'),
                (uuid_generate_v4(), 'economy',      'Экономическое'),
                (uuid_generate_v4(), 'ecology',      'Экология'),
                (uuid_generate_v4(), 'tourism',      'Туризм'),
                (uuid_generate_v4(), 'transport',    'Транспорт'),
                (uuid_generate_v4(), 'climate',      'Климат')
            ON CONFLICT (code) DO NOTHING
        """))
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _truncate_between_tests(engine):
    """After every test, wipe non-seed tables so tests don't see each other's
    rows. Directions stay (re-seeded if missing); roles are reused but tied
    to test users via UserRole, which is truncated each round."""
    yield
    from sqlalchemy import text
    async with engine.begin() as conn:
        # Find all tables except the small seed/lookup tables we want to keep.
        rows = (await conn.execute(text(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' "
            "  AND tablename NOT IN ('directions', 'roles', 'spatial_ref_sys')"
        ))).fetchall()
        if rows:
            tables = ", ".join(f'"{r[0]}"' for r in rows)
            await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await _reset_login_counters()


async def _reset_login_counters():
    """Счётчики неудачных входов (H-2) живут в Redis между тестами — сбрасываем,
    иначе тесты с неверным паролем заблокировали бы вход последующим тестам."""
    import redis.asyncio as aioredis

    from app.config import settings
    from app.core import session
    session._memory.clear()
    # Отдельный клиент: общий из app.core.cache привязан к циклу событий другого теста.
    client = aioredis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
    try:
        keys = [k async for k in client.scan_iter("login:*")]
        if keys:
            await client.delete(*keys)
    except Exception:
        pass  # Redis недоступен — счётчики только в памяти, уже очищены
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def session_factory(engine):
    """Sessionmaker bound to the test engine. Used by both `db` (for direct
    setup in tests) and `client` (one fresh session per API request)."""
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Direct DB session for tests that need to seed data (regions, users, …).
    Tests must `await db.commit()` if they want data visible to API calls."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose injected `get_db` opens a *fresh* session per request,
    matching the real app semantics (commit on success, rollback on error).
    Sharing one session across the test and API requests caused asyncpg
    'another operation is in progress' errors when autoflush ran on a
    connection that the test was still holding."""

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    # Sub-app /intake-portal — отдельный dependency_overrides dict, который НЕ
    # наследует от родителя; нужно подменить get_db и в нём, иначе обращения
    # к /intake-portal в тестах пойдут на prod-DB (localhost:5432) и упадут.
    try:
        from app.intake_portal import intake_portal as _intake_portal
        _intake_portal.dependency_overrides[get_db] = override_get_db
    except ImportError:
        _intake_portal = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    if _intake_portal is not None:
        # Сохраняем подмену get_current_active_user → get_user_by_api_key,
        # удаляем только get_db.
        _intake_portal.dependency_overrides.pop(get_db, None)


# -----------------------------------------------------------------------
# Seed helpers
# -----------------------------------------------------------------------

async def _ensure_roles(db: AsyncSession) -> dict[str, Role]:
    """Insert all known roles if not exists; return dict role_name→Role."""
    from sqlalchemy import select
    roles = {}
    for role_name in ["admin", "exp_ver", "exp_dir", "akim_obl", "akim_ray", "data_uploader"]:
        result = await db.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if not role:
            role = Role(name=role_name, description=role_name)
            db.add(role)
            await db.flush()
        roles[role_name] = role
    await db.commit()
    return roles


async def _create_user(db: AsyncSession, username: str, email: str, password: str) -> User:
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        user = User(username=username, email=email, password_hash=hash_password(password))
        db.add(user)
        await db.flush()
        await db.commit()
    return user


@pytest_asyncio.fixture
async def roles(db: AsyncSession) -> dict[str, Role]:
    return await _ensure_roles(db)


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession, roles: dict[str, Role]) -> User:
    user = await _create_user(db, "test_admin", "admin@example.com", "AdminPass123!")
    # assign admin role
    from sqlalchemy import select
    existing = await db.execute(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == roles["admin"].id)
    )
    if not existing.scalar_one_or_none():
        ur = UserRole(user_id=user.id, role_id=roles["admin"].id, region_id=None)
        db.add(ur)
        await db.commit()
    return user


@pytest_asyncio.fixture
async def regular_user(db: AsyncSession, roles: dict[str, Role]) -> User:
    user = await _create_user(db, "test_expert", "expert@example.com", "ExpertPass123!")
    from sqlalchemy import select
    existing = await db.execute(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == roles["exp_ver"].id)
    )
    if not existing.scalar_one_or_none():
        ur = UserRole(user_id=user.id, role_id=roles["exp_ver"].id, region_id=None)
        db.add(ur)
        await db.commit()
    return user


async def _get_token(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, admin_user: User) -> str:
    return await _get_token(client, "test_admin", "AdminPass123!")


@pytest_asyncio.fixture
async def data_uploader_user(db: AsyncSession, roles: dict[str, Role]) -> User:
    user = await _create_user(db, "test_uploader", "uploader@example.com", "UploadPass123!")
    from sqlalchemy import select
    existing = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user.id, UserRole.role_id == roles["data_uploader"].id
        )
    )
    if not existing.scalar_one_or_none():
        ur = UserRole(user_id=user.id, role_id=roles["data_uploader"].id, region_id=None)
        db.add(ur)
        await db.commit()
    return user


@pytest_asyncio.fixture
async def data_uploader_token(client: AsyncClient, data_uploader_user: User) -> str:
    return await _get_token(client, "test_uploader", "UploadPass123!")


@pytest_asyncio.fixture
async def expert_token(client: AsyncClient, regular_user: User) -> str:
    return await _get_token(client, "test_expert", "ExpertPass123!")
