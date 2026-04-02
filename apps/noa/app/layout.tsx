import type { Metadata } from "next";

import { ErrorReportingProvider } from "@/components/lib/observability/error-reporting-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "NOA",
  description: "NOA assistant and admin console rewrite",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-dvh bg-bg text-text font-body antialiased">
        <ErrorReportingProvider>{children}</ErrorReportingProvider>
      </body>
    </html>
  );
}
