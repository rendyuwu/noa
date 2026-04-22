from __future__ import annotations

from noa_api.core.workflows.whm.account_lifecycle import (
    WHMAccountLifecycleTemplate,
)
from noa_api.core.workflows.whm.contact_email import (
    WHMAccountContactEmailTemplate,
)
from noa_api.core.workflows.whm.primary_domain import (
    WHMAccountPrimaryDomainTemplate,
)
from noa_api.core.workflows.whm.firewall import WHMFirewallBatchTemplate
from noa_api.core.workflows.types import WorkflowTemplate

WORKFLOW_TEMPLATES: dict[str, WorkflowTemplate] = {
    "whm-account-lifecycle": WHMAccountLifecycleTemplate(),
    "whm-account-contact-email": WHMAccountContactEmailTemplate(),
    "whm-account-primary-domain": WHMAccountPrimaryDomainTemplate(),
    "whm-firewall-batch-change": WHMFirewallBatchTemplate(),
}

__all__ = [
    "WORKFLOW_TEMPLATES",
    "WHMAccountLifecycleTemplate",
    "WHMAccountContactEmailTemplate",
    "WHMAccountPrimaryDomainTemplate",
    "WHMFirewallBatchTemplate",
]
