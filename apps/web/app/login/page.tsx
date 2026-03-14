"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

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
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const labelClass = "block text-sm font-medium text-text";
  const inputClass =
    "mt-1 w-full rounded-xl border border-border bg-surface/80 px-3 py-2.5 text-sm text-text shadow-sm outline-none placeholder:text-muted focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-70";

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const response = await fetch(`${getApiUrl()}/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      const payload = await jsonOrThrow<LoginResponse>(response);

      setAuthToken(payload.access_token);
      setAuthUser(payload.user ?? null);
      router.push("/assistant");
    } catch (error) {
      setError(toUserMessage(error));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <form
        onSubmit={onSubmit}
        aria-busy={submitting}
        className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-border bg-surface/70 shadow-[0_0.5rem_2rem_rgba(0,0,0,0.06)] backdrop-blur-sm"
      >
        <div className="p-6 sm:p-7">
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.02em] text-text">
            Login
          </h1>
          <p className="mt-2 font-ui text-sm text-muted">Sign in with your LDAP credentials.</p>

          <div className="mt-6 space-y-4 font-ui">
            <div>
              <label htmlFor="login-email" className={labelClass}>
                Email
              </label>
              <input
                id="login-email"
                name="email"
                autoComplete="username"
                inputMode="email"
                className={inputClass}
                required
                type="email"
                value={email}
                disabled={submitting}
                aria-describedby={error ? "login-error" : undefined}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div>
              <label htmlFor="login-password" className={labelClass}>
                Password
              </label>
              <input
                id="login-password"
                name="password"
                autoComplete="current-password"
                className={inputClass}
                required
                type="password"
                value={password}
                disabled={submitting}
                aria-describedby={error ? "login-error" : undefined}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>

          <div className="mt-6 border-t border-border pt-5">
            <button
              className="inline-flex w-full items-center justify-center rounded-xl bg-accent px-4 py-2.5 font-ui text-sm font-semibold text-white shadow-sm transition hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-60 active:scale-[0.99]"
              disabled={submitting}
              type="submit"
            >
              {submitting ? "Signing in..." : "Sign in"}
            </button>

            {error ? (
              <p
                id="login-error"
                role="alert"
                aria-live="assertive"
                className="mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2 font-ui text-sm text-red-800"
              >
                {error}
              </p>
            ) : null}
          </div>
        </div>
      </form>
    </main>
  );
}
