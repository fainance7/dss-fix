"""Прокси к AI-микросервису (ai-service) с серверной авторизацией.

Фронт ходит на наш бэкенд (/api/v1/ai/*), мы форвардим запрос на ai-service
(settings.AI_SERVICE_URL). Это:
- требует валидный JWT DSS (get_current_active_user) — раньше браузер ходил на
  dssai напрямую с захардкоженным X-AI-Key, что светило секрет и обходило auth;
- инжектит X-AI-Key серверно, ключ не уходит в браузер;
- enforce-ит RBAC по направлениям: exp_dir с ограниченным списком направлений
  не может обращаться к ИИ-ресурсам чужих направлений (geo-context / прогноз по
  показателю / запрос с direction_id|direction_code|indicator_id);
- транзитом форвардит путь/тело/квери/ответ — при эволюции ИИ-API прокси менять
  не нужно.
"""

import asyncio
import json
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import log_action
from app.core.rbac import get_user_allowed_directions, get_user_region_scope
from app.database import get_db
from app.deps import get_current_active_user
from app.models.user import User

router = APIRouter(prefix="/ai", tags=["ai"])

# Заголовки соединения/исходного запроса не пробрасываем. Authorization (Bearer
# DSS) и клиентский X-AI-Key в ai-service не уходят — ключ ставит сам прокси.
_SKIP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "authorization",
    "cookie",
    "x-ai-key",
}

_client: httpx.AsyncClient | None = None
# Ссылки на фоновые задачи прокси — иначе сборщик мусора может снять их до конца.
_BACKGROUND: set[asyncio.Task] = set()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.AI_SERVICE_URL.rstrip("/"),
            timeout=settings.AI_SERVICE_TIMEOUT,
            headers={"X-AI-Key": settings.AI_SECRET_KEY},
        )
    return _client


async def _direction_by_code(db: AsyncSession, code: str) -> str | None:
    row = (
        await db.execute(text("SELECT id FROM directions WHERE code = :c"), {"c": code})
    ).first()
    return str(row[0]) if row else None


async def _required_direction_id(path: str, body: bytes, db: AsyncSession) -> str | None:
    """Best-effort вывод направления из запроса для RBAC.

    Источники по приоритету: direction_code в пути geo-context; в JSON-теле —
    direction_id, затем direction_code, затем indicator_id (→ его направление).
    None — направление из запроса не выводится (эндпойнт не направленческий:
    задачи/сценарии/прочее), такие запросы RBAC по направлениям не ограничивает.
    """
    parts = [p for p in path.split("/") if p]
    if "geo-context" in parts:
        i = parts.index("geo-context")
        if i + 1 < len(parts):
            return await _direction_by_code(db, parts[i + 1])

    if body:
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            if payload.get("direction_id"):
                return str(payload["direction_id"])
            if payload.get("direction_code"):
                return await _direction_by_code(db, str(payload["direction_code"]))
            if payload.get("indicator_id"):
                row = (
                    await db.execute(
                        text("SELECT direction_id FROM indicators WHERE id = :i"),
                        {"i": str(payload["indicator_id"])},
                    )
                ).first()
                return str(row[0]) if row and row[0] else None
    return None


# Действия, которые фронт показывает только отдельным ролям, — дублируем проверку
# на сервере: прокси форвардит транзитом, и без неё любой вошедший пользователь
# мог утвердить сценарий прямым запросом к API. Шаблон сравнивается с ХВОСТОМ
# пути (у фронта путь идёт с префиксом api/v1), «*» — любой сегмент.
# Последнее поле — ключ тела, в который прокси подставляет id текущего
# пользователя, чтобы «кто утвердил/отклонил» нельзя было подделать.
_EXPERTS = frozenset({"exp_dir", "admin"})
_SCENARIO_VIEWERS = frozenset({"exp_dir", "akim_obl", "akim_ray", "admin"})
_TASK_EXECUTORS = frozenset({"akim_ray", "akim_obl", "exp_dir", "admin"})

