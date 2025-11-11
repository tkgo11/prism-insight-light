"use client"

import React, { createContext, useContext, useState, useEffect } from "react"

type Language = "ko" | "en"

interface LanguageContextType {
  language: Language
  setLanguage: (lang: Language) => void
  t: (key: string) => string
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined)

// Translation dictionary
const translations: Record<Language, Record<string, string>> = {
  ko: {
    // Header
    "header.season": "Season 2",
    "header.openSource": "Open Source",
    "header.tooltip.openSource": "AI 기반 주식 분석 및 매매 시스템 • MIT License",
    "header.startDate": "시작: 2025.09.29",
    "header.updated": "업데이트",
    "header.realtimeUpdate": "실시간 업데이트",
    "header.dashboard": "대시보드",
    "header.aiDecisions": "AI 보유 분석",
    "header.trading": "거래 내역",
    "header.watchlist": "관심 종목",
    "header.tooltip.github": "GitHub 저장소",
    "header.tooltip.telegram": "텔레그램 채널",
    "header.tooltip.theme": "테마 전환",

    // Loading
    "loading.text": "데이터 로딩 중...",

    // Tables
    "table.realPortfolio": "실전투자 포트폴리오",
    "table.simulator": "프리즘 시뮬레이터",
    "table.stock": "종목",
    "table.quantity": "수량",
    "table.avgPrice": "평균단가",
    "table.currentPrice": "현재가",
    "table.profitRate": "수익률",
    "table.profitAmount": "평가손익",
    "table.totalValue": "평가금액",

    // Metrics
    "metrics.totalReturn": "누적 수익률",
    "metrics.winRate": "승률",
    "metrics.avgReturn": "평균 수익률",
    "metrics.avgHoldingDays": "평균 보유일",
    "metrics.totalTrades": "총 거래",
    "metrics.wins": "승",
    "metrics.losses": "패",

    // Common
    "common.won": "원",
    "common.krw": "₩",
    "common.percent": "%",
    "common.days": "일",
    "common.trades": "건",
  },
  en: {
    // Header
    "header.season": "Season 2",
    "header.openSource": "Open Source",
    "header.tooltip.openSource": "AI-powered Stock Analysis & Trading System • MIT License",
    "header.startDate": "Start: 2025.09.29",
    "header.updated": "Updated",
    "header.realtimeUpdate": "Real-time Update",
    "header.dashboard": "Dashboard",
    "header.aiDecisions": "AI Holdings",
    "header.trading": "Trades",
    "header.watchlist": "Watchlist",
    "header.tooltip.github": "GitHub Repository",
    "header.tooltip.telegram": "Telegram Channel",
    "header.tooltip.theme": "Toggle Theme",

    // Loading
    "loading.text": "Loading data...",

    // Tables
    "table.realPortfolio": "Real Trading Portfolio",
    "table.simulator": "Prism Simulator",
    "table.stock": "Stock",
    "table.quantity": "Qty",
    "table.avgPrice": "Avg Price",
    "table.currentPrice": "Current",
    "table.profitRate": "Return",
    "table.profitAmount": "P&L",
    "table.totalValue": "Value",

    // Metrics
    "metrics.totalReturn": "Total Return",
    "metrics.winRate": "Win Rate",
    "metrics.avgReturn": "Avg Return",
    "metrics.avgHoldingDays": "Avg Days",
    "metrics.totalTrades": "Total Trades",
    "metrics.wins": "Wins",
    "metrics.losses": "Losses",

    // Common
    "common.won": "KRW",
    "common.krw": "₩",
    "common.percent": "%",
    "common.days": "d",
    "common.trades": "trades",
  },
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [language, setLanguageState] = useState<Language>("ko")

  // Load language from localStorage on mount
  useEffect(() => {
    const stored = localStorage.getItem("language") as Language
    if (stored === "ko" || stored === "en") {
      setLanguageState(stored)
    }
  }, [])

  // Save language to localStorage when it changes
  const setLanguage = (lang: Language) => {
    setLanguageState(lang)
    localStorage.setItem("language", lang)
  }

  // Translation function
  const t = (key: string): string => {
    return translations[language][key] || key
  }

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  )
}

export function useLanguage() {
  const context = useContext(LanguageContext)
  if (context === undefined) {
    throw new Error("useLanguage must be used within a LanguageProvider")
  }
  return context
}
