import type { Metadata } from 'next'

export const metadata: Metadata = {
  metadataBase: new URL('https://prism-insight.vercel.app'),
  title: 'PRISM-INSIGHT | AI-Powered Stock Analysis & Automated Trading',
  description: '13 specialized AI agents analyze Korean & US stocks in real-time, generate trading signals, and execute trades automatically. Open source, free to use.',
  keywords: [
    'stock analysis',
    'AI trading',
    'automated trading',
    'Korean stocks',
    'US stocks',
    'KOSPI',
    'NASDAQ',
    'trading bot',
    'investment AI',
    'open source trading'
  ],
  authors: [{ name: 'dragon1086' }],
  creator: 'PRISM-INSIGHT',
  publisher: 'PRISM-INSIGHT',
  robots: 'index, follow',
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: 'https://prism-insight.vercel.app/landing',
    siteName: 'PRISM-INSIGHT',
    title: 'PRISM-INSIGHT | AI-Powered Stock Analysis & Automated Trading',
    description: '13 specialized AI agents analyze Korean & US stocks in real-time. Open source, free to use.',
    images: [
      {
        url: '/og-image.png',
        width: 1200,
        height: 630,
        alt: 'PRISM-INSIGHT - AI Stock Analysis',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'PRISM-INSIGHT | AI Stock Analysis',
    description: '13 AI agents for Korean & US stock analysis with automated trading',
    images: ['/og-image.png'],
  },
  alternates: {
    canonical: 'https://prism-insight.vercel.app/landing',
  },
}

export default function LandingLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