_PRIVILEGED: list[tuple[str, tuple[str, ...], frozenset[str], str | None, str]] = [
    ("POST", ("internal", "scenario-approve"), frozenset({"akim_obl", "admin"}), "approved_by",
     "Утверждать сценарии может только аким области"),
    ("POST", ("scenarios", "*", "reject"), frozenset({"akim_obl", "admin"}), "rejected_by",
     "Отклонять сценарии может только аким области"),
    ("POST", ("tasks", "*", "accept"), frozenset({"akim_obl", "admin"}), "author_id",
     "Принимать выполнение задач может только аким области"),
    ("POST", ("tasks", "*", "reject-completion"), frozenset({"akim_obl", "admin"}), "author_id",
     "Возвращать задачи на доработку может только аким области"),
    ("POST", ("models",), frozenset({"admin"}), None, "Регистрировать модели может только администратор"),
    ("PUT", ("models", "*", "activate"), frozenset({"admin"}), None, "Управлять моделями может только администратор"),
    ("PUT", ("models", "*", "deprecate"), frozenset({"admin"}), None, "Управлять моделями может только администратор"),
    ("PUT", ("models", "*", "metrics"), frozenset({"admin"}), None, "Управлять моделями может только администратор"),
    ("POST", ("models", "*", "rollback"), frozenset({"admin"}), None, "Управлять моделями может только администратор"),
    # Формирование и настройка сценариев — эксперт по направлениям (ТЗ ФИИ.4).
    ("POST", ("scenarios", "generate"), _EXPERTS, "created_by",
     "Формировать сценарии может эксперт по направлениям"),
    ("POST", ("scenarios",), _EXPERTS, "created_by", "Создавать сценарии может эксперт по направлениям"),
    # Аким области корректирует входные параметры перед утверждением (ТЗ, Прил. 2, ч. 3).
    ("PATCH", ("scenarios", "*", "parameters"), _EXPERTS | {"akim_obl"}, None,
     "Менять управляющие параметры может эксперт по направлениям или аким области"),
    ("POST", ("scenarios", "*", "calculate"), _EXPERTS | {"akim_obl"}, None,
     "Пересчитывать сценарии может эксперт по направлениям или аким области"),
    # Заключения ИИ запускают генерацию на GPU — только ролям, работающим со сценариями.
    ("POST", ("scenarios", "*", "narrative"), _SCENARIO_VIEWERS, None,
     "Нет доступа к заключениям по сценариям"),
    ("POST", ("scenarios", "*", "narrative", "all"), _SCENARIO_VIEWERS, None,
     "Нет доступа к заключениям по сценариям"),
    # Состав задач сценария формирует эксперт; после утверждения — и аким области.
    ("POST", ("scenarios", "*", "tasks", "generate"), _EXPERTS | {"akim_obl"}, None,
     "Формировать задачи может эксперт по направлениям или аким области"),
    # Задачи вне сценария, ответственные и сроки — аким области (ТЗ, Прил. 2, ч. 3) и эксперт.
    ("POST", ("tasks",), _EXPERTS | {"akim_obl"}, None,
     "Создавать задачи может аким области или эксперт по направлениям"),
    ("PATCH", ("tasks", "*"), _EXPERTS | {"akim_obl"}, "author_id",
     "Редактировать задачи может аким области или эксперт по направлениям"),
    ("DELETE", ("tasks", "*"), _EXPERTS | {"akim_obl"}, None,
     "Удалять задачи может аким области или эксперт по направлениям"),
    ("PUT", ("tasks", "*", "status"), _TASK_EXECUTORS, "author_id",
     "Менять статус задачи может исполнитель района, аким области или эксперт"),
    ("POST", ("tasks", "*", "comment"), _TASK_EXECUTORS | {"exp_ver"}, "author_id",
     "Нет доступа к комментариям задач"),
    # Снимок варианта ЛП считает сервер (/api/v1/landscape/scenarios/{id}/variant);
    # прямой записью клиент мог бы подложить произвольные доли зон — закрыто всем.
    ("PUT", ("scenarios", "*", "landscape"), frozenset(), None,
     "Вариант ландшафтного планирования меняется через /landscape/scenarios/{id}/variant"),
]


# Действия контура управления развитием, которые пишутся в журнал аудита:
# (метод, хвост пути, действие, тип объекта, позиция id объекта в хвосте или None).
_AUDITED: list[tuple[str, tuple[str, ...], str, str, int | None]] = [
    ("POST", ("scenarios", "generate"), "scenario_generate", "scenario", None),
    ("POST", ("scenarios",), "scenario_create", "scenario", None),
    ("PATCH", ("scenarios", "*", "parameters"), "scenario_update_parameters", "scenario", 1),
    ("POST", ("scenarios", "*", "calculate"), "scenario_calculate", "scenario", 1),
    ("POST", ("internal", "scenario-approve"), "scenario_approve", "scenario", None),
    ("POST", ("scenarios", "*", "reject"), "scenario_reject", "scenario", 1),
    ("POST", ("scenarios", "*", "tasks", "generate"), "task_generate", "scenario", 1),
    ("POST", ("tasks",), "task_create", "task", None),
    ("PATCH", ("tasks", "*"), "task_update", "task", 1),
    ("DELETE", ("tasks", "*"), "task_delete", "task", 1),
    ("PUT", ("tasks", "*", "status"), "task_status_change", "task", 1),
    ("POST", ("tasks", "*", "comment"), "task_comment", "task", 1),
    ("POST", ("tasks", "*", "accept"), "task_accept", "task", 1),
    ("POST", ("tasks", "*", "reject-completion"), "task_reject_completion", "task", 1),
]


