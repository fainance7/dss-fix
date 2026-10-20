from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai_models import DevelopmentScenario, RegionalTask, TaskEvent
from app.schemas.task import (
    TaskAcceptBody,
    TaskCommentCreate,
    TaskCreate,
    TaskEventResponse,
    TaskListResponse,
    TaskRejectCompletionBody,
    TaskResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from app.services.tasks import (
    generate_tasks_from_scenario,
    get_tasks_by_region,
    get_tasks_by_scenario,
    update_task_status,
)

router = APIRouter(tags=["tasks"])


async def _get_task_or_404(db: AsyncSession, task_id: UUID) -> RegionalTask:
    result = await db.execute(select(RegionalTask).where(RegionalTask.id == task_id))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Задача {task_id} не найдена")
    return obj


async def _enrich_with_scenarios(db: AsyncSession, tasks: list[RegionalTask]) -> list[TaskResponse]:
    """Обогащает TaskResponse данными сценария (scenario_type, target_year)."""
    if not tasks:
        return []
    scenario_ids = list({t.scenario_id for t in tasks})
    sc_result = await db.execute(
        select(DevelopmentScenario).where(DevelopmentScenario.id.in_(scenario_ids))
    )
    scenario_map = {s.id: s for s in sc_result.scalars().all()}
    items = []
    for t in tasks:
        resp = TaskResponse.model_validate(t)
        sc = scenario_map.get(t.scenario_id)
        if sc:
            resp.scenario_type = sc.scenario_type
            resp.target_year = sc.target_year
        items.append(resp)
    return items


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    region_id: UUID | None = Query(None),
    scenario_id: UUID | None = Query(None),
    task_status: str | None = Query(None, alias="status"),
    only_approved: bool = Query(False, description="Только задачи утверждённых сценариев"),
    db: AsyncSession = Depends(get_db),
):
    """Список задач с фильтрацией по региону, сценарию и статусу.

    `only_approved` оставляет задачи утверждённых сценариев: черновые задачи
    создаются при генерации для предпросмотра и исполнителям не адресованы.
    Для акима района прокси бэкенда включает фильтр принудительно."""
    if region_id is not None:
        tasks = await get_tasks_by_region(db, region_id=region_id, status=task_status)
    elif scenario_id is not None:
        tasks = await get_tasks_by_scenario(db, scenario_id=scenario_id)
        if task_status:
            tasks = [t for t in tasks if t.status == task_status]
    else:
        q = select(RegionalTask)
        if task_status:
            q = q.where(RegionalTask.status == task_status)
        result = await db.execute(q.order_by(RegionalTask.created_at.desc()))
        tasks = list(result.scalars().all())

    if only_approved and tasks:
        approved = set((await db.execute(
            select(DevelopmentScenario.id).where(
                DevelopmentScenario.id.in_({t.scenario_id for t in tasks}),
                DevelopmentScenario.approved_at.is_not(None),
            )
        )).scalars().all())
        tasks = [t for t in tasks if t.scenario_id in approved]

    items = await _enrich_with_scenarios(db, tasks)
    return TaskListResponse(items=items, total=len(items))


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, db: AsyncSession = Depends(get_db)):
    task = await _get_task_or_404(db, task_id)
    enriched = await _enrich_with_scenarios(db, [task])
    return enriched[0]


@router.post("/scenarios/{scenario_id}/tasks/generate", response_model=TaskListResponse, status_code=status.HTTP_201_CREATED)
async def generate_tasks(scenario_id: UUID, db: AsyncSession = Depends(get_db)):
    """Генерирует задачи из утверждённого сценария."""
    try:
        tasks = await generate_tasks_from_scenario(db, scenario_id)
        await db.commit()
        for t in tasks:
            await db.refresh(t)
        return TaskListResponse(items=[TaskResponse.model_validate(t) for t in tasks], total=len(tasks))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, db: AsyncSession = Depends(get_db)):
    """Создаёт новую задачу для сценария."""
    task = RegionalTask(
        scenario_id=body.scenario_id,
        region_id=body.region_id,
        title=body.title,
        description=body.description,
        category=body.category,
        target_metric=body.target_metric,
        target_value=body.target_value,
        unit=body.unit,
        deadline=body.deadline,
        status=body.status,
        assigned_to=body.assigned_to,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task)
    await db.commit()
    await db.refresh(task)
    enriched = await _enrich_with_scenarios(db, [task])
    return enriched[0]


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(task_id: UUID, body: TaskUpdate, db: AsyncSession = Depends(get_db)):
    """Обновляет поля задачи."""
    task = await _get_task_or_404(db, task_id)
    old_status = task.status
    update = body.model_dump(exclude_none=True)
    author_id = update.pop("author_id", None)
    for field, value in update.items():
        setattr(task, field, value)
    if "status" in update and update["status"] != old_status:
        db.add(TaskEvent(
            task_id=task_id,
            event_type="status_change",
            old_status=old_status,
            new_status=update["status"],
            comment=update.get("completion_report"),
            author_id=author_id,
        ))
    await db.flush()
    await db.commit()
    await db.refresh(task)
    enriched = await _enrich_with_scenarios(db, [task])
    return enriched[0]


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: UUID, db: AsyncSession = Depends(get_db)):
    """Удаляет задачу."""
    task = await _get_task_or_404(db, task_id)
    await db.delete(task)
    await db.commit()


