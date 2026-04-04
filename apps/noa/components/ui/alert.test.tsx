import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { Alert, AlertDescription, AlertTitle } from "./alert"

describe("Alert", () => {
  it.each([
    ["default", "border-border bg-surface text-text"],
    ["destructive", "border-destructive/30 bg-destructive/10 text-destructive"],
    ["warning", "border-warning/30 bg-warning/10 text-warning"],
    ["success", "border-success/30 bg-success/10 text-success"],
    ["info", "border-info/30 bg-info/10 text-info"],
  ] as const)("renders the %s tone with tokenized classes", (tone, expectedClass) => {
    render(
      <Alert tone={tone}>
        <AlertTitle>System update</AlertTitle>
        <AlertDescription>Everything looks good.</AlertDescription>
      </Alert>
    )

    const alert = screen.getByRole("alert")

    expect(alert).toHaveClass("border", "rounded-xl")
    expect(alert).toHaveClass(...expectedClass.split(" "))
    expect(screen.getByText("System update")).toBeInTheDocument()
    expect(screen.getByText("Everything looks good.")).toBeInTheDocument()
  })
})
