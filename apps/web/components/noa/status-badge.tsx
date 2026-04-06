import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const statusBadgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
  {
    variants: {
      variant: {
        default: "bg-secondary text-secondary-foreground ring-border",
        primary: "bg-primary/10 text-primary ring-primary/25",
        success: "bg-success/10 text-success ring-success/25",
        warning: "bg-warning/10 text-warning-foreground ring-warning/25 bg-warning/15",
        destructive: "bg-destructive/10 text-destructive ring-destructive/25",
        info: "bg-info/10 text-info ring-info/25",
        muted: "bg-muted text-muted-foreground ring-border",
        outline: "bg-transparent text-muted-foreground ring-border",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export type StatusBadgeVariant = NonNullable<VariantProps<typeof statusBadgeVariants>["variant"]>;

export function StatusBadge({
  variant,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof statusBadgeVariants>) {
  return (
    <span className={cn(statusBadgeVariants({ variant }), className)} {...props}>
      {children}
    </span>
  );
}
