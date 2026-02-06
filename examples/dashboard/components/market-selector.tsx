"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { Market } from "@/types/dashboard"

interface MarketSelectorProps {
  market: Market
  onMarketChange: (market: Market) => void
}

export function MarketSelector({ market, onMarketChange }: MarketSelectorProps) {
  const [mounted, setMounted] = useState(false)

  // Hydration fix
  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) {
    return null
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center rounded-full border border-border/50 bg-muted/30 p-0.5">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onMarketChange("KR")}
              className={`rounded-full px-3 h-8 font-medium transition-all ${
                market === "KR"
                  ? "bg-gradient-to-r from-blue-600 to-indigo-600 text-white hover:from-blue-700 hover:to-indigo-700"
                  : "hover:bg-muted text-muted-foreground"
              }`}
            >
              <span className="mr-1.5">🇰🇷</span>
              <span className="text-sm">KR</span>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onMarketChange("US")}
              className={`rounded-full px-3 h-8 font-medium transition-all ${
                market === "US"
                  ? "bg-gradient-to-r from-emerald-600 to-teal-600 text-white hover:from-emerald-700 hover:to-teal-700"
                  : "hover:bg-muted text-muted-foreground"
              }`}
            >
              <span className="mr-1.5">🇺🇸</span>
              <span className="text-sm">US</span>
            </Button>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">
            {market === "KR" ? "한국 주식 시장" : "US Stock Market"}
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// Hook for managing market state with localStorage persistence
export function useMarket(): [Market, (market: Market) => void] {
  const [market, setMarketState] = useState<Market>("KR")

  useEffect(() => {
    // Load from localStorage on mount
    const stored = localStorage.getItem("prism-market") as Market
    if (stored === "KR" || stored === "US") {
      setMarketState(stored)
    }
  }, [])

  const setMarket = (newMarket: Market) => {
    setMarketState(newMarket)
    localStorage.setItem("prism-market", newMarket)
  }

  return [market, setMarket]
}
