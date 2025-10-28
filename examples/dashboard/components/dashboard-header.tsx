"use client"

import { Moon, Sun, TrendingUp } from "lucide-react"
import { useTheme } from "next-themes"
import { Button } from "@/components/ui/button"

interface DashboardHeaderProps {
  activeTab: "dashboard" | "ai-decisions" | "trading" | "watchlist"
  onTabChange: (tab: "dashboard" | "ai-decisions" | "trading" | "watchlist") => void
  lastUpdated?: string
}

export function DashboardHeader({ activeTab, onTabChange, lastUpdated }: DashboardHeaderProps) {
  const { theme, setTheme } = useTheme()

  const formatLastUpdated = () => {
    if (!lastUpdated) return "실시간 업데이트"
    
    try {
      const date = new Date(lastUpdated)
      if (isNaN(date.getTime())) return "실시간 업데이트"
      return date.toLocaleString("ko-KR", {
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return "실시간 업데이트"
    }
  }

  return (
    <header className="sticky top-0 z-50 w-full border-b border-border/40 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto px-4 max-w-[1600px]">
        <div className="flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-primary via-purple-600 to-blue-600">
              <TrendingUp className="w-6 h-6 text-white" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold bg-gradient-to-r from-primary via-purple-600 to-blue-600 bg-clip-text text-transparent">
                  Prism Insight
                </h1>
                <span className="px-2 py-0.5 text-xs font-semibold rounded-full bg-gradient-to-r from-primary to-purple-600 text-white">
                  Season 2
                </span>
              </div>
              <div className="flex items-center gap-3 mt-0.5">
                <p className="text-xs text-muted-foreground">
                  시작: 2025.09.29
                </p>
                <span className="text-muted-foreground/30">•</span>
                <p className="text-xs text-muted-foreground">
                  업데이트: {formatLastUpdated()}
                </p>
              </div>
            </div>
          </div>

          <nav className="hidden md:flex items-center gap-1">
            <Button
              variant={activeTab === "dashboard" ? "secondary" : "ghost"}
              onClick={() => onTabChange("dashboard")}
              className="font-medium"
            >
              대시보드
            </Button>
            <Button
              variant={activeTab === "ai-decisions" ? "secondary" : "ghost"}
              onClick={() => onTabChange("ai-decisions")}
              className="font-medium"
            >
              AI 보유 분석
            </Button>
            <Button
              variant={activeTab === "trading" ? "secondary" : "ghost"}
              onClick={() => onTabChange("trading")}
              className="font-medium"
            >
              거래 내역
            </Button>
            <Button
              variant={activeTab === "watchlist" ? "secondary" : "ghost"}
              onClick={() => onTabChange("watchlist")}
              className="font-medium"
            >
              관심 종목
            </Button>
          </nav>

          <Button
            variant="ghost"
            size="icon"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="rounded-full"
          >
            <Sun className="h-5 w-5 rotate-0 scale-100 transition-all dark:-rotate-90 dark:scale-0" />
            <Moon className="absolute h-5 w-5 rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
            <span className="sr-only">테마 전환</span>
          </Button>
        </div>

        {/* Mobile Navigation */}
        <nav className="md:hidden flex items-center gap-1 pb-3 overflow-x-auto">
          <Button
            variant={activeTab === "dashboard" ? "secondary" : "ghost"}
            onClick={() => onTabChange("dashboard")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            대시보드
          </Button>
          <Button
            variant={activeTab === "ai-decisions" ? "secondary" : "ghost"}
            onClick={() => onTabChange("ai-decisions")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            AI 보유 분석
          </Button>
          <Button
            variant={activeTab === "trading" ? "secondary" : "ghost"}
            onClick={() => onTabChange("trading")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            거래 내역
          </Button>
          <Button
            variant={activeTab === "watchlist" ? "secondary" : "ghost"}
            onClick={() => onTabChange("watchlist")}
            size="sm"
            className="font-medium whitespace-nowrap"
          >
            관심 종목
          </Button>
        </nav>
      </div>
    </header>
  )
}
