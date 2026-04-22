"""Tool definitions split by domain, merged into a single registry tuple."""

from __future__ import annotations

from noa_api.core.tools.definitions.common import COMMON_TOOLS
from noa_api.core.tools.definitions.proxmox import PROXMOX_TOOLS
from noa_api.core.tools.definitions.whm import WHM_TOOLS

ALL_TOOLS = COMMON_TOOLS + WHM_TOOLS + PROXMOX_TOOLS
