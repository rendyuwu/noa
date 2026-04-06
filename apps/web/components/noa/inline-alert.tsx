import { cva, type VariantProps } from "class-variance-authority";
import { AlertCircle, CheckCircle, Info, AlertTriangle } from "lucide-react";

import { cn } from "@/lib/utils";

const inlineAlertVariants = cva(
  "rounded-xl border px-3 py-2 text-sm flex items-start gap-2",
  {
    variants: {
      variant: {
        destructive: "border-destructive/30 bg-destructive/5 text-destructive dark:border-destructive/40 dark:bg-destructive/10",
        success: "border-success/30 bg-success/5 text-success dark:border-success/40 dark:bg-success/10",
        warning: "border-warning/30 bg-warning/5 text-warning-foreground dark:border-warning/40 dark:bg-warning/10",
        info: "border-info/30 bg-info/5 text-info dark:border-info/40 dark:bg-info/10",
      },
    },
    defaultVariants: {
      variant: "destructive",
    },
  },
);

const iconMap = {
  destructive: AlertCircle,
  success: CheckCircle,
  warning: AlertTriangle,
  info: Info,
};

export function InlineAlert({
  variant = "destructive",
  className,
  children,
  showIcon = true,
  ...props
}: React.HTMLAttributes<HTMLDivElement> &
  VariantProps<typeof inlineAlertVariants> & { showIcon?: boolean }) {
  const Icon = iconMap[variant ?? "destructive"];

  return (
    <div
      role="alert"
      aria-live="assertive"
      className={cn(inlineAlertVariants({ variant }), className)}
      {...props}
    >
      {showIcon ? <Icon className="mt-0.5 h-4 w-4 shrink-0" /> : null}
      <div className="min-w-0">{children}</div>
    </div>
  );
}
