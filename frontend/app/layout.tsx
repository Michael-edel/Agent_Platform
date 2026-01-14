import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'CyberPlat - Document Processing',
  description: 'SaaS platform for document processing and invoice management',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
