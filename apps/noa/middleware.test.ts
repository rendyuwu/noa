import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

describe("middleware auth imports", () => {
  it("uses edge-safe auth constants instead of server-auth", async () => {
    const currentDir = dirname(fileURLToPath(import.meta.url));
    const source = await readFile(resolve(currentDir, "middleware.ts"), "utf8");

    expect(source).not.toContain('from "@/components/lib/auth/server-auth"');
    expect(source).toContain('from "@/components/lib/auth/auth-constants"');
  });
});
