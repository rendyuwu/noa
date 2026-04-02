import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
};

export default nextConfig;
