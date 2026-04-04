export type AuthUser = {
  id: string;
  email: string;
  display_name?: string | null;
  is_active?: boolean;
  roles?: string[];
};

export type LoginResponse = {
  expiresIn?: number;
  user: AuthUser | null;
};
