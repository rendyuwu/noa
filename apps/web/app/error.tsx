"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { reportClientError } from "@/components/lib/observability/error-reporting";

type GlobalErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    reportClientError(error, {
      source: "app.error-boundary",
      digest: error.digest,
    });
  }, [error]);

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <section className="w-full max-w-[480px] overflow-hidden rounded-[32px] border border-border/80 bg-card/80 shadow-xl shadow-amber-950/5 backdrop-blur">
        <div className="p-8 sm:p-10">
          <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-muted-foreground">
            Editorial fallback
          </p>
          <h1 className="mt-3 font-serif text-4xl font-semibold leading-tight tracking-[-0.03em] text-foreground">
            Something went wrong
          </h1>
          <p className="mt-3 font-sans text-sm text-muted-foreground">
            We hit an unexpected error while loading this page.
          </p>
          <div className="mt-8 border-t border-border/70 pt-6">
            <Button type="button" onClick={() => reset()}>
              Try again
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
