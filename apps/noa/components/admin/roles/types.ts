export type AdminRolesResponse = {
  roles: Array<{ name: string }> | string[];
};

export type AdminToolsResponse = {
  tools: string[];
};

export type AdminRoleToolsResponse = {
  tools: string[];
};

export type DirectGrantsMigrationResponse = Record<string, unknown>;
