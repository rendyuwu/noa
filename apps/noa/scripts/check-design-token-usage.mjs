import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ALLOWED_EXTENSIONS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".css", ".scss", ".sass"]);
const CSS_LIKE_EXTENSIONS = new Set([".css", ".scss", ".sass"]);
const EXEMPT_FILES = new Set(["app/globals.css", "tailwind.config.ts"]);

const TAILWIND_PALETTE_FAMILIES = "(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)";
const TAILWIND_KEYWORD_COLORS = "(?:black|white|transparent|current|inherit)";
const TAILWIND_COLOR_VALUE = `(?:${TAILWIND_PALETTE_FAMILIES}(?:-(?:50|100|200|300|400|500|600|700|800|900|950))?|${TAILWIND_KEYWORD_COLORS})`;
const RAW_UTILITY_PATTERN = new RegExp(
  `(?:^|[^A-Za-z0-9-])((?:bg|text|border|ring|fill|stroke|from|via|to)-${TAILWIND_COLOR_VALUE}(?:\/\d{1,3})?)(?=[^A-Za-z0-9-]|$)`,
  "g",
);
const RAW_CSS_COLOR_PATTERN = /(?:#(?:[\da-fA-F]{3,8})\b|\b(?:rgba?|hsla?|hsl|oklch|oklab|lab|lch|color)\([^\)]*\)|\b(?:black|white)\b(?!-))/g;

function normalizeFilePath(filePath) {
  return filePath.replace(/\\/g, "/");
}

function getExtension(filePath) {
  return path.extname(filePath).toLowerCase();
}

function isExemptFile(filePath) {
  const normalized = normalizeFilePath(filePath);
  return Array.from(EXEMPT_FILES).some((entry) => normalized === entry || normalized.endsWith(`/${entry}`));
}

function isAllowedFile(filePath) {
  return ALLOWED_EXTENSIONS.has(getExtension(filePath));
}

function collectMatches(line, regex) {
  const matches = [];
  for (const match of line.matchAll(regex)) {
    matches.push(match[1] ?? match[0]);
  }
  return matches;
}

export function findTokenViolations(source, filePath) {
  if (!isAllowedFile(filePath) || isExemptFile(filePath)) {
    return [];
  }

  const violations = [];
  const normalizedFilePath = normalizeFilePath(filePath);
  const useCssColorChecks = CSS_LIKE_EXTENSIONS.has(getExtension(filePath));

  for (const [index, line] of source.split(/\r?\n/).entries()) {
    const lineNumber = index + 1;

    for (const utility of collectMatches(line, RAW_UTILITY_PATTERN)) {
      violations.push({ filePath: normalizedFilePath, line: lineNumber, utility });
    }

    if (useCssColorChecks) {
      for (const utility of collectMatches(line, RAW_CSS_COLOR_PATTERN)) {
        violations.push({ filePath: normalizedFilePath, line: lineNumber, utility });
      }
    }
  }

  return violations;
}

async function walkFiles(rootDir) {
  const entries = await readdir(rootDir, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    if (entry.name === "node_modules" || entry.name === ".next" || entry.name === ".git") {
      continue;
    }

    const fullPath = path.join(rootDir, entry.name);

    if (entry.isDirectory()) {
      files.push(...(await walkFiles(fullPath)));
      continue;
    }

    if (entry.isFile() && ALLOWED_EXTENSIONS.has(getExtension(entry.name))) {
      files.push(fullPath);
    }
  }

  return files;
}

export async function scanTokenViolations(rootDir = process.cwd()) {
  const files = await walkFiles(rootDir);
  const violations = [];

  for (const filePath of files) {
    const relativeFilePath = normalizeFilePath(path.relative(rootDir, filePath));
    if (isExemptFile(relativeFilePath)) {
      continue;
    }

    const source = await readFile(filePath, "utf8");
    violations.push(...findTokenViolations(source, relativeFilePath));
  }

  return violations;
}

export async function main() {
  const violations = await scanTokenViolations();

  if (violations.length === 0) {
    console.log("Design token usage check passed.");
    return;
  }

  for (const violation of violations) {
    console.error(`${violation.filePath}:${violation.line} ${violation.utility}`);
  }

  process.exitCode = 1;
}

const isMainModule = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.stack || error.message : String(error));
    process.exitCode = 1;
  });
}
