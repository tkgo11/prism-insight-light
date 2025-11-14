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
    "table.stockName": "종목명",
    "table.sector": "섹터",
    "table.quantity": "수량",
    "table.avgPrice": "평균단가",
    "table.currentPrice": "현재가",
    "table.buyPrice": "매수가",
    "table.targetPrice": "목표가",
    "table.stopLoss": "손절가",
    "table.profitRate": "수익률",
    "table.profitAmount": "평가손익",
    "table.totalValue": "평가금액",
    "table.holdingDays": "보유일",
    "table.period": "기간",
    "table.weight": "비중",
    "table.unknown": "알 수 없음",

    // Metrics Cards - Real Trading
    "metrics.realTrading": "실전투자 (Season 2)",
    "metrics.realTotalAssets": "실전 총 자산",
    "metrics.realHoldingsProfit": "실전 보유종목 평가손익",
    "metrics.realProfitDist": "실전 포트폴리오 손익 분포",
    "metrics.startAmount": "시작금액",
    "metrics.deposit": "예수금",
    "metrics.stocks": "개 종목",
    "metrics.assetsDesc": "평가금액 + 예수금",
    "metrics.holdingsProfitDesc": "현재 보유중인 종목의 손익",
    "metrics.excludeRealized": "실현손익 제외",
    "metrics.profitDistDesc": "현재 보유 종목 수익/손실 현황",
    "metrics.fullyInvested": "전액 투자중",
    "metrics.wins": "승",
    "metrics.losses": "패",
    "metrics.breakEven": "보합",
    "metrics.totalStocks": "총",
    "metrics.winRate": "승률",

    // Metrics Cards - Simulator
    "metrics.simulator": "프리즘 시뮬레이터",
    "metrics.simSoldProfit": "시뮬레이터 매도종목 누적수익률",
    "metrics.simAvgHoldingDays": "시뮬레이터 평균 보유기간",
    "metrics.simCurrentProfit": "시뮬레이터 보유종목 누적수익률",
    "metrics.waitingSell": "매도 대기중",
    "metrics.onlyHolding": "현재 보유만 존재",
    "metrics.updateOnSell": "매도 시 업데이트",
    "metrics.soldProfitDesc": "매도 완료한 종목 수익률 합계",
    "metrics.sold": "매도",
    "metrics.soldBasis": "건 매도 기준",
    "metrics.needStrategy": "보유 전략 확인 필요",
    "metrics.avgHoldingDesc": "매도까지 평균 소요 기간",
    "metrics.holding": "보유",
    "metrics.avgProfit": "평균",
    "metrics.slotUsage": "슬롯 사용률",
    "metrics.currentProfitDesc": "현재 보유 종목 수익률 합계",
    "metrics.started": "시작",
    "metrics.elapsed": "일 경과",

    // Operating Costs
    "costs.title": "프로젝트 운영 비용 투명 공개",
    "costs.description": "오픈소스 프로젝트의 지속 가능한 운영을 위해 전월 비용을 공개합니다",
    "costs.serverHosting": "서버 호스팅",
    "costs.basis": "기준",
    "costs.perMonth": "/ 월",
    "costs.year": "년",
    "costs.month": "월",
    "costs.helpQuestion": "이 프로젝트가 도움이 되셨나요?",
    "costs.sponsorDesc": "GitHub Sponsor를 통해 프로젝트의 지속 가능한 개발을 지원해주세요",
    "costs.becomeSponsor": "스폰서 되기",

    // Badges
    "badge.realTrading": "실전투자",
    "badge.aiSimulation": "AI 시뮬레이션",
    "badge.season2": "Season 2",

    // Date/Time
    "date.year": "년",
    "date.month": "월",
    "date.day": "일",

    // Common
    "common.won": "원",
    "common.krw": "₩",
    "common.percent": "%",
    "common.days": "일",
    "common.trades": "건",
    "common.shares": "주",
    "metrics.totalReturn": "누적 수익률",
    "metrics.avgReturn": "평균 수익률",
    "metrics.avgHoldingDays": "평균 보유일",
    "metrics.totalTrades": "총 거래",
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
    "table.stockName": "Stock Name",
    "table.sector": "Sector",
    "table.quantity": "Qty",
    "table.avgPrice": "Avg Price",
    "table.currentPrice": "Current",
    "table.buyPrice": "Buy Price",
    "table.targetPrice": "Target",
    "table.stopLoss": "Stop Loss",
    "table.profitRate": "Return",
    "table.profitAmount": "P&L",
    "table.totalValue": "Value",
    "table.holdingDays": "Days",
    "table.period": "Period",
    "table.weight": "Weight",
    "table.unknown": "Unknown",

    // Metrics Cards - Real Trading
    "metrics.realTrading": "Real Trading (Season 2)",
    "metrics.realTotalAssets": "Real Total Assets",
    "metrics.realHoldingsProfit": "Real Holdings P&L",
    "metrics.realProfitDist": "Real Profit Distribution",
    "metrics.startAmount": "Start Amount",
    "metrics.deposit": "Cash",
    "metrics.stocks": " stocks",
    "metrics.assetsDesc": "Holdings + Cash",
    "metrics.holdingsProfitDesc": "Current holdings P&L",
    "metrics.excludeRealized": "Unrealized only",
    "metrics.profitDistDesc": "Profit/Loss breakdown",
    "metrics.fullyInvested": "Fully invested",
    "metrics.wins": "W",
    "metrics.losses": "L",
    "metrics.breakEven": "Even",
    "metrics.totalStocks": "Total",
    "metrics.winRate": "Win Rate",

    // Metrics Cards - Simulator
    "metrics.simulator": "Prism Simulator",
    "metrics.simSoldProfit": "Sim Sold Total Return",
    "metrics.simAvgHoldingDays": "Sim Avg Holding Days",
    "metrics.simCurrentProfit": "Sim Current Holdings Return",
    "metrics.waitingSell": "Awaiting sale",
    "metrics.onlyHolding": "Only holdings exist",
    "metrics.updateOnSell": "Updates on sale",
    "metrics.soldProfitDesc": "Total return from sold stocks",
    "metrics.sold": "sold",
    "metrics.soldBasis": " sold basis",
    "metrics.needStrategy": "Review strategy",
    "metrics.avgHoldingDesc": "Avg days until sale",
    "metrics.holding": "holding",
    "metrics.avgProfit": "Avg",
    "metrics.slotUsage": "Slot usage",
    "metrics.currentProfitDesc": "Total return from current holdings",
    "metrics.started": "Started",
    "metrics.elapsed": "d elapsed",

    // Operating Costs
    "costs.title": "Project Operating Costs Transparency",
    "costs.description": "Disclosing last month's costs for sustainable open-source operations",
    "costs.serverHosting": "Server Hosting",
    "costs.basis": "as of",
    "costs.perMonth": "/ mo",
    "costs.year": "",
    "costs.month": "",
    "costs.helpQuestion": "Has this project helped you?",
    "costs.sponsorDesc": "Support sustainable development via GitHub Sponsor",
    "costs.becomeSponsor": "Become a Sponsor",

    // Badges
    "badge.realTrading": "Real Trading",
    "badge.aiSimulation": "AI Simulation",
    "badge.season2": "Season 2",

    // Date/Time
    "date.year": "-",
    "date.month": "-",
    "date.day": "",

    // Common
    "common.won": "KRW",
    "common.krw": "₩",
    "common.percent": "%",
    "common.days": "d",
    "common.trades": "trades",
    "common.shares": " shares",
    "metrics.totalReturn": "Total Return",
    "metrics.avgReturn": "Avg Return",
    "metrics.avgHoldingDays": "Avg Days",
    "metrics.totalTrades": "Total Trades",
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
