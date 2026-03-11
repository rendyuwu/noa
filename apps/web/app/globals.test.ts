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

  it("defines dark background token", () => {
    const css = readFileSync(path.join(dirname, "globals.css"), "utf8");

    expect(css).toContain("--bg: 30 3.3% 11.8%;");
  });
});
