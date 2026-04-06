import type { NextConfig } from "next";

import * as dotenv from "dotenv";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const configDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(configDir, "../..");
const envPath = path.join(repoRoot, ".env");

if (fs.existsSync(envPath)) {
  dotenv.config({ path: envPath, override: false });
}

const nextConfig: NextConfig = {
  env: {
    LLM_MODEL: process.env.LLM_MODEL ?? "",
  },
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
};

export default nextConfig;
