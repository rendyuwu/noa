import * as React from "react";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createContext, useContext, useState, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const { setThemeMock } = vi.hoisted(() => ({
  setThemeMock: vi.fn(),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({
    theme: "system",
    setTheme: setThemeMock,
  }),
}));

vi.mock("@/components/ui/dialog", () => {
  function Dialog({ open, children }: { open?: boolean; children?: ReactNode }) {
    if (!open) {
      return null;
    }

    return <div role="dialog">{children}</div>;
  }

  function DialogContent({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DialogHeader({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DialogTitle({ children }: { children?: ReactNode }) {
    return <h1>{children}</h1>;
  }

  function DialogDescription({ children }: { children?: ReactNode }) {
    return <p>{children}</p>;
  }

  function DialogFooter({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  return {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
  };
});

vi.mock("@/components/ui/dropdown-menu", () => {
  type MenuContextValue = {
    open: boolean;
    setOpen: (open: boolean) => void;
  };

  type MenuButtonProps = React.ComponentPropsWithoutRef<"button"> & {
    children?: ReactNode;
    asChild?: boolean;
    onSelect?: () => void;
  };

  const DropdownMenuContext = createContext<MenuContextValue | null>(null);

  function DropdownMenu({ children }: { children?: ReactNode }) {
    const [open, setOpen] = useState(false);

    return <DropdownMenuContext.Provider value={{ open, setOpen }}>{children}</DropdownMenuContext.Provider>;
  }

  function DropdownMenuTrigger({ children, asChild, ...props }: MenuButtonProps) {
    const context = useContext(DropdownMenuContext);

    const handleClick = (event: React.MouseEvent<HTMLButtonElement>) => {
      context?.setOpen(true);
      props.onClick?.(event);
    };

    if (asChild && React.isValidElement(children)) {
      return React.cloneElement(children as React.ReactElement<React.ComponentPropsWithoutRef<"button">>, {
        ...props,
        onClick: handleClick,
      });
    }

    return (
      <button type="button" {...props} onClick={handleClick}>
        {children}
      </button>
    );
  }

  function DropdownMenuContent({ children }: { children?: ReactNode }) {
    const context = useContext(DropdownMenuContext);

    if (!context?.open) {
      return null;
    }

    return <div role="menu">{children}</div>;
  }

  function DropdownMenuItem({ children, onSelect, ...props }: MenuButtonProps) {
    const context = useContext(DropdownMenuContext);

    return (
      <button
        type="button"
        role="menuitem"
        {...props}
        onClick={(event) => {
          props.onClick?.(event);
          onSelect?.();
          context?.setOpen(false);
        }}
      >
        {children}
      </button>
    );
  }

  function DropdownMenuLabel({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DropdownMenuRadioGroup({ children }: { children?: ReactNode }) {
    return <div>{children}</div>;
  }

  function DropdownMenuRadioItem({ children }: { children?: ReactNode }) {
    return <button type="button">{children}</button>;
  }

  function DropdownMenuSeparator() {
    return <hr />;
  }

  return {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
  };
});

import { AccountMenu } from "./account-menu";

describe("AccountMenu", () => {
  it("keeps the logout dialog visible after the dropdown closes", async () => {
    const user = userEvent.setup();
    const onLogout = vi.fn();

    render(
      <AccountMenu
        onLogout={onLogout}
        trigger={<button type="button">Open account menu</button>}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Open account menu" }));
    await user.click(screen.getByRole("menuitem", { name: "Log out" }));

    await waitFor(() => {
      expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    });

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByText("Log out?")).toBeVisible();
    expect(onLogout).not.toHaveBeenCalled();
  });
});
