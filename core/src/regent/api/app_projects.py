import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from regent.application.app_project_service import AppProjectService
from regent.application.delivery_review_api import DeliveryReviewQueryService
from regent.application.goal_execution_service import GoalExecutionService
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
    plan: dict[str, object] = Field(default_factory=dict)
    needs_user_fork: bool = False
    auto_started: bool = False


class ConfirmAppResponse(BaseModel):
    project: AppProjectResponse
    goal_id: uuid.UUID
    goal_status: str
    goal_spec_id: uuid.UUID
    goal_spec_version: int
    goal_spec_status: str
    goal_spec_hash: str


@router.post("/drafts", response_model=AppDraftResponse, status_code=status.HTTP_201_CREATED)
async def create_app_draft(
    payload: CreateAppDraftBody, projects: ServiceDep, request: Request
) -> AppDraftResponse:
    receipt = await projects.create_draft(idea=payload.idea, actor=payload.actor)
    auto_started = False
    # run-think-learn L2: when model cannot self-consistently deduce a path,
    # wait for user fork selection before auto-start (human as auxiliary).
    if not receipt.needs_user_fork:
        started = await GoalExecutionService(request.app.state.sessions).start(
            receipt.goal.id,
            actor=payload.actor,
            idempotency_key=f"auto-start:{receipt.goal.id}",
        )
        auto_started = True
        goal_status = started.status
    else:
        goal_status = receipt.goal.status
    return AppDraftResponse(
        project=project_response(receipt.project),
        conversation_id=receipt.conversation.id,
        goal_id=receipt.goal.id,
        goal_status=goal_status,
        goal_spec_id=receipt.spec.id,
        goal_spec_version=receipt.spec.version,
        goal_spec_status=receipt.spec.status,
        goal_spec_hash=receipt.spec.content_hash,
        understanding=receipt.understanding.model_dump(mode="json"),
        model=receipt.model,
        plan=dict(receipt.runtime_plan or {}),
        needs_user_fork=bool(receipt.needs_user_fork),
        auto_started=auto_started,
    )


@router.get("", response_model=list[AppProjectResponse])
async def list_app_projects(
    projects: ServiceDep, limit: int = Query(default=100, ge=1, le=200)
) -> list[AppProjectResponse]:
    return [project_response(item) for item in await projects.list_projects(limit=limit)]


@router.get("/{project_id}", response_model=AppProjectResponse)
async def get_app_project(project_id: uuid.UUID, projects: ServiceDep) -> AppProjectResponse:
    return project_response(await projects.get(project_id))


@router.get("/{project_id}/delivery-review")
async def get_delivery_review(project_id: uuid.UUID, request: Request) -> dict[str, Any]:
    """CD-3.1: read-only plan / transcript / verification / budget for Console review."""
    return await DeliveryReviewQueryService(request.app.state.sessions).get_for_project(project_id)


@router.get("/{project_id}/workspace/tree")
async def workspace_tree(project_id: uuid.UUID, request: Request) -> dict[str, Any]:
    from fastapi import HTTPException

    from regent.application.workspace_browser import list_tree, resolve_project_workspace

    root = await resolve_project_workspace(request.app.state.sessions, project_id)
    if root is None:
        raise HTTPException(status_code=404, detail="No workspace found for this project")
    return {"root": str(root), "entries": list_tree(root)}


@router.get("/{project_id}/workspace/file")
async def workspace_file(
    project_id: uuid.UUID,
    request: Request,
    path: str = Query(..., min_length=1),
) -> dict[str, Any]:
    from fastapi import HTTPException

    from regent.application.workspace_browser import read_text_file, resolve_project_workspace

    root = await resolve_project_workspace(request.app.state.sessions, project_id)
    if root is None:
        raise HTTPException(status_code=404, detail="No workspace found for this project")
    try:
        return read_text_file(root, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_id}/workspace/diff")
async def workspace_diff(
    project_id: uuid.UUID,
    request: Request,
    from_snap: str | None = Query(default=None, alias="from"),
    to_snap: str | None = Query(default=None, alias="to"),
) -> dict[str, Any]:
    from fastapi import HTTPException

    from regent.application.workspace_browser import diff_trees, resolve_project_workspace
    from regent.config import get_settings

    settings = get_settings()
    snap_root = Path(settings.workspace_root) / "accepted_workspace_snapshots"
    if from_snap and to_snap:
        a = (snap_root / from_snap).resolve()
        b = (snap_root / to_snap).resolve()
        if not a.is_dir() or not b.is_dir():
            raise HTTPException(status_code=404, detail="snapshot not found")
        return {"from": from_snap, "to": to_snap, "patch": diff_trees(a, b)}

    root = await resolve_project_workspace(request.app.state.sessions, project_id)
    if root is None:
        raise HTTPException(status_code=404, detail="No workspace found for this project")
    # Without explicit snapshots, return empty patch with current root marker.
    return {"from": from_snap or "", "to": to_snap or str(root), "patch": ""}


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
