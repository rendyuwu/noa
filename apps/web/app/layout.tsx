import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NOA Web",
  description: "NOA assistant and admin console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-dvh bg-bg text-text font-body antialiased">
        {children}
      </body>
    </html>
  );
}
