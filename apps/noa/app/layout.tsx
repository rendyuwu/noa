import type { Metadata } from "next";

import { ErrorReportingProvider } from "@/components/lib/observability/error-reporting-provider";
import { Toaster } from "sonner";

import "./globals.css";

export const metadata: Metadata = {
  title: "NOA",
  description: "NOA — Your AI-powered assistant and admin console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-dvh bg-bg text-text font-body antialiased">
        <ErrorReportingProvider>{children}</ErrorReportingProvider>
        <Toaster position="bottom-right" richColors closeButton />
      </body>
    </html>
  );
}
