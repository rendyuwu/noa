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
      <section className="w-full max-w-[440px] overflow-hidden rounded-2xl border border-border bg-card/70 shadow-lg backdrop-blur-sm">
        <div className="p-6 sm:p-7">
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.02em] text-foreground">
            Something went wrong
          </h1>
          <p className="mt-2 font-sans text-sm text-muted-foreground">
            We hit an unexpected error while loading this page.
          </p>
          <div className="mt-6 border-t border-border pt-5">
            <Button type="button" onClick={() => reset()}>
              Try again
            </Button>
          </div>
        </div>
      </section>
    </main>
  );
}
