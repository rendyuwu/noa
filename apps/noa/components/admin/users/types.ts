export type AdminUser = {
  id: string;
  email: string;
  display_name?: string | null;
  created_at?: string;
  last_login_at?: string | null;
  is_active?: boolean;
  roles?: string[];
  tools?: string[];
  direct_tools?: string[];
};

export type AdminUsersResponse = {
  users: AdminUser[];
};

export type UpdateUserResponse = {
  user: AdminUser;
};

export type AdminRolesResponse = {
  roles: Array<{ name: string }> | string[];
};
