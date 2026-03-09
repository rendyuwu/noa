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
    <main className="page-shell" style={{ display: "grid", placeItems: "center" }}>
      <form className="panel" style={{ width: "100%", maxWidth: 420, padding: 20 }} onSubmit={onSubmit}>
        <h1 style={{ marginTop: 0 }}>Login</h1>
        <p className="muted">Sign in with your LDAP credentials.</p>

        <label>
          Email
          <input className="input" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <div style={{ height: 10 }} />
        <label>
          Password
          <input
            className="input"
            required
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <div style={{ height: 12 }} />
        <button className="button button-primary" disabled={submitting} type="submit" style={{ width: "100%" }}>
          {submitting ? "Signing in..." : "Sign in"}
        </button>
        {error ? <p className="error">{error}</p> : null}
      </form>
    </main>
  );
}
