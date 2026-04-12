import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const dirname = path.dirname(fileURLToPath(import.meta.url));

describe("globals.css", () => {
  function getDarkBlock(css: string) {
    const match = css.match(/\.dark\s*\{([\s\S]*?)\n\}/);

    expect(match).not.toBeNull();

    return match?.[1] ?? "";
  }

  function getOklchLightness(block: string, token: string) {
    const match = block.match(new RegExp(`--${token}:\\s*oklch\\(([^)]+)\\)`));

    expect(match).not.toBeNull();

    return Number.parseFloat(match?.[1]?.split(/\s+/)[0] ?? "NaN");
  }

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

  it("softens dark mode colors", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");
    const darkBlock = getDarkBlock(css);

    expect(getOklchLightness(darkBlock, "background")).toBeGreaterThanOrEqual(0.16);
    expect(getOklchLightness(darkBlock, "sidebar")).toBeGreaterThanOrEqual(0.14);
    expect(getOklchLightness(darkBlock, "card")).toBeGreaterThanOrEqual(0.19);
    expect(getOklchLightness(darkBlock, "border")).toBeGreaterThanOrEqual(0.29);
    expect(getOklchLightness(darkBlock, "foreground")).toBeLessThanOrEqual(0.92);
  });

  it("adds dark-only typography tweaks", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).toMatch(/\.dark body\s*\{[\s\S]*line-height:\s*1\.625;[\s\S]*letter-spacing:\s*0\.01em;[\s\S]*\}/);
    expect(css).toMatch(/\.dark code,\s*\.dark pre,\s*\.dark \.font-mono\s*\{[\s\S]*letter-spacing:\s*0;[\s\S]*\}/);
  });

  it("defines the editorial font and warm UI utilities", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).toMatch(/--font-serif:\s*var\(--font-newsreader\)/);
    expect(css).toMatch(/:root\s*\{[\s\S]*--background:\s*oklch\([^)]+\)[\s\S]*--card:\s*oklch\([^)]+\)[\s\S]*--primary:\s*oklch\([^)]+\)/);
    expect(css).toMatch(/\.input\s*\{/);
    expect(css).toMatch(/\.editorial-title\s*\{/);
  });
});