def _match(parts: list[str], pattern: tuple[str, ...]) -> bool:
    if len(parts) < len(pattern):
        return False
    return all(p == "*" or p == t for p, t in zip(pattern, parts[-len(pattern):]))


def _uuid_or_none(value) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _audit(db: AsyncSession, request: Request, user: User, parts: list[str],
                 body: bytes, response_body: bytes) -> None:
    """Запись действия контура управления развитием в журнал аудита.

    Пишется только после успешного ответа ai-service: неудавшиеся попытки в журнал
    не попадают. В детали — тело запроса (статус, комментарий, параметры) без
    служебных полей автора, которые прокси подставил сам.
    """
    for method, pattern, action, entity_type, id_pos in _AUDITED:
        if method != request.method or not _match(parts, pattern):
            continue
        tail = parts[-len(pattern):]
        entity_id = _uuid_or_none(tail[id_pos]) if id_pos is not None else None
        try:
            payload = json.loads(body) if body else {}
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if entity_id is None:
            entity_id = _uuid_or_none(payload.get("scenario_id"))
        if entity_id is None:
            try:
                resp = json.loads(response_body) if response_body else None
            except (ValueError, TypeError):
                resp = None
            if isinstance(resp, dict):
                entity_id = _uuid_or_none(resp.get("id") or resp.get("scenario_id"))
        details = {k: v for k, v in payload.items()
                   if k not in ("approved_by", "rejected_by", "author_id", "created_by")}
        await log_action(
            db, user_id=user.id, action=action, entity_type=entity_type, entity_id=entity_id,
            details=details or None,
            ip_address=request.client.host if request.client else None,
        )
        await db.commit()
        return


def _path_parts(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный путь")
    return parts


def _enforce_privileged(parts: list[str], method: str, body: bytes, user: User) -> bytes:
    """403 для привилегированного действия без нужной роли; для разрешённого —
    тело с подставленным id пользователя в поле автора действия."""
    for m, pattern, roles, author_key, message in _PRIVILEGED:
        if m != method or len(parts) < len(pattern):
            continue
        tail = parts[-len(pattern):]
        if not all(p == "*" or p == t for p, t in zip(pattern, tail)):
            continue
        if not {ur.role.name for ur in user.user_roles} & roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)
        if author_key and body:
            try:
                payload = json.loads(body)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                payload[author_key] = str(user.id)
                if author_key == "approved_by":
                    payload["approved_by_name"] = user.username
                return json.dumps(payload).encode()
        return body
    return body


def _roles(user: User) -> set[str]:
    return {ur.role.name for ur in user.user_roles}


def _enforce_task_status(parts: list[str], method: str, body: bytes, user: User) -> None:
    """Правила смены статуса задачи (PUT tasks/{id}/status и PATCH tasks/{id}).

    «Принята» ставится только приёмкой (POST tasks/{id}/accept) — иначе исполнитель
    мог бы сам принять свою работу. «Отменено» — решение акима области или
    эксперта, а не исполнителя района.
    """
    is_status_put = method == "PUT" and len(parts) >= 3 and parts[-3] == "tasks" and parts[-1] == "status"
    # PATCH tasks/{id} тоже принимает status — те же правила, иначе приёмку можно обойти.
    is_task_patch = method == "PATCH" and len(parts) >= 2 and parts[-2] == "tasks"
    if not (is_status_put or is_task_patch):
        return
    try:
        new_status = json.loads(body or b"{}").get("status")
    except (ValueError, TypeError, AttributeError):
        return
    if new_status == "accepted":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Статус «Принята» ставится только приёмкой выполнения акиматом области",
        )
    if new_status == "cancelled" and not _roles(user) & {"akim_obl", "exp_dir", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Отменить задачу может аким области или эксперт по направлениям",
        )


