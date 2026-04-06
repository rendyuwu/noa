"use client";

import type { ReactNode } from "react";

import { useTheme } from "next-themes";

import { ConfirmAction } from "@/components/lib/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

export function AccountMenu({ trigger, onLogout }: { trigger: ReactNode; onLogout: () => void }) {
  const { theme, setTheme } = useTheme();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
      <DropdownMenuContent align="end" forceMount>
        <DropdownMenuLabel>Appearance</DropdownMenuLabel>
        <DropdownMenuRadioGroup aria-label="Theme" value={theme ?? "system"} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <ConfirmAction
          title="Log out?"
          description="This ends your NOA session on this device."
          confirmLabel="Log out"
          confirmVariant="primary"
          closeOnConfirm
          onConfirm={onLogout}
          trigger={({ open, disabled }) => (
            <DropdownMenuItem
              variant="destructive"
              disabled={disabled}
              onSelect={() => open()}
            >
              Log out
            </DropdownMenuItem>
          )}
        />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
