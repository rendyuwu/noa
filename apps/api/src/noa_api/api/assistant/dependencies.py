from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.api.assistant.assistant_repository import SQLAssistantRepository
from noa_api.api.assistant.service import AssistantService
from noa_api.core.agent.runner import AgentRunner, create_default_llm_client
from noa_api.core.auth.authorization import (
    AuthorizationService,
    SQLAuthorizationRepository,
)
from noa_api.core.config import get_app_settings
from noa_api.storage.postgres.action_tool_runs import (
    ActionToolRunService,
    SQLActionToolRunRepository,
)
from noa_api.storage.postgres.client import get_session_factory
from noa_api.storage.postgres.workflow_todos import (
    SQLWorkflowTodoRepository,
    WorkflowTodoService,
)


def _build_assistant_service(
    *, session: AsyncSession, app_settings: Any
) -> AssistantService:
    action_tool_run_service = ActionToolRunService(
        repository=SQLActionToolRunRepository(session)
    )
    return AssistantService(
        SQLAssistantRepository(session),
        AgentRunner(
            llm_client=create_default_llm_client(app_settings),
            action_tool_run_service=action_tool_run_service,
            session=session,
        ),
        action_tool_run_service=action_tool_run_service,
        workflow_todo_service=WorkflowTodoService(
            repository=SQLWorkflowTodoRepository(session)
        ),
        session=session,
    )


def _build_authorization_service(*, session: AsyncSession) -> AuthorizationService:
    return AuthorizationService(repository=SQLAuthorizationRepository(session))


async def get_assistant_service(
    request: Request,
) -> AsyncGenerator[AssistantService, None]:
    app_settings = get_app_settings(request.app)
    async with get_session_factory()() as session:
        service = _build_assistant_service(session=session, app_settings=app_settings)
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise
