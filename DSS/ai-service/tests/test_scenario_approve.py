"""Утверждение сценария: задачи районов не задваиваются.

Задачи создаются уже при генерации сценариев (предпросмотр «Задачи по
сценарию», эксперт может их править). Раньше утверждение генерировало их из
шаблонов повторно — у утверждённого сценария каждая задача была дважды, а
черновики отклонённых вариантов оставались у акимов в «Мероприятиях».
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.api.v1 import integration


def _result(*, one=None, many=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = one
    r.scalars.return_value.all.return_value = many or []
    return r


def _task(scenario_id, status="pending"):
    return SimpleNamespace(
        id=uuid4(), scenario_id=scenario_id, region_id=uuid4(), title="Задача", description=None,
        category=None, target_metric=None, target_value=None, unit=None, deadline=date(2030, 12, 31),
        status=status, assigned_to=None, completion_report=None,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        scenario_type="optimal", target_year=2030,
    )


def _db(results):
    db = AsyncMock()
    db.execute.side_effect = results
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_existing_draft_tasks_become_working_without_regeneration():
    scenario = SimpleNamespace(id=uuid4(), region_id=uuid4(), target_year=2030, status="calculated",
                               approved_by=None, approved_at=None)
    sibling = SimpleNamespace(id=uuid4(), status="calculated")
    sibling_draft = _task(sibling.id)
    own = [_task(scenario.id), _task(scenario.id)]
    db = _db([
        _result(one=scenario),              # сам сценарий
        _result(many=[]),                   # ранее утверждённые
        _result(many=[sibling]),            # варианты того же поколения
        _result(many=[sibling_draft]),      # их черновые задачи
        _result(many=own),                  # задачи утверждаемого сценария
    ])
    gen = AsyncMock()
    with patch("app.services.tasks.generate_tasks_from_scenario", gen), \
         patch.object(integration, "TaskResponse", MagicMock(model_validate=lambda t: t)), \
         patch.object(integration, "ScenarioApproveResponse", lambda **kw: SimpleNamespace(**kw)):
        resp = await integration.scenario_approve(
            integration.ScenarioApproveBody(scenario_id=scenario.id, approved_by=uuid4()), db=db, _key="k",
        )
    gen.assert_not_called()
    assert resp.tasks_count == 2
    assert scenario.status == "approved" and sibling.status == "archived"
    assert sibling_draft.status == "cancelled"


@pytest.mark.asyncio
async def test_generates_tasks_when_scenario_has_none():
    scenario = SimpleNamespace(id=uuid4(), region_id=uuid4(), target_year=2030, status="calculated",
                               approved_by=None, approved_at=None)
    db = _db([_result(one=scenario), _result(many=[]), _result(many=[]), _result(many=[])])
    generated = [_task(scenario.id)]
    with patch("app.services.tasks.generate_tasks_from_scenario", AsyncMock(return_value=generated)), \
         patch.object(integration, "TaskResponse", MagicMock(model_validate=lambda t: t)), \
         patch.object(integration, "ScenarioApproveResponse", lambda **kw: SimpleNamespace(**kw)):
        resp = await integration.scenario_approve(
            integration.ScenarioApproveBody(scenario_id=scenario.id, approved_by=uuid4()), db=db, _key="k",
        )
    assert resp.tasks_count == 1


@pytest.mark.asyncio
async def test_approval_comment_saved_in_parameters():
    scenario = SimpleNamespace(id=uuid4(), region_id=uuid4(), target_year=2030, status="calculated",
                               approved_by=None, approved_at=None, parameters={"scenario_params": {}})
    db = _db([_result(one=scenario), _result(many=[]), _result(many=[]), _result(many=[_task(scenario.id)])])
    with patch.object(integration, "flag_modified", lambda *a: None), \
         patch.object(integration, "TaskResponse", MagicMock(model_validate=lambda t: t)), \
         patch.object(integration, "ScenarioApproveResponse", lambda **kw: SimpleNamespace(**kw)):
        await integration.scenario_approve(
            integration.ScenarioApproveBody(scenario_id=scenario.id, approved_by=uuid4(),
                                            comment="  Утверждено на совещании  "),
            db=db, _key="k",
        )
    assert scenario.parameters["approval_comment"] == "Утверждено на совещании"
    assert scenario.parameters["scenario_params"] == {}


@pytest.mark.asyncio
async def test_approver_name_saved_for_scenario_card():
    """ФFE.4: в карточке утверждённого сценария показывается, кто утвердил."""
    scenario = SimpleNamespace(id=uuid4(), region_id=uuid4(), target_year=2030, status="calculated",
                               approved_by=None, approved_at=None, parameters={})
    db = _db([_result(one=scenario), _result(many=[]), _result(many=[]), _result(many=[_task(scenario.id)])])
    with patch.object(integration, "flag_modified", lambda *a: None), \
         patch.object(integration, "TaskResponse", MagicMock(model_validate=lambda t: t)), \
         patch.object(integration, "ScenarioApproveResponse", lambda **kw: SimpleNamespace(**kw)):
        await integration.scenario_approve(
            integration.ScenarioApproveBody(scenario_id=scenario.id, approved_by=uuid4(),
                                            approved_by_name="demo_akim_obl"),
            db=db, _key="k",
        )
    assert scenario.parameters["approved_by_name"] == "demo_akim_obl"
    assert scenario.approved_at is not None


@pytest.mark.asyncio
async def test_task_list_only_approved_filters_drafts():
    from app.api.v1 import tasks as tasks_api
    approved_sid, draft_sid = uuid4(), uuid4()
    items = [_task(approved_sid), _task(draft_sid)]
    db = _db([_result(many=[approved_sid])])  # запрос утверждённых сценариев
    with patch.object(tasks_api, "get_tasks_by_region", AsyncMock(return_value=items)), \
         patch.object(tasks_api, "_enrich_with_scenarios", AsyncMock(side_effect=lambda _db, ts: ts)):
        resp = await tasks_api.list_tasks(region_id=uuid4(), scenario_id=None, task_status=None,
                                          only_approved=True, db=db)
    assert [t.scenario_id for t in resp.items] == [approved_sid]
