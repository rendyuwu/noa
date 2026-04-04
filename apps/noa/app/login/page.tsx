"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { setAuthToken, setAuthUser } from "@/components/lib/auth/auth-storage";
import { sanitizeReturnTo } from "@/components/lib/auth/return-to";
import type { LoginResponse } from "@/components/lib/auth/types";

type ErrorPayload = {
  detail?: string;
  error_code?: string;
};

const PENDING_APPROVAL_COPY = "Your account is pending approval. Ask an admin to enable it.";

function resolveLoginError(payload: ErrorPayload | null, fallback: string) {
  if (payload?.error_code === "user_pending_approval") {
    return PENDING_APPROVAL_COPY;
  }

  return payload?.detail || fallback;
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setPending(true);

    const returnTo = sanitizeReturnTo(new URLSearchParams(window.location.search).get("returnTo"));

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({ email, password }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as ErrorPayload | null;
        setError(resolveLoginError(payload, `Login failed (${response.status})`));
        return;
      }

      const payload = (await response.json()) as LoginResponse;
      setAuthToken(payload.access_token);
      setAuthUser(payload.user);
      router.push(returnTo);
    } catch {
      setError("Login failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10 sm:px-6">
      <section className="w-full max-w-[440px] rounded-3xl border border-border bg-surface p-6 shadow-soft sm:p-8">
        <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">NOA</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.03em] text-text">Sign in to continue</h1>
        <p className="mt-2 font-ui text-sm leading-6 text-muted">
          Sign in to access your assistant workspace and admin tools.
        </p>

        <form className="mt-8 space-y-4" onSubmit={onSubmit}>
          <label className="block font-ui text-sm font-medium text-text" htmlFor="email">
            Email
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="mt-2 w-full rounded-2xl border border-border bg-bg px-4 py-3 text-text outline-none transition focus:border-accent"
            />
          </label>

          <label className="block font-ui text-sm font-medium text-text" htmlFor="password">
            Password
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-2 w-full rounded-2xl border border-border bg-bg px-4 py-3 text-text outline-none transition focus:border-accent"
            />
          </label>

          {error ? (
            <div role="alert" className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 font-ui text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <button
            type="submit"
            disabled={pending}
            className="inline-flex w-full items-center justify-center rounded-2xl bg-accent px-4 py-3 font-ui text-sm font-semibold text-accent-foreground disabled:opacity-70"
          >
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
