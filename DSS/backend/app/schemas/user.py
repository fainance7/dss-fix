from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserRoleOut(BaseModel):
    role_name: str
    region_id: Optional[UUID] = None
    region_name: Optional[str] = None
    assigned_at: datetime

    model_config = {"from_attributes": True}


class DirectionBrief(BaseModel):
    id: UUID
    code: str
    name: str


class UserOut(BaseModel):
    id: UUID
    username: str
    email: str
    iin: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    roles: list[UserRoleOut] = []
    directions: list[DirectionBrief] = []

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=100)
    is_active: bool = True
    iin: Optional[str] = Field(default=None, pattern=r'^\d{12}$')


class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=100)
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=100)
    iin: Optional[str] = Field(default=None, pattern=r'^\d{12}$')


class AssignRoleRequest(BaseModel):
    role_name: str
    region_id: Optional[UUID] = None


class AssignDirectionsRequest(BaseModel):
    direction_ids: list[UUID]  # пустой список = снять ограничения


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int
    page: int
    page_size: int
    pages: int
