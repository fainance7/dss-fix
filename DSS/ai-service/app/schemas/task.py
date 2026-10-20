from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TaskResponse(BaseModel):
    """Схема ответа для региональной задачи."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scenario_id: UUID
    region_id: UUID
    title: str
    description: str | None
    category: str | None
    target_metric: str | None
    target_value: Decimal | None
    unit: str | None
    deadline: date | None
    status: str
    assigned_to: UUID | None
    completion_report: str | None
    updated_at: datetime
    created_at: datetime
    # Обогащается на уровне API (не из ORM-атрибутов):
    scenario_type: str | None = None
    target_year: int | None = None


class TaskCreate(BaseModel):
    """Схема для создания задачи."""
    scenario_id: UUID
    region_id: UUID
    title: str
    description: str | None = None
    category: str | None = None
    target_metric: str | None = None
    target_value: Decimal | None = None
    unit: str | None = None
    deadline: date | None = None
    status: str = "pending"
    assigned_to: UUID | None = None


class TaskUpdate(BaseModel):
    """Схема для обновления задачи."""
    title: str | None = None
    description: str | None = None
    category: str | None = None
    target_metric: str | None = None
    target_value: Decimal | None = None
    unit: str | None = None
    deadline: date | None = None
    status: str | None = None
    completion_report: str | None = None
    assigned_to: UUID | None = None
    region_id: UUID | None = None
    # Автор изменения — подставляет backend-прокси из учётной записи (L-3).
    author_id: str | None = None


class TaskStatusUpdate(BaseModel):
    """Схема для обновления статуса задачи."""
    status: str = Field(
        ...,
        description="Новый статус: pending | in_progress | completed | cancelled | accepted",
        pattern="^(pending|in_progress|completed|cancelled|accepted)$",
    )
    completion_report: str | None = Field(
        None,
        description="Отчёт о выполнении (обязателен при status=completed)",
    )
    # Автор изменения — подставляет backend-прокси из учётной записи (L-3).
    author_id: str | None = None


class TaskListResponse(BaseModel):
    """Схема ответа для списка задач."""
    items: list[TaskResponse]
    total: int


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    event_type: str
    old_status: str | None
    new_status: str | None
    comment: str | None
    author_id: str | None
    author_name: str | None
    created_at: datetime


class TaskCommentCreate(BaseModel):
    comment: str
    author_id: str | None = None
    author_name: str | None = None


class TaskAcceptBody(BaseModel):
    comment: str | None = None
    author_id: str | None = None
    author_name: str | None = None


class TaskRejectCompletionBody(BaseModel):
    comment: str
    author_id: str | None = None
    author_name: str | None = None
