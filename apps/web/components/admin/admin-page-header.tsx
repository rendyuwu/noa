import type { ReactNode } from "react";

type AdminPageHeaderProps = {
  title: string;
  description?: string;
  actions?: ReactNode;
  divider?: boolean;
};

export function AdminPageHeader({
  title,
  description,
  actions,
  divider = true,
}: AdminPageHeaderProps) {
  return (
    <div className={divider ? "border-b border-border/70 pb-5" : undefined}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="font-serif text-[2rem] font-semibold tracking-[-0.03em] text-foreground sm:text-[2.25rem]">
            {title}
          </h1>
          {description ? <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2 sm:pb-1">{actions}</div> : null}
      </div>
    </div>
  );
}
