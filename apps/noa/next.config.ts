import type { NextConfig } from "next";

import { buildSecurityHeaders } from "./components/lib/security/http-headers";

const nextConfig: NextConfig = {
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: buildSecurityHeaders({ isProduction: process.env.NODE_ENV === "production" }),
      },
    ];
  },
};

export default nextConfig;
