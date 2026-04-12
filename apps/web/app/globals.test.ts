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

    expect(css).toMatch(
      /--font-serif:\s*var\(--font-newsreader\),\s*ui-serif,\s*Georgia,\s*"Iowan Old Style",\s*"Palatino Linotype",\s*Palatino,\s*"Times New Roman",\s*Times,\s*serif;/,
    );
    expect(css).toMatch(/:root\s*\{[\s\S]*--background:\s*oklch\(0\.985\s+0\.006\s+78\);/);
    expect(css).toMatch(/:root\s*\{[\s\S]*--card:\s*oklch\(0\.996\s+0\.003\s+78\);/);
    expect(css).toMatch(/:root\s*\{[\s\S]*--primary:\s*oklch\(0\.360\s+0\.075\s+46\);/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--background:\s*oklch\(0\.170\s+0\.010\s+50\);/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--card:\s*oklch\(0\.235\s+0\.012\s+50\);/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--primary:\s*oklch\(0\.880\s+0\.018\s+90\);/);
    expect(css).toMatch(/\.input\s*\{/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*rounded-xl/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*bg-card\/80/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*text-base/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*md:text-sm/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*aria-invalid:border-destructive/);
    expect(css).toMatch(/\.input\s*\{[\s\S]*aria-invalid:ring-destructive\/20/);
    expect(css).toMatch(/\.editorial-kicker\s*\{/);
    expect(css).toMatch(/\.editorial-title\s*\{/);
    expect(css).toMatch(/\.editorial-subpanel\s*\{/);
    expect(css).toMatch(/\.font-serif\.font-semibold\s*\{[\s\S]*font-weight:\s*500\s*!important;/);
  });
});
