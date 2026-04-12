"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { InlineAlert } from "@/components/noa/inline-alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toUserMessage } from "@/components/lib/error-message";
import { getApiUrl, jsonOrThrow } from "@/components/lib/fetch-helper";
import { setAuthToken, setAuthUser } from "@/components/lib/auth-store";

type LoginResponse = {
  access_token: string;
  user?: Parameters<typeof setAuthUser>[0];
};

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const validate = () => {
    const nextErrors: { email?: string; password?: string } = {};

    if (!email.trim()) {
      nextErrors.email = "Email is required.";
    } else if (!/^\S+@\S+\.\S+$/.test(email.trim())) {
      nextErrors.email = "Enter a valid email address.";
    }

    if (!password.trim()) {
      nextErrors.password = "Password is required.";
    }

    setFieldErrors(nextErrors);
    return Object.keys(nextErrors).length === 0;
  };

  const validateField = (field: "email" | "password") => {
    setFieldErrors((current) => {
      const next = { ...current };

      if (field === "email") {
        if (!email.trim()) {
          next.email = "Email is required.";
        } else if (!/^\S+@\S+\.\S+$/.test(email.trim())) {
          next.email = "Enter a valid email address.";
        } else {
          delete next.email;
        }
      }

      if (field === "password") {
        if (!password.trim()) {
          next.password = "Password is required.";
        } else {
          delete next.password;
        }
      }

      return next;
    });
  };

  const getSafeReturnTo = () => {
    if (typeof window === "undefined") return null;
    const url = new URL(window.location.href);
    const raw = url.searchParams.get("returnTo");
    if (!raw) return null;
    if (!raw.startsWith("/")) return null;
    if (raw.startsWith("//")) return null;
    return raw;
  };

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    if (!validate()) return;

    setSubmitting(true);

    try {
      const response = await fetch(`${getApiUrl()}/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      const payload = await jsonOrThrow<LoginResponse>(response);

      setAuthToken(payload.access_token);
      setAuthUser(payload.user ?? null);
      router.push(getSafeReturnTo() ?? "/assistant");
    } catch (error) {
      setError(toUserMessage(error, "Login failed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <form
        onSubmit={onSubmit}
        noValidate
        aria-busy={submitting}
        className="w-full max-w-[460px] overflow-hidden rounded-[32px] border border-border/80 bg-card/80 shadow-xl shadow-amber-950/5 backdrop-blur"
      >
        <div className="p-8 sm:p-10">
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary/12 text-primary ring-1 ring-primary/20">
              <span className="text-sm font-semibold tracking-wide">NOA</span>
            </div>
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
                Editorial access
              </p>
              <p className="text-sm text-muted-foreground">Assistant and admin console</p>
            </div>
          </div>

          <h1 className="font-serif text-4xl font-semibold leading-tight tracking-[-0.03em] text-foreground">
            Login
          </h1>
          <p className="mt-3 font-sans text-sm text-muted-foreground">
            Sign in with your LDAP credentials.
          </p>

          <div className="mt-8 space-y-4 font-sans">
            <div>
              <Label htmlFor="login-email">
                Email
              </Label>
              <Input
                id="login-email"
                name="email"
                autoComplete="username"
                inputMode="email"
                className="mt-1"
                type="email"
                value={email}
                disabled={submitting}
                aria-invalid={!!fieldErrors.email}
                aria-describedby={
                  [fieldErrors.email ? "login-email-error" : null, error ? "login-error" : null]
                    .filter(Boolean)
                    .join(" ") || undefined
                }
                onBlur={() => validateField("email")}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setFieldErrors((prev) => (prev.email ? { ...prev, email: undefined } : prev));
                }}
              />
              {fieldErrors.email ? (
                <p id="login-email-error" className="mt-1 text-sm text-destructive">
                  {fieldErrors.email}
                </p>
              ) : null}
            </div>

            <div>
              <Label htmlFor="login-password">
                Password
              </Label>
              <Input
                id="login-password"
                name="password"
                autoComplete="current-password"
                className="mt-1"
                type="password"
                value={password}
                disabled={submitting}
                aria-invalid={!!fieldErrors.password}
                aria-describedby={
                  [fieldErrors.password ? "login-password-error" : null, error ? "login-error" : null]
                    .filter(Boolean)
                    .join(" ") || undefined
                }
                onBlur={() => validateField("password")}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setFieldErrors((prev) => (prev.password ? { ...prev, password: undefined } : prev));
                }}
              />
              {fieldErrors.password ? (
                <p id="login-password-error" className="mt-1 text-sm text-destructive">
                  {fieldErrors.password}
                </p>
              ) : null}
            </div>
          </div>

          <div className="mt-8 border-t border-border/70 pt-6">
            <Button className="w-full" disabled={submitting} type="submit">
              {submitting ? "Signing in..." : "Sign in"}
            </Button>

            {error ? (
              <InlineAlert id="login-error" variant="destructive" className="mt-4">
                {error}
              </InlineAlert>
            ) : null}
          </div>
        </div>
      </form>
    </main>
  );
}
