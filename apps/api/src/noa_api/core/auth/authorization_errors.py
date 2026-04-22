from __future__ import annotations


class UnknownToolError(Exception):
    def __init__(self, unknown_tools: list[str]) -> None:
        self.unknown_tools = sorted(
            {name.strip() for name in unknown_tools if name.strip()}
        )
        super().__init__(f"Unknown tools: {', '.join(self.unknown_tools)}")


class LastActiveAdminError(Exception):
    pass


class SelfDeactivateAdminError(Exception):
    pass


class SelfDeleteAdminError(Exception):
    pass


class InvalidRoleNameError(Exception):
    pass


class ReservedRoleError(Exception):
    pass


class InternalRoleError(Exception):
    pass


class RoleNotFoundError(Exception):
    pass


class UnknownRoleError(Exception):
    def __init__(self, unknown_roles: list[str]) -> None:
        self.unknown_roles = sorted(
            {name.strip() for name in unknown_roles if name.strip()}
        )
        super().__init__(f"Unknown roles: {', '.join(self.unknown_roles)}")


class SelfRemoveAdminRoleError(Exception):
    pass
