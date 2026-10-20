"""RBAC прокси к ai-service (`/api/v1/ai/*`).

Прокси форвардит запросы транзитом, поэтому роли, которые фронт проверяет на
кнопках (утверждение сценария, приёмка задачи, управление моделями), обязаны
проверяться и на сервере. ai-service заменён `httpx.MockTransport`: тест видит,
дошёл ли запрос до него и с каким телом.
"""
import json
import uuid

import httpx
import pytest
from httpx import AsyncClient

from app.api.v1 import ai as ai_proxy
from app.core.security import hash_password
from app.models.user import Region, User, UserRole

PASSWORD = "ProxyPass123!"
APPROVE = "/api/v1/ai/api/v1/internal/scenario-approve"


@pytest.fixture
def upstream(monkeypatch):
    """Подменяет клиент ai-service; возвращает список дошедших запросов."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(base_url="http://ai", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(ai_proxy, "_get_client", lambda: client)
    return calls


async def _user(db, roles, username: str, role: str, region_id=None) -> User:
    user = User(username=username, email=f"{username}@example.com", password_hash=hash_password(PASSWORD))
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=roles[role].id, region_id=region_id))
    await db.commit()
    return user


async def _token(client: AsyncClient, username: str) -> dict:
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _region(db, code: str) -> Region:
    region = Region(code=code, name=f"Район {code}", level=2)
    db.add(region)
    await db.commit()
    return region


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["exp_dir", "exp_ver", "akim_ray"])
async def test_scenario_approve_forbidden_for_non_akim_obl(client, db, roles, upstream, role):
    await _user(db, roles, f"u_{role}", role)
    headers = await _token(client, f"u_{role}")
    resp = await client.post(APPROVE, headers=headers, json={"scenario_id": str(uuid.uuid4())})
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
async def test_scenario_reject_forbidden_for_exp_dir(client, db, roles, upstream):
    await _user(db, roles, "u_dir", "exp_dir")
    headers = await _token(client, "u_dir")
    resp = await client.post(
        f"/api/v1/ai/api/v1/scenarios/{uuid.uuid4()}/reject", headers=headers, json={"reason": "x"},
    )
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_obl", "admin"])
async def test_scenario_approve_allowed_and_author_is_current_user(client, db, roles, upstream, role):
    user = await _user(db, roles, f"ok_{role}", role)
    headers = await _token(client, f"ok_{role}")
    spoofed = str(uuid.uuid4())
    resp = await client.post(
        APPROVE, headers=headers,
        json={"scenario_id": str(uuid.uuid4()), "approved_by": spoofed, "comment": "ок"},
    )
    assert resp.status_code == 200
    assert len(upstream) == 1
    sent = json.loads(upstream[0].content)
    assert sent["approved_by"] == str(user.id)
    assert sent["comment"] == "ок"


@pytest.mark.asyncio
async def test_task_accept_forbidden_for_akim_ray(client, db, roles, upstream):
    await _user(db, roles, "u_ray_acc", "akim_ray")
    headers = await _token(client, "u_ray_acc")
    resp = await client.post(
        f"/api/v1/ai/api/v1/tasks/{uuid.uuid4()}/accept", headers=headers, json={"comment": "x"},
    )
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
async def test_model_activate_admin_only(client, db, roles, upstream):
    await _user(db, roles, "u_obl_m", "akim_obl")
    headers = await _token(client, "u_obl_m")
    resp = await client.put(f"/api/v1/ai/api/v1/models/{uuid.uuid4()}/activate", headers=headers)
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/api/v1/ai/api/v1/internal/scenario-approve/",
    "/api/v1/ai//api/v1//internal/scenario-approve",
])
async def test_path_variants_do_not_bypass(client, db, roles, upstream, path):
    await _user(db, roles, "u_var", "exp_dir")
    headers = await _token(client, "u_var")
    resp = await client.post(path, headers=headers, json={"scenario_id": str(uuid.uuid4())})
    assert resp.status_code == 403
    assert upstream == []


def test_dot_segments_rejected():
    with pytest.raises(Exception) as exc:
        ai_proxy._path_parts("api/v1/../internal/scenario-approve")
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_akim_ray_task_list_scoped_to_own_district(client, db, roles, upstream):
    own = await _region(db, "R-OWN")
    await _user(db, roles, "u_ray_list", "akim_ray", region_id=own.id)
    headers = await _token(client, "u_ray_list")

    resp = await client.get("/api/v1/ai/api/v1/tasks", headers=headers)
    assert resp.status_code == 200
    assert upstream[-1].url.params.get("region_id") == str(own.id)

    other = await _region(db, "R-OTHER")
    resp = await client.get(f"/api/v1/ai/api/v1/tasks?region_id={other.id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_summary_denied_for_region_scoped_user(client, db, roles, upstream):
    """Вывод по вкладке строится по всем районам — akim_ray его не получает."""
    own = await _region(db, "R-SUM")
    await _user(db, roles, "u_ray_sum", "akim_ray", region_id=own.id)
    await _user(db, roles, "u_obl_sum", "akim_obl")
    body = {"page_key": "economy:resources", "title": "t", "direction_code": "economy", "indicator_ids": []}
    url = "/api/v1/ai/api/v1/dashboard-summaries"

    resp = await client.post(url, json=body, headers=await _token(client, "u_ray_sum"))
    assert resp.status_code == 403
    assert upstream == []

    resp = await client.post(url, json=body, headers=await _token(client, "u_obl_sum"))
    assert resp.status_code == 200
    assert len(upstream) == 1


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client, upstream):
    resp = await client.post(APPROVE, json={})
    assert resp.status_code in (401, 403)
    assert upstream == []


# ── Контур управления развитием: роли на сервере, статусы задач, аудит ────────

SCENARIOS = "/api/v1/ai/api/v1/scenarios"
TASKS = "/api/v1/ai/api/v1/tasks"


@pytest.fixture
async def task_tables(db):
    """Таблицы задач и сценариев живут в ai-service; для проверок прокси хватает
    колонок, по которым он проверяет район и утверждённость сценария."""
    from sqlalchemy import text
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS development_scenarios (id uuid PRIMARY KEY, approved_at timestamptz)"
    ))
    await db.execute(text(
        "CREATE TABLE IF NOT EXISTS regional_tasks (id uuid PRIMARY KEY, region_id uuid, scenario_id uuid)"
    ))
    await db.execute(text("ALTER TABLE development_scenarios ADD COLUMN IF NOT EXISTS status varchar(20)"))
    await db.execute(text("DELETE FROM regional_tasks"))
    await db.execute(text("DELETE FROM development_scenarios"))
    await db.commit()

    async def add(region_id, approved: bool) -> str:
        sid, tid = uuid.uuid4(), uuid.uuid4()
        await db.execute(text("INSERT INTO development_scenarios (id, approved_at, status) "
                              "VALUES (:s, CASE WHEN :a THEN now() END, CASE WHEN :a THEN 'approved' ELSE 'calculated' END)"),
                         {"s": sid, "a": approved})
        await db.execute(text("INSERT INTO regional_tasks VALUES (:t, :r, :s)"),
                         {"t": tid, "r": region_id, "s": sid})
        await db.commit()
        return str(tid)

    return add


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["akim_obl", "akim_ray", "exp_ver", "data_uploader"])
async def test_scenario_generate_only_experts(client, db, roles, upstream, role):
    region = await _region(db, f"G{role[:3]}")
    await _user(db, roles, f"g_{role}", role, region.id if role == "akim_ray" else None)
    headers = await _token(client, f"g_{role}")
    resp = await client.post("/api/v1/ai/api/v1/scenarios/generate", headers=headers,
                             json={"region_id": str(uuid.uuid4()), "target_year": 2030})
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
async def test_scenario_generate_by_expert_author_is_current_user(client, db, roles, upstream):
    user = await _user(db, roles, "g_dir", "exp_dir")
    headers = await _token(client, "g_dir")
    resp = await client.post("/api/v1/ai/api/v1/scenarios/generate", headers=headers,
                             json={"region_id": str(uuid.uuid4()), "target_year": 2030,
                                   "created_by": str(uuid.uuid4())})
    assert resp.status_code == 200
    assert json.loads(upstream[0].content)["created_by"] == str(user.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", TASKS), ("PATCH", f"{TASKS}/{uuid.uuid4()}"), ("DELETE", f"{TASKS}/{uuid.uuid4()}"),
])
async def test_task_editing_forbidden_for_verifier(client, db, roles, upstream, method, path):
    await _user(db, roles, "t_ver", "exp_ver")
    headers = await _token(client, "t_ver")
    resp = await client.request(method, path, headers=headers, json={"title": "x"})
    assert resp.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", TASKS), ("PATCH", f"{TASKS}/{uuid.uuid4()}"),
    ("PATCH", f"/api/v1/ai/api/v1/scenarios/{uuid.uuid4()}/parameters"),
    ("POST", f"/api/v1/ai/api/v1/scenarios/{uuid.uuid4()}/calculate"),
])
async def test_akim_obl_manages_tasks_and_parameters(client, db, roles, upstream, method, path):
    # ТЗ, Приложение 2, ч. 3: задачи вручную, сроки и параметры до утверждения — акимат области.
    await _user(db, roles, "t_obl", "akim_obl")
    headers = await _token(client, "t_obl")
    resp = await client.request(method, path, headers=headers, json={"title": "x"})
    assert resp.status_code == 200
    assert len(upstream) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "akim_obl", "exp_dir"])
async def test_status_accepted_only_through_acceptance(client, db, roles, upstream, role):
    await _user(db, roles, f"s_{role}", role)
    headers = await _token(client, f"s_{role}")
    resp = await client.put(f"{TASKS}/{uuid.uuid4()}/status", headers=headers, json={"status": "accepted"})
    assert resp.status_code == 403
    assert upstream == []
    patched = await client.patch(f"{TASKS}/{uuid.uuid4()}", headers=headers, json={"status": "accepted"})
    assert patched.status_code == 403
    assert upstream == []


@pytest.mark.asyncio
async def test_akim_ray_status_rules(client, db, roles, upstream, task_tables):
    region = await _region(db, "SR1")
    await _user(db, roles, "s_ray", "akim_ray", region.id)
    headers = await _token(client, "s_ray")
    task = await task_tables(region.id, approved=True)
    ok = await client.put(f"{TASKS}/{task}/status", headers=headers, json={"status": "in_progress"})
    assert ok.status_code == 200
    cancel = await client.put(f"{TASKS}/{task}/status", headers=headers, json={"status": "cancelled"})
    assert cancel.status_code == 403
    assert len(upstream) == 1


@pytest.mark.asyncio
async def test_akim_ray_has_no_access_to_draft_tasks(client, db, roles, upstream, task_tables):
    region = await _region(db, "SR2")
    await _user(db, roles, "d_ray", "akim_ray", region.id)
    headers = await _token(client, "d_ray")
    draft = await task_tables(region.id, approved=False)
    resp = await client.get(f"{TASKS}/{draft}", headers=headers)
    assert resp.status_code == 403
    listing = await client.get(TASKS, headers=headers)
    assert listing.status_code == 200
    assert upstream[-1].url.params["only_approved"] == "true"


@pytest.mark.asyncio
async def test_approval_written_to_audit_log(client, db, roles, upstream):
    from sqlalchemy import text
    user = await _user(db, roles, "a_obl", "akim_obl")
    headers = await _token(client, "a_obl")
    scenario_id = str(uuid.uuid4())
    resp = await client.post(APPROVE, headers=headers,
                             json={"scenario_id": scenario_id, "comment": "Утверждено на совещании"})
    assert resp.status_code == 200
    row = (await db.execute(text(
        "SELECT user_id, entity_id, details FROM audit_log WHERE action = 'scenario_approve'"
    ))).one()
    assert str(row[0]) == str(user.id) and str(row[1]) == scenario_id
    await db.commit()
    assert row[2]["comment"] == "Утверждено на совещании" and "approved_by" not in row[2]


@pytest.mark.asyncio
async def test_forbidden_action_not_audited(client, db, roles, upstream):
    from sqlalchemy import text
    await _user(db, roles, "f_ray", "exp_ver")
    headers = await _token(client, "f_ray")
    resp = await client.post(APPROVE, headers=headers, json={"scenario_id": str(uuid.uuid4())})
    assert resp.status_code == 403
    count = (await db.execute(text("SELECT count(*) FROM audit_log WHERE action LIKE 'scenario_%'"))).scalar()
    await db.commit()
    assert count == 0


@pytest.mark.asyncio
async def test_akim_ray_sees_only_approved_scenarios(client, db, roles, upstream, task_tables):
    from sqlalchemy import text
    region = await _region(db, "SC1")
    await _user(db, roles, "sc_ray", "akim_ray", region.id)
    headers = await _token(client, "sc_ray")
    await task_tables(region.id, approved=True)
    await task_tables(region.id, approved=False)
    approved = str((await db.execute(text("SELECT id FROM development_scenarios WHERE status='approved'"))).scalar())
    draft = str((await db.execute(text("SELECT id FROM development_scenarios WHERE status<>'approved'"))).scalar())
    await db.commit()

    lst = await client.get(f"{SCENARIOS}?status=calculated", headers=headers)
    assert lst.status_code == 200
    assert upstream[-1].url.params["status"] == "approved"

    assert (await client.get(f"{SCENARIOS}/{approved}", headers=headers)).status_code == 200
    assert (await client.get(f"{SCENARIOS}/{draft}", headers=headers)).status_code == 403
    assert (await client.get(f"{SCENARIOS}/compare?ids={approved},{draft}", headers=headers)).status_code == 403
    assert (await client.get(f"{SCENARIOS}/compare?ids={approved}", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_akim_obl_sees_all_scenarios(client, db, roles, upstream, task_tables):
    await _user(db, roles, "sc_obl", "akim_obl")
    headers = await _token(client, "sc_obl")
    lst = await client.get(f"{SCENARIOS}?status=calculated", headers=headers)
    assert lst.status_code == 200
    assert upstream[-1].url.params["status"] == "calculated"
