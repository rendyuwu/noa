import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

function collectTokensFromBlock(source, selectorNeedles) {
  const tokens = new Set();

  for (const match of source.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = match[1];
    const body = match[2];

    if (!selectorNeedles.some((needle) => selector.includes(needle))) {
      continue;
    }

    for (const tokenMatch of body.matchAll(/(--[\w-]+)\s*:/g)) {
      tokens.add(tokenMatch[1]);
    }
  }

  return tokens;
}

export function collectThemeTokens(source) {
  return {
    light: collectTokensFromBlock(source, [":root", '[data-theme="light"]']),
    dark: collectTokensFromBlock(source, ['[data-theme="dark"]']),
  };
}

export function findMissingThemeTokens(source) {
  const { light, dark } = collectThemeTokens(source);
  return [...light].filter((token) => !dark.has(token)).sort();
}

export function findExtraThemeTokens(source) {
  const { light, dark } = collectThemeTokens(source);
  return [...dark].filter((token) => !light.has(token)).sort();
}

export async function main() {
  const filePath = path.resolve("app/globals.css");
  const source = await readFile(filePath, "utf8");
  const missingDark = findMissingThemeTokens(source);
  const missingLight = findExtraThemeTokens(source);

  if (missingDark.length > 0 || missingLight.length > 0) {
    if (missingDark.length > 0) {
      console.error(`Missing dark theme tokens: ${missingDark.join(", ")}`);
    }

    if (missingLight.length > 0) {
      console.error(`Missing light theme tokens: ${missingLight.join(", ")}`);
    }

    process.exitCode = 1;
    return;
  }

  console.log("Theme token parity check passed.");
}

const isMainModule = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack || error.message : String(error));
    process.exitCode = 1;
  });
}
