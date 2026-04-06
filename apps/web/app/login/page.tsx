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
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
        aria-busy={submitting}
        className="w-full max-w-[420px] overflow-hidden rounded-2xl border border-border bg-card/70 shadow-lg backdrop-blur-sm"
      >
        <div className="p-6 sm:p-7">
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.02em] text-foreground">
            Login
          </h1>
          <p className="mt-2 font-sans text-sm text-muted-foreground">Sign in with your LDAP credentials.</p>

          <div className="mt-6 space-y-4 font-sans">
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
                required
                type="email"
                value={email}
                disabled={submitting}
                aria-describedby={error ? "login-error" : undefined}
                onChange={(e) => setEmail(e.target.value)}
              />
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
            <Button className="w-full" disabled={submitting} type="submit">
              {submitting ? "Signing in..." : "Sign in"}
            </Button>

            {error ? (
              <InlineAlert id="login-error" variant="destructive" className="mt-4" role="alert" aria-live="assertive">
                {error}
              </InlineAlert>
            ) : null}
          </div>
        </div>
      </form>
    </main>
  );
}
