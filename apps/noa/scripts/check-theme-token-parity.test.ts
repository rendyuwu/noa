import { describe, expect, it } from "vitest";

import { collectThemeTokens, findExtraThemeTokens, findMissingThemeTokens } from "./check-theme-token-parity.mjs";

describe("check-theme-token-parity", () => {
  it("collects tokens from both theme blocks", () => {
    const css = `
      :root, [data-theme="light"] { --bg: 0 0% 100%; --text: 0 0% 0%; }
      [data-theme="dark"] { --bg: 0 0% 0%; --text: 0 0% 100%; }
    `;

    expect(collectThemeTokens(css)).toEqual({
      light: new Set(["--bg", "--text"]),
      dark: new Set(["--bg", "--text"]),
    });
  });

  it("reports tokens missing from dark theme", () => {
    const css = `
      :root, [data-theme="light"] { --bg: 0 0% 100%; --text: 0 0% 0%; --warning: 40 90% 50%; }
      [data-theme="dark"] { --bg: 0 0% 0%; --text: 0 0% 100%; }
    `;

    expect(findMissingThemeTokens(css)).toEqual(["--warning"]);
  });

  it("reports tokens present only in dark theme", () => {
    const css = `
      :root, [data-theme="light"] { --bg: 0 0% 100%; --text: 0 0% 0%; }
      [data-theme="dark"] { --bg: 0 0% 0%; --text: 0 0% 100%; --warning: 40 90% 50%; }
    `;

    expect(findExtraThemeTokens(css)).toEqual(["--warning"]);
  });
});
