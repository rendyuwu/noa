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
    expect(getOklchLightness(darkBlock, "foreground")).toBeLessThanOrEqual(0.99);
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
    /* DESIGN.md §2: Parchment #f5f4ed, Ivory #faf9f5, Terracotta #c96442 */
    expect(css).toMatch(/:root\s*\{[\s\S]*--background:\s*oklch\(0\.966\s+0\.009\s+100\.0\);/);
    expect(css).toMatch(/:root\s*\{[\s\S]*--card:\s*oklch\(0\.982\s+0\.005\s+95\.1\);/);
    expect(css).toMatch(/:root\s*\{[\s\S]*--primary:\s*oklch\(0\.617\s+0\.138\s+39\.0\);/);
    /* DESIGN.md §2 dark: Near Black #141413, Dark Surface #30302e, Coral Accent #d97757 */
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--background:\s*oklch\(0\.191\s+0\.002\s+106\.6\);/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--card:\s*oklch\(0\.308\s+0\.004\s+106\.6\);/);
    expect(css).toMatch(/\.dark\s*\{[\s\S]*--primary:\s*oklch\(0\.672\s+0\.131\s+38\.8\);/);
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
