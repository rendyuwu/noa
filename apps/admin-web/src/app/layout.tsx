import type { Metadata } from 'next'
import { JetBrains_Mono, Plus_Jakarta_Sans } from 'next/font/google'
import { Toaster } from '@gio/bigsu-ui'
import './globals.css'

const plusJakartaSans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  variable: '--font-pjs',
})

const jetBrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jbm',
})

export const metadata: Metadata = {
  title: 'NOA Admin — Biznet Gio',
  description: 'NOA administration panel, built on BIGSU, the Biznet Gio Standard UI.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plusJakartaSans.variable} ${jetBrainsMono.variable}`}>
      <body className="bg-app font-sans text-text-primary antialiased">
        {children}
        {/* BIGSU toast outlet — bigsuToast.success/warning/danger/info render here. */}
        <Toaster />
      </body>
    </html>
  )
}
