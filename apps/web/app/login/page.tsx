"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { getApiUrl } from "@/components/lib/fetch-helper";
import { setAuthToken, setAuthUser } from "@/components/lib/auth-store";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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

      const payload = await response.json();
      if (!response.ok) {
        setError(payload?.detail ?? "Login failed");
        return;
      }

      setAuthToken(payload.access_token);
      setAuthUser(payload.user ?? null);
      router.push("/assistant");
    } catch {
      setError("Unable to reach API");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <form
        onSubmit={onSubmit}
        aria-busy={submitting}
        className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-[#00000010] bg-white/70 shadow-[0_0.5rem_2rem_rgba(0,0,0,0.06)] backdrop-blur-sm dark:border-[#6c6a6040] dark:bg-[#1f1e1b]/70"
      >
        <div className="p-6 sm:p-7">
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.02em] text-[#1a1a18] dark:text-[#eee]">
            Login
          </h1>
          <p className="mt-2 font-ui text-sm text-muted">Sign in with your LDAP credentials.</p>

          <div className="mt-6 space-y-4 font-ui">
            <div>
              <label htmlFor="login-email" className="block text-sm font-medium text-[#1a1a18] dark:text-[#eee]">
                Email
              </label>
              <input
                id="login-email"
                name="email"
                autoComplete="username"
                inputMode="email"
                className="mt-1 w-full rounded-xl border border-[#00000015] bg-white/80 px-3 py-2.5 text-sm text-[#1a1a18] shadow-sm outline-none placeholder:text-[#6b6a68] focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-70 dark:border-[#6c6a6040] dark:bg-[#2b2a27] dark:text-[#eee] dark:placeholder:text-[#9a9893]"
                required
                type="email"
                value={email}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "login-error" : undefined}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            <div>
              <label htmlFor="login-password" className="block text-sm font-medium text-[#1a1a18] dark:text-[#eee]">
                Password
              </label>
              <input
                id="login-password"
                name="password"
                autoComplete="current-password"
                className="mt-1 w-full rounded-xl border border-[#00000015] bg-white/80 px-3 py-2.5 text-sm text-[#1a1a18] shadow-sm outline-none placeholder:text-[#6b6a68] focus-visible:border-accent/60 focus-visible:ring-2 focus-visible:ring-accent/25 focus-visible:ring-offset-2 focus-visible:ring-offset-bg disabled:cursor-not-allowed disabled:opacity-70 dark:border-[#6c6a6040] dark:bg-[#2b2a27] dark:text-[#eee] dark:placeholder:text-[#9a9893]"
                required
                type="password"
                value={password}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "login-error" : undefined}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>

          <div className="mt-6 border-t border-[#00000010] pt-5 dark:border-[#6c6a6040]">
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
