import { ArrowRight, FlaskConical } from "lucide-react";

import { isPlaceholderAdminRouteEnabled } from "./lib/placeholder-route-access";

type AdminPlaceholderProps = {
  eyebrow: string;
  title: string;
  description: string;
};

export function AdminPlaceholder({ eyebrow, title, description }: AdminPlaceholderProps) {
  const placeholderRoutesEnabled = isPlaceholderAdminRouteEnabled();

  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">{eyebrow}</p>
        <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 font-ui text-[11px] font-medium uppercase tracking-[0.12em] text-amber-800">
          <FlaskConical className="size-3" />
          Non-production surface
        </span>
      </div>
      <h2 className="mt-3 text-2xl font-semibold tracking-[-0.02em] text-text">{title}</h2>
      <p className="mt-3 max-w-3xl font-ui text-sm leading-6 text-muted">{description}</p>
      <div className="mt-5 space-y-3">
        <div className="inline-flex items-center gap-2 rounded-xl border border-border bg-bg px-3 py-2 font-ui text-sm text-muted">
          Next lane: wire backend contracts and representative integration tests.
          <ArrowRight className="size-4" />
        </div>
        <p className="font-ui text-xs leading-5 text-muted">
          {placeholderRoutesEnabled
            ? "This placeholder route is enabled only outside production unless NOA_ENABLE_PLACEHOLDER_ADMIN_SURFACES=true is set on the server."
            : "This placeholder route is disabled in production builds."}
        </p>
      </div>
    </section>
  );
}
