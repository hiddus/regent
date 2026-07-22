import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from regent.application.app_project_service import AppProjectService
from regent.config import get_settings
from regent.model.factory import build_model_provider

router = APIRouter(prefix="/v1/app-projects", tags=["app-projects"])


def service(request: Request) -> AppProjectService:
    return AppProjectService(
        request.app.state.sessions,
        build_model_provider(get_settings()),
    )


ServiceDep = Annotated[AppProjectService, Depends(service)]


class CreateAppDraftBody(BaseModel):
    idea: str = Field(min_length=1, max_length=20_000)
    actor: str = Field(min_length=1, max_length=255)


class ConfirmAppBody(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    expected_spec_hash: str = Field(min_length=1, max_length=64)


class AppProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    product_intent: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class AppDraftResponse(BaseModel):
    project: AppProjectResponse
    conversation_id: uuid.UUID
    goal_id: uuid.UUID
    goal_status: str
    goal_spec_id: uuid.UUID
    goal_spec_version: int
    goal_spec_status: str
    goal_spec_hash: str
    understanding: dict[str, object]
    model: str


class ConfirmAppResponse(BaseModel):
    project: AppProjectResponse
    goal_id: uuid.UUID
    goal_status: str
    goal_spec_id: uuid.UUID
    goal_spec_version: int
    goal_spec_status: str
    goal_spec_hash: str


@router.post("/drafts", response_model=AppDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_app_draft(payload: CreateAppDraftBody, projects: ServiceDep) -> AppDraftResponse:
    receipt = await projects.create_draft(idea=payload.idea, actor=payload.actor)
    return AppDraftResponse(
        project=project_response(receipt.project),
        conversation_id=receipt.conversation.id,
        goal_id=receipt.goal.id,
        goal_status=receipt.goal.status,
        goal_spec_id=receipt.spec.id,
        goal_spec_version=receipt.spec.version,
        goal_spec_status=receipt.spec.status,
        goal_spec_hash=receipt.spec.content_hash,
        understanding=receipt.understanding.model_dump(mode="json"),
        model=receipt.model,
    )


@router.get("", response_model=list[AppProjectResponse])
async def list_app_projects(
    projects: ServiceDep, limit: int = Query(default=100, ge=1, le=200)
) -> list[AppProjectResponse]:
    return [project_response(item) for item in await projects.list_projects(limit=limit)]


@router.get("/{project_id}", response_model=AppProjectResponse)
async def get_app_project(project_id: uuid.UUID, projects: ServiceDep) -> AppProjectResponse:
    return project_response(await projects.get(project_id))


@router.post("/{project_id}/confirm", response_model=ConfirmAppResponse)
async def confirm_app_project(
    project_id: uuid.UUID,
    payload: ConfirmAppBody,
    projects: ServiceDep,
) -> ConfirmAppResponse:
    receipt = await projects.confirm(
        project_id,
        actor=payload.actor,
        expected_spec_hash=payload.expected_spec_hash,
    )
    return ConfirmAppResponse(
        project=project_response(receipt.project),
        goal_id=receipt.goal.id,
        goal_status=receipt.goal.status,
        goal_spec_id=receipt.spec.id,
        goal_spec_version=receipt.spec.version,
        goal_spec_status=receipt.spec.status,
        goal_spec_hash=receipt.spec.content_hash,
    )


def project_response(model: object) -> AppProjectResponse:
    return AppProjectResponse.model_validate(model, from_attributes=True)
