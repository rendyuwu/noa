"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function DisabledNavItem({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <button
      type="button"
      aria-disabled="true"
      title="Coming soon"
      onClick={(event) => event.preventDefault()}
      className="flex w-full items-center justify-start gap-3 rounded-xl px-3 py-2 font-sans text-sm text-muted-foreground/80 opacity-70 transition-colors hover:bg-foreground/[0.05] hover:text-foreground"
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </button>
  );
}

export function NavLinkItem({
  icon,
  label,
  href,
  flat = false,
}: {
  icon: ReactNode;
  label: string;
  href: string;
  flat?: boolean;
}) {
  const pathname = usePathname();
  const isActive = pathname === href || pathname.startsWith(`${href}/`);

  return (
    <Link
      href={href}
      aria-current={isActive ? "page" : undefined}
      className={[
        flat
          ? "flex w-full items-center justify-start gap-3 rounded-xl px-3 py-2 font-sans text-sm transition-colors active:scale-[0.99]"
          : "flex w-full items-center justify-start gap-3 rounded-2xl px-4 py-2.5 font-sans text-sm transition-colors active:scale-[0.99]",
        flat
          ? isActive
            ? "bg-foreground/[0.08] text-foreground"
            : "text-muted-foreground/80 hover:bg-foreground/[0.05] hover:text-foreground"
          : isActive
            ? "border border-border/80 bg-card/80 text-foreground shadow-sm"
            : "border border-transparent text-muted-foreground/80 hover:border-border/70 hover:bg-card/70 hover:text-foreground",
      ].join(" ")}
    >
      <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center">
        {icon}
      </span>
      {label}
    </Link>
  );
}
