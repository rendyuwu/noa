import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Button } from "./button"

describe("Button", () => {
  it.each([
    ["outline", "border border-input bg-background hover:bg-muted/10 hover:text-foreground"],
    ["ghost", "hover:bg-muted/10 hover:text-foreground"],
  ] as const)("renders the %s variant with neutral hover classes", (variant, expectedClass) => {
    render(<Button variant={variant}>Action</Button>)

    const button = screen.getByRole("button", { name: "Action" })

    expect(button).toHaveClass(...expectedClass.split(" "))
  })
})
