"use client";

import { useEffect } from "react";

import { reportClientError } from "@/components/lib/error-reporting";

type GlobalErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    console.error(error);
    reportClientError(error, {
      source: "app.error-boundary",
      digest: error.digest,
    });
  }, [error]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <section className="w-full max-w-[440px] overflow-hidden rounded-2xl border border-border bg-surface/70 shadow-[0_0.5rem_2rem_rgba(0,0,0,0.06)] backdrop-blur-sm">
        <div className="p-6 sm:p-7">
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.02em] text-text">
            Something went wrong
          </h1>
          <p className="mt-2 font-ui text-sm text-muted">
            We hit an unexpected error while loading this page.
          </p>
          <div className="mt-6 border-t border-border pt-5">
            <button
              type="button"
              className="inline-flex items-center justify-center rounded-xl bg-accent px-4 py-2.5 font-ui text-sm font-semibold text-white shadow-sm transition hover:bg-accent/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-offset-2 focus-visible:ring-offset-bg active:scale-[0.99]"
              onClick={() => reset()}
            >
              Try again
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