@router.put("/tasks/{task_id}/status", response_model=TaskResponse)
async def update_status(task_id: UUID, body: TaskStatusUpdate, db: AsyncSession = Depends(get_db)):
    """Обновляет статус задачи."""
    try:
        task = await _get_task_or_404(db, task_id)
        old_status = task.status
        task = await update_task_status(db, task_id=task_id, status=body.status, completion_report=body.completion_report)
        if old_status != body.status:
            db.add(TaskEvent(
                task_id=task_id,
                event_type="status_change",
                old_status=old_status,
                new_status=body.status,
                comment=body.completion_report,
                author_id=body.author_id,
            ))
        await db.commit()
        await db.refresh(task)
        enriched = await _enrich_with_scenarios(db, [task])
        return enriched[0]
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


# ──────────────────────────────── EVENTS / HISTORY ────────────────────────────────


@router.get("/tasks/{task_id}/events", response_model=list[TaskEventResponse])
async def list_task_events(task_id: UUID, db: AsyncSession = Depends(get_db)):
    """Возвращает хронологию событий задачи."""
    await _get_task_or_404(db, task_id)
    result = await db.execute(
        select(TaskEvent)
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.created_at)
    )
    return result.scalars().all()


@router.post("/tasks/{task_id}/comment", response_model=TaskEventResponse, status_code=status.HTTP_201_CREATED)
async def add_comment(task_id: UUID, body: TaskCommentCreate, db: AsyncSession = Depends(get_db)):
    """Добавляет комментарий к задаче без изменения статуса (для исполнителя)."""
    await _get_task_or_404(db, task_id)
    event = TaskEvent(
        task_id=task_id,
        event_type="comment",
        comment=body.comment,
        author_id=body.author_id,
        author_name=body.author_name,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


@router.post("/tasks/{task_id}/accept", response_model=TaskResponse)
async def accept_task(task_id: UUID, body: TaskAcceptBody, db: AsyncSession = Depends(get_db)):
    """Принимает выполненную задачу (akim_obl). Статус → accepted."""
    task = await _get_task_or_404(db, task_id)
    if task.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Принять можно только задачу в статусе 'completed'",
        )
    old_status = task.status
    task.status = "accepted"
    event = TaskEvent(
        task_id=task_id,
        event_type="accept",
        old_status=old_status,
        new_status="accepted",
        comment=body.comment,
        author_id=body.author_id,
        author_name=body.author_name,
    )
    db.add(event)
    await db.flush()
    await db.commit()
    await db.refresh(task)
    enriched = await _enrich_with_scenarios(db, [task])
    return enriched[0]


@router.post("/tasks/{task_id}/reject-completion", response_model=TaskResponse)
async def reject_completion(task_id: UUID, body: TaskRejectCompletionBody, db: AsyncSession = Depends(get_db)):
    """Отклоняет выполнение задачи (akim_obl), возвращает в работу."""
    task = await _get_task_or_404(db, task_id)
    if task.status not in ("completed", "accepted"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Отклонить можно только задачу в статусе 'completed' или 'accepted'",
        )
    old_status = task.status
    task.status = "in_progress"
    event = TaskEvent(
        task_id=task_id,
        event_type="reject_completion",
        old_status=old_status,
        new_status="in_progress",
        comment=body.comment,
        author_id=body.author_id,
        author_name=body.author_name,
    )
    db.add(event)
    await db.flush()
    await db.commit()
    await db.refresh(task)
    enriched = await _enrich_with_scenarios(db, [task])
    return enriched[0]
