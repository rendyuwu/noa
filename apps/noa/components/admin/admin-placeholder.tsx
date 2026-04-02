import { ArrowRight } from "lucide-react";

type AdminPlaceholderProps = {
  eyebrow: string;
  title: string;
  description: string;
};

export function AdminPlaceholder({ eyebrow, title, description }: AdminPlaceholderProps) {
  return (
    <section className="rounded-3xl border border-border bg-surface p-5 shadow-soft">
      <p className="font-ui text-xs uppercase tracking-[0.18em] text-muted">{eyebrow}</p>
      <h2 className="mt-3 text-2xl font-semibold tracking-[-0.02em] text-text">{title}</h2>
      <p className="mt-3 max-w-3xl font-ui text-sm leading-6 text-muted">{description}</p>
      <div className="mt-5 inline-flex items-center gap-2 rounded-xl border border-border bg-bg px-3 py-2 font-ui text-sm text-muted">
        Next lane: wire backend contracts and representative integration tests.
        <ArrowRight className="size-4" />
      </div>
    </section>
  );
}