async def _enforce_task_region_scope(
    path: str, method: str, request: Request, db: AsyncSession, user: User
) -> dict | None:
    """G9: akim_ray может менять/видеть задачи только своего района (RegionalTask
    живёт в ai-service, но БД общая — enforce на прокси, где есть пользователь).

    - мутация конкретной задачи (`tasks/{uuid}/...`, методы PUT/PATCH/DELETE/POST):
      403, если регион задачи вне района пользователя;
    - чтение конкретной задачи и её событий (`GET tasks/{uuid}[/events]`) — то же;
    - список задач (`GET /tasks?region_id=`): 403, если запрошен чужой район;
      без `region_id` список сужается до своего района — иначе ai-service отдал
      бы задачи всех районов.

    Возвращает query-параметры для форварда (с подставленным районом) или None,
    если их менять не нужно.
    """
    scope = get_user_region_scope(user)
    if scope is None:
        return None
    parts = [p for p in path.split("/") if p]
    if "tasks" not in parts:
        return None
    ti = parts.index("tasks")

    if ti + 1 < len(parts):
        try:
            task_id = UUID(parts[ti + 1])
        except ValueError:
            return None  # не UUID (напр. tasks/generate) — не адресная операция с задачей
        row = (await db.execute(
            text(
                "SELECT t.region_id, s.approved_at IS NOT NULL FROM regional_tasks t "
                "LEFT JOIN development_scenarios s ON s.id = t.scenario_id "
                "WHERE t.id = CAST(:i AS uuid)"
            ),
            {"i": str(task_id)},
        )).first()
        if row is not None and row[0] not in scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Задача другого района — доступ запрещён",
            )
        if row is not None and not row[1]:
            # Черновые задачи неутверждённого сценария исполнителям не адресованы.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Задача ещё не утверждена акиматом области",
            )
    elif method == "GET":
        rid = request.query_params.get("region_id")
        if rid:
            try:
                requested = UUID(rid)
            except ValueError:
                requested = None
            if requested not in scope:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Доступ только к задачам своего района",
                )
            params = dict(request.query_params)
            params["only_approved"] = "true"
            return params
        elif len(scope) == 1:
            params = dict(request.query_params)
            params["region_id"] = str(next(iter(scope)))
            params["only_approved"] = "true"
            return params
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Укажите район (region_id)",
            )
    return None


async def _enforce_scenario_scope(
    parts: list[str], method: str, request: Request, db: AsyncSession, user: User
) -> dict | None:
    """akim_ray видит только утверждённые сценарии и сравнения по ним (ТЗ, Прил. 2, ч. 3).

    - список сценариев сужается до статуса approved;
    - сценарий по id (и его вложенные ресурсы) и сравнение — 403, если среди них
      есть неутверждённый.

    Возвращает query-параметры для форварда или None, если их менять не нужно.
    """
    if get_user_region_scope(user) is None or "scenarios" not in parts or "tasks" in parts:
        return None
    si = parts.index("scenarios")
    ids: list[str] = []
    if si + 1 == len(parts):
        if method != "GET":
            return None
        params = dict(request.query_params)
        params["status"] = "approved"
        return params
    if parts[si + 1] == "compare":
        ids = [i.strip() for i in request.query_params.get("ids", "").split(",") if i.strip()]
    else:
        ids = [parts[si + 1]]
    try:
        uuids = [str(UUID(i)) for i in ids]
    except ValueError:
        return None
    if not uuids:
        return None
    rows = (await db.execute(
        text("SELECT status FROM development_scenarios WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": uuids},
    )).scalars().all()
    if any(st != "approved" for st in rows):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Акимату района доступны только утверждённые сценарии",
        )
    return None


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    body = await request.body()
    parts = _path_parts(path)
    body = _enforce_privileged(parts, request.method, body, user)
    _enforce_task_status(parts, request.method, body, user)

    allowed = get_user_allowed_directions(user)
    if allowed is not None:
        required = await _required_direction_id(path, body, db)
        if required is not None and required not in {str(d) for d in allowed}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к направлению запрошенного ИИ-ресурса",
            )

    # Вывод по вкладке дашборда строится по всем районам области — пользователю,
    # ограниченному своим районом (akim_ray), он не выдаётся.
    if "dashboard-summaries" in parts and get_user_region_scope(user) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Вывод по дашборду строится по данным всех районов области",
        )

    scoped_params = await _enforce_task_region_scope(path, request.method, request, db, user)
    scenario_params = await _enforce_scenario_scope(parts, request.method, request, db, user)
    if scenario_params is not None:
        scoped_params = scenario_params

    fwd_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS
    }

    client = _get_client()
    try:
        upstream = await client.request(
            request.method,
            f"/{path}",
            params=scoped_params if scoped_params is not None else request.query_params,
            content=body or None,
            headers=fwd_headers,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise HTTPException(status_code=503, detail="AI-сервис недоступен") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Ошибка обращения к AI-сервису: {exc}"
        ) from exc

    if upstream.status_code >= 400:
        detail = upstream.text
        try:
            data = upstream.json()
            detail = data.get("detail") or data.get("message") or detail
        except (ValueError, AttributeError):
            pass
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    if request.method != "GET":
        await _audit(db, request, user, parts, body, upstream.content)

    # Новые сценарии сразу получают вариант ЛП по умолчанию (V2) — в фоне, чтобы
    # не задерживать ответ генерации и не ломать её при недоступности ГИС.
    if request.method == "POST" and parts[-2:] == ["scenarios", "generate"]:
        from app.api.v1 import landscape_link

        task = asyncio.create_task(landscape_link.link_defaults_background(user.id))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
