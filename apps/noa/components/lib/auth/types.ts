export type AuthUser = {
  id: string;
  email: string;
  display_name?: string | null;
  roles?: string[];
};

export type LoginResponse = {
  access_token: string;
  user: AuthUser | null;
};
