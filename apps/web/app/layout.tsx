import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { ErrorReportingProvider } from "@/components/lib/observability/error-reporting-provider";
import { ThemeProvider } from "@/components/noa/theme-provider";
import { Toaster } from "@/components/ui/sonner";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "NOA",
  description: "NOA assistant and admin console",
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full" suppressHydrationWarning>
      <body
        className={[
          geistSans.variable,
          geistMono.variable,
          "min-h-dvh bg-background text-foreground font-sans antialiased",
        ].join(" ")}
      >
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
