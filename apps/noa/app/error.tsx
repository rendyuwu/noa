"use client";

import { useEffect } from "react";

import { reportClientError } from "@/components/lib/observability/error-reporting";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    reportClientError(error, {
      source: "app.error-boundary",
      digest: error.digest,
    });
  }, [error]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <section className="w-full max-w-[440px] rounded-3xl border border-border bg-surface p-6 shadow-soft">
        <h1 className="text-3xl font-semibold tracking-[-0.02em] text-text">Something went wrong</h1>
        <p className="mt-2 font-ui text-sm text-muted">We hit an unexpected error while loading this route.</p>
        <button
          type="button"
          className="mt-6 inline-flex rounded-xl bg-accent px-4 py-2.5 font-ui text-sm font-semibold text-accent-foreground"
          onClick={() => reset()}
        >
          Try again
        </button>
      </section>
    </main>
  );
}
