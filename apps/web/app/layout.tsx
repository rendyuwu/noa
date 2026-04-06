import type { Metadata } from "next";
import "./globals.css";

import { ErrorReportingProvider } from "@/components/lib/observability/error-reporting-provider";
import { ThemeProvider } from "@/components/noa/theme-provider";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "NOA",
  description: "NOA assistant and admin console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full" suppressHydrationWarning>
      <body className="min-h-dvh bg-background text-foreground font-sans antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem
          disableTransitionOnChange
        >
          <ErrorReportingProvider>{children}</ErrorReportingProvider>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
