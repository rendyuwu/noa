from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.tool_error_sanitizer import SanitizedToolError
from noa_api.core.workflows.types import (
    WorkflowTemplateContext,
    WorkflowTemplate,
    normalized_text,
)
from noa_api.core.workflows.whm.common import _extract_before_state
from noa_api.core.workflows.whm.matching import _require_account_preflight
from noa_api.whm.tools.preflight_tools import whm_preflight_account


class _WHMTemplate(WorkflowTemplate):
    def build_before_state(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        preflight_results: list[dict[str, object]],
    ) -> list[dict[str, str]] | None:
        context = WorkflowTemplateContext(
            tool_name=tool_name,
            args=args,
            phase="waiting_on_approval",
            preflight_evidence=[
                {
                    "toolName": item.get("toolName"),
                    "result": item.get("result"),
                }
                for item in preflight_results
                if isinstance(item, dict)
            ],
        )
        evidence_template = self.build_evidence_template(context)
        if evidence_template is not None:
            for section in evidence_template.sections:
                if section.key != "before_state":
                    continue
                return [
                    {"label": item.label, "value": item.value}
                    for item in section.items
                    if item.label.strip() and item.value.strip()
                ]
        return _extract_before_state(preflight_results)


class _WHMAccountTemplate(_WHMTemplate):
    def require_preflight(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        working_messages: list[dict[str, object]],
        requested_server_id: str | None,
    ) -> SanitizedToolError | None:
        _ = tool_name
        return _require_account_preflight(
            args=args,
            working_messages=working_messages,
            requested_server_id=requested_server_id,
        )

    async def fetch_postflight_result(
        self,
        *,
        tool_name: str,
        args: dict[str, object],
        session: AsyncSession,
    ) -> dict[str, object] | None:
        _ = tool_name
        server_ref = normalized_text(args.get("server_ref"))
        username = normalized_text(args.get("username"))
        if server_ref is None or username is None:
            return None
        result = await whm_preflight_account(
            session=session,
            server_ref=server_ref,
            username=username,
        )
        return result if isinstance(result, dict) else None
