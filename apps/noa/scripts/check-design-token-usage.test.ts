import { describe, expect, it } from "vitest";

import { findTokenViolations } from "./check-design-token-usage.mjs";

describe("check-design-token-usage", () => {
  it("does not flag semantic token utilities", () => {
    const source =
      '<div className="bg-primary text-warning border-destructive bg-surface-2 ring-info/30 bg-overlay/40" />';

    expect(findTokenViolations(source, "components/ui/button.tsx")).toEqual([]);
  });

  it("flags raw palette utilities from non-red families", () => {
    const source = '<div className="bg-slate-500 text-primary" />';

    expect(findTokenViolations(source, "components/ui/card.tsx")).toEqual([
      { filePath: "components/ui/card.tsx", line: 1, utility: "bg-slate-500" },
    ]);
  });

  it("scans CSS modules and flags raw CSS color literals", () => {
    const source = ".banner { color: hsl(0 84% 60%); background: white; }";

    expect(findTokenViolations(source, "components/example.module.css")).toEqual([
      { filePath: "components/example.module.css", line: 1, utility: "hsl(0 84% 60%)" },
      { filePath: "components/example.module.css", line: 1, utility: "white" },
    ]);
  });

  it("exempts globals css and tailwind config from raw CSS color checks", () => {
    const source = ".banner { color: hsl(0 84% 60%); background: white; }";

    expect(findTokenViolations(source, "app/globals.css")).toEqual([]);
    expect(findTokenViolations(source, "tailwind.config.ts")).toEqual([]);
  });
});
