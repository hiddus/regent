"""ProjectAgentSession — durable chassis binding dialogue + AgentRunner.

This is NOT a third Agent loop. Execution stays in ``AgentRunner``; user
dialogue stays in ``AppGuidanceService``. The session row holds identity,
workspace authority, checkpoint, and epoch for cross-Run resume.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from regent.config import get_settings
from regent.domain.errors import DomainError, ErrorCode
from regent.infrastructure.models import (
    AppProjectModel,
    GoalModel,
    ProjectAgentSessionModel,
)

SESSION_STATUS_ACTIVE = "ACTIVE"
SESSION_STATUS_PAUSED = "PAUSED"
SESSION_STATUS_STOPPED = "STOPPED"

_META_SESSION_ID = "project_agent_session_id"
_META_SESSION_EPOCH = "project_agent_session_epoch"
_META_SESSION_WORKSPACE = "project_agent_session_workspace_uri"


@dataclass(frozen=True, slots=True)
class ProjectAgentSessionView:
    id: uuid.UUID
    app_project_id: uuid.UUID
    goal_id: uuid.UUID
    status: str
    workspace_uri: str
    epoch: int
    version: int
    last_generation_run_id: uuid.UUID | None
    checkpoint: dict[str, Any]


def default_session_workspace_uri(
    app_project_id: uuid.UUID, *, workspace_root: str | Path | None = None
) -> str:
    root = Path(workspace_root if workspace_root is not None else get_settings().workspace_root)
    return str((root / "projects" / str(app_project_id) / "agent").resolve())


def _to_view(row: ProjectAgentSessionModel) -> ProjectAgentSessionView:
    return ProjectAgentSessionView(
        id=row.id,
        app_project_id=row.app_project_id,
        goal_id=row.goal_id,
        status=row.status,
        workspace_uri=row.workspace_uri,
        epoch=int(row.epoch or 0),
        version=int(row.version or 0),
        last_generation_run_id=row.last_generation_run_id,
        checkpoint=dict(row.checkpoint_json or {}),
    )


class ProjectAgentSessionService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        workspace_root: str | Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._workspace_root = workspace_root

    async def get_active_in(
        self, session: AsyncSession, app_project_id: uuid.UUID
    ) -> ProjectAgentSessionView | None:
        row = await self._load_active(session, app_project_id)
        return _to_view(row) if row is not None else None

    async def bump_epoch_in(
        self,
        session: AsyncSession,
        app_project_id: uuid.UUID,
        *,
        checkpoint_patch: dict[str, Any] | None = None,
        last_generation_run_id: uuid.UUID | None = None,
    ) -> ProjectAgentSessionView:
        row = await self._require_active_row(session, app_project_id)
        row.epoch = int(row.epoch or 0) + 1
        row.version = int(row.version or 0) + 1
        if checkpoint_patch:
            merged = dict(row.checkpoint_json or {})
            merged.update(checkpoint_patch)
            row.checkpoint_json = merged
        if last_generation_run_id is not None:
            row.last_generation_run_id = last_generation_run_id
        goal = await session.get(GoalModel, row.goal_id)
        if goal is not None:
            self._stamp_goal_metadata(goal, row)
        await session.flush()
        return _to_view(row)

    async def bind_generation_run(
        self,
        app_project_id: uuid.UUID,
        *,
        generation_run_id: uuid.UUID,
    ) -> ProjectAgentSessionView | None:
        """Record the lease GenerationRun on the active session (no epoch bump)."""
        async with self._sessions() as session, session.begin():
            row = await self._load_active(session, app_project_id)
            if row is None:
                return None
            row.last_generation_run_id = generation_run_id
            row.version = int(row.version or 0) + 1
            goal = await session.get(GoalModel, row.goal_id)
            if goal is not None:
                self._stamp_goal_metadata(goal, row)
            await session.flush()
            return _to_view(row)

    async def assert_resume_epoch(
        self,
        app_project_id: uuid.UUID,
        *,
        session_id: uuid.UUID | str,
        epoch: int,
    ) -> ProjectAgentSessionView:
        """Reject stale SESSION_RESUME outbox events (epoch behind active session)."""
        view = await self.require_active(app_project_id)
        if str(view.id) != str(session_id):
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"session resume target mismatch: expected {view.id}, got {session_id}",
            )
        if int(epoch) < int(view.epoch):
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"stale session resume epoch {epoch} < active {view.epoch}",
            )
        return view

    async def get_active(
        self, app_project_id: uuid.UUID
    ) -> ProjectAgentSessionView | None:
        async with self._sessions() as session:
            row = await self._load_active(session, app_project_id)
            return _to_view(row) if row is not None else None

    async def require_active(
        self, app_project_id: uuid.UUID
    ) -> ProjectAgentSessionView:
        view = await self.get_active(app_project_id)
        if view is None:
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"ACTIVE project {app_project_id} has no ProjectAgentSession",
            )
        return view

    async def ensure_active_session(
        self,
        *,
        app_project_id: uuid.UUID,
        goal_id: uuid.UUID,
        actor: str = "regent-core",
        workspace_uri: str | None = None,
    ) -> ProjectAgentSessionView:
        """Create or rebind the single ACTIVE session for a project.

        Safe to call inside an open transaction via ``ensure_active_session_in``.
        """
        async with self._sessions() as session, session.begin():
            return await self.ensure_active_session_in(
                session,
                app_project_id=app_project_id,
                goal_id=goal_id,
                actor=actor,
                workspace_uri=workspace_uri,
            )

    async def ensure_active_session_in(
        self,
        session: AsyncSession,
        *,
        app_project_id: uuid.UUID,
        goal_id: uuid.UUID,
        actor: str = "regent-core",
        workspace_uri: str | None = None,
    ) -> ProjectAgentSessionView:
        project = await session.get(AppProjectModel, app_project_id)
        goal = await session.get(GoalModel, goal_id)
        if project is None or goal is None:
            raise DomainError(ErrorCode.NOT_FOUND, "project or goal not found for session")
        if goal.app_project_id is not None and goal.app_project_id != app_project_id:
            raise DomainError(
                ErrorCode.INVALID_STATE,
                "goal does not belong to app_project for session ensure",
            )

        existing = await self._load_active(session, app_project_id)
        uri = workspace_uri or default_session_workspace_uri(
            app_project_id, workspace_root=self._workspace_root
        )
        try:
            Path(uri).mkdir(parents=True, exist_ok=True)
        except OSError:
            # Persist the authority URI even if the host cannot create it yet
            # (e.g. unit tests without the production workspace mount).
            pass

        if existing is not None:
            if existing.goal_id != goal_id:
                existing.goal_id = goal_id
                existing.version = int(existing.version or 0) + 1
            if not existing.workspace_uri:
                existing.workspace_uri = uri
            self._stamp_goal_metadata(goal, existing)
            await session.flush()
            return _to_view(existing)

        row = ProjectAgentSessionModel(
            id=uuid.uuid4(),
            app_project_id=app_project_id,
            goal_id=goal_id,
            status=SESSION_STATUS_ACTIVE,
            workspace_uri=uri,
            checkpoint_json={},
            epoch=0,
            version=0,
            created_by=actor,
        )
        session.add(row)
        self._stamp_goal_metadata(goal, row)
        await session.flush()
        return _to_view(row)

    async def pause(
        self, app_project_id: uuid.UUID, *, actor: str = "regent-core"
    ) -> ProjectAgentSessionView:
        async with self._sessions() as session, session.begin():
            row = await self._require_active_row(session, app_project_id)
            row.status = SESSION_STATUS_PAUSED
            row.version = int(row.version or 0) + 1
            goal = await session.get(GoalModel, row.goal_id)
            if goal is not None:
                self._stamp_goal_metadata(goal, row)
            await session.flush()
            return _to_view(row)

    async def stop(
        self, app_project_id: uuid.UUID, *, actor: str = "regent-core"
    ) -> ProjectAgentSessionView:
        async with self._sessions() as session, session.begin():
            row = await self._require_active_row(session, app_project_id)
            row.status = SESSION_STATUS_STOPPED
            row.version = int(row.version or 0) + 1
            goal = await session.get(GoalModel, row.goal_id)
            if goal is not None:
                meta = dict(goal.metadata_json or {})
                meta.pop(_META_SESSION_ID, None)
                meta.pop(_META_SESSION_EPOCH, None)
                meta.pop(_META_SESSION_WORKSPACE, None)
                goal.metadata_json = meta
            await session.flush()
            return _to_view(row)

    async def bump_epoch(
        self,
        app_project_id: uuid.UUID,
        *,
        checkpoint_patch: dict[str, Any] | None = None,
        last_generation_run_id: uuid.UUID | None = None,
    ) -> ProjectAgentSessionView:
        async with self._sessions() as session, session.begin():
            row = await self._require_active_row(session, app_project_id)
            row.epoch = int(row.epoch or 0) + 1
            row.version = int(row.version or 0) + 1
            if checkpoint_patch:
                merged = dict(row.checkpoint_json or {})
                merged.update(checkpoint_patch)
                row.checkpoint_json = merged
            if last_generation_run_id is not None:
                row.last_generation_run_id = last_generation_run_id
            goal = await session.get(GoalModel, row.goal_id)
            if goal is not None:
                self._stamp_goal_metadata(goal, row)
            await session.flush()
            return _to_view(row)

    async def resume_from_paused(
        self, app_project_id: uuid.UUID, *, goal_id: uuid.UUID | None = None
    ) -> ProjectAgentSessionView:
        """Re-activate a PAUSED session (or ensure ACTIVE if already running)."""
        async with self._sessions() as session, session.begin():
            active = await self._load_active(session, app_project_id)
            if active is not None:
                if goal_id is not None and active.goal_id != goal_id:
                    active.goal_id = goal_id
                    active.version = int(active.version or 0) + 1
                goal = await session.get(GoalModel, active.goal_id)
                if goal is not None:
                    self._stamp_goal_metadata(goal, active)
                await session.flush()
                return _to_view(active)

            paused = await session.scalar(
                select(ProjectAgentSessionModel)
                .where(
                    ProjectAgentSessionModel.app_project_id == app_project_id,
                    ProjectAgentSessionModel.status == SESSION_STATUS_PAUSED,
                )
                .order_by(ProjectAgentSessionModel.updated_at.desc())
                .limit(1)
            )
            if paused is None:
                raise DomainError(
                    ErrorCode.INVALID_STATE,
                    f"no PAUSED/ACTIVE ProjectAgentSession for project {app_project_id}",
                )
            paused.status = SESSION_STATUS_ACTIVE
            paused.epoch = int(paused.epoch or 0) + 1
            paused.version = int(paused.version or 0) + 1
            if goal_id is not None:
                paused.goal_id = goal_id
            goal = await session.get(GoalModel, paused.goal_id)
            if goal is not None:
                self._stamp_goal_metadata(goal, paused)
            await session.flush()
            return _to_view(paused)

    @staticmethod
    def _stamp_goal_metadata(goal: GoalModel, row: ProjectAgentSessionModel) -> None:
        meta = dict(goal.metadata_json or {})
        meta[_META_SESSION_ID] = str(row.id)
        meta[_META_SESSION_EPOCH] = int(row.epoch or 0)
        meta[_META_SESSION_WORKSPACE] = row.workspace_uri
        goal.metadata_json = meta

    @staticmethod
    async def _load_active(
        session: AsyncSession, app_project_id: uuid.UUID
    ) -> ProjectAgentSessionModel | None:
        row = await session.scalar(
            select(ProjectAgentSessionModel)
            .where(
                ProjectAgentSessionModel.app_project_id == app_project_id,
                ProjectAgentSessionModel.status == SESSION_STATUS_ACTIVE,
            )
            .limit(1)
        )
        # MagicMock / unrelated ORM rows must not look like a real session
        # (delivery-gap unit tests use AsyncMock sessions).
        if not isinstance(row, ProjectAgentSessionModel):
            return None
        return row

    async def _require_active_row(
        self, session: AsyncSession, app_project_id: uuid.UUID
    ) -> ProjectAgentSessionModel:
        row = await self._load_active(session, app_project_id)
        if row is None:
            raise DomainError(
                ErrorCode.INVALID_STATE,
                f"ACTIVE project {app_project_id} has no ProjectAgentSession",
            )
        return row
