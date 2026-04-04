import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <section className="w-full max-w-[460px] rounded-3xl border border-border bg-surface p-6 shadow-soft">
        <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">404</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-[-0.02em] text-text">Page not found</h1>
        <p className="mt-2 font-ui text-sm text-muted">
          This page doesn't exist, or the link may be outdated.
        </p>
        <Button variant="outline" className="mt-6 rounded-xl font-ui text-sm font-medium" asChild>
          <Link href="/assistant">Return to assistant</Link>
        </Button>
      </section>
    </main>
  );
}
