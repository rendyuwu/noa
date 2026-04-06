import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const dirname = path.dirname(fileURLToPath(import.meta.url));

describe("globals.css", () => {
  it("does not use overflow-wrap:anywhere for wrap-break-word (tables must not collapse)", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).not.toMatch(
      /\.wrap-break-word\s*\{[\s\S]*overflow-wrap:\s*anywhere\s*;/,
    );
    expect(css).toMatch(
      /\.wrap-break-word\s*\{[\s\S]*overflow-wrap:\s*break-word\s*;/,
    );
  });

  it("defines NOA color tokens for both light and dark mode", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).toMatch(/:root\s*\{[^}]*--background:/);
    expect(css).toMatch(/:root\s*\{[^}]*--primary:/);
    expect(css).toMatch(/:root\s*\{[^}]*--destructive:/);
    expect(css).toMatch(/:root\s*\{[^}]*--success:/);
    expect(css).toMatch(/:root\s*\{[^}]*--warning:/);
    expect(css).toMatch(/\.dark\s*\{[^}]*--background:/);
    expect(css).toMatch(/\.dark\s*\{[^}]*--primary:/);
  });

  it("uses oklch color format", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).toMatch(/--primary:\s*oklch\(/);
    expect(css).toMatch(/--background:\s*oklch\(/);
  });
});
