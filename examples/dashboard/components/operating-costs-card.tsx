"use client"

import { Server, ExternalLink, Heart } from "lucide-react"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import Image from "next/image"

interface OperatingCost {
  name: string
  amount: number
  logo?: string
  icon?: any
  color: string
  gradient: string
}

interface OperatingCostsCardProps {
  costs?: {
    server_hosting: number
    openai_api: number
    anthropic_api: number
    firecrawl_api: number
    perplexity_api: number
    month: string
  }
}

export function OperatingCostsCard({ costs }: OperatingCostsCardProps) {
  // 기본값 설정 (2025년 10월)
  const defaultCosts = {
    server_hosting: 31.68,
    openai_api: 95.82,
    anthropic_api: 18.2,
    firecrawl_api: 19,
    perplexity_api: 9.9,
    month: "2025-10"
  }

  const actualCosts = costs || defaultCosts

  const costItems: OperatingCost[] = [
    {
      name: "서버 호스팅",
      amount: actualCosts.server_hosting,
      icon: Server,
      color: "text-blue-600 dark:text-blue-400",
      gradient: "from-blue-500/20 to-blue-500/5"
    },
    {
      name: "OpenAI API",
      amount: actualCosts.openai_api,
      logo: "/openai_logo.svg",
      color: "text-green-600 dark:text-green-400",
      gradient: "from-green-500/20 to-green-500/5"
    },
    {
      name: "Anthropic API",
      amount: actualCosts.anthropic_api,
      logo: "/claude_logo.svg",
      color: "text-orange-600 dark:text-orange-400",
      gradient: "from-orange-500/20 to-orange-500/5"
    },
    {
      name: "Firecrawl API",
      amount: actualCosts.firecrawl_api,
      logo: "/firecrawl_logo.svg",
      color: "text-purple-600 dark:text-purple-400",
      gradient: "from-purple-500/20 to-purple-500/5"
    },
    {
      name: "Perplexity API",
      amount: actualCosts.perplexity_api,
      logo: "/perplexity_logo.svg",
      color: "text-pink-600 dark:text-pink-400",
      gradient: "from-pink-500/20 to-pink-500/5"
    }
  ]

  const totalCost = costItems.reduce((sum, item) => sum + item.amount, 0)

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value)
  }

  // 월 표시 포맷팅
  const formatMonth = (monthStr: string) => {
    const [year, month] = monthStr.split('-')
    return `${year}년 ${parseInt(month)}월`
  }

  return (
    <Card className="border-2 border-primary/20 shadow-xl bg-gradient-to-br from-primary/5 via-background to-background">
      <CardContent className="p-6">
        <div className="space-y-6">
          {/* 헤더 */}
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <Heart className="w-5 h-5 text-red-500 fill-red-500 animate-pulse" />
                <h2 className="text-xl font-bold text-foreground">
                  프로젝트 운영 비용 투명 공개
                </h2>
              </div>
              <p className="text-sm text-muted-foreground">
                오픈소스 프로젝트의 지속 가능한 운영을 위해 전월 비용을 공개합니다
              </p>
            </div>
            <div className="text-right space-y-1">
              <div className="text-xs text-muted-foreground">
                {formatMonth(actualCosts.month)} 기준
              </div>
              <div className="text-3xl font-bold text-primary">
                {formatCurrency(totalCost)}
              </div>
              <div className="text-xs text-muted-foreground">
                / 월
              </div>
            </div>
          </div>

          {/* 비용 항목들 */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {costItems.map((item, index) => {
              const Icon = item.icon
              const percentage = (item.amount / totalCost) * 100
              
              return (
                <div
                  key={index}
                  className="relative overflow-hidden rounded-lg border border-border/50 hover:border-border transition-all duration-300 hover:shadow-md"
                >
                  <div className={`absolute inset-0 bg-gradient-to-br ${item.gradient} opacity-50`} />
                  <div className="relative p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      {item.logo ? (
                        <div className="w-6 h-6 relative flex items-center justify-center">
                          <Image 
                            src={item.logo} 
                            alt={item.name}
                            width={24}
                            height={24}
                            className="object-contain w-full h-full"
                            style={{ maxWidth: '100%', maxHeight: '100%' }}
                          />
                        </div>
                      ) : (
                        Icon && <Icon className={`w-6 h-6 ${item.color}`} />
                      )}
                      <span className="text-xs font-medium text-muted-foreground">
                        {percentage.toFixed(0)}%
                      </span>
                    </div>
                    <div className="space-y-1">
                      <div className="text-xs font-medium text-muted-foreground">
                        {item.name}
                      </div>
                      <div className="text-lg font-bold text-foreground">
                        {formatCurrency(item.amount)}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* 하단 CTA */}
          <div className="flex items-center justify-between p-4 rounded-lg bg-primary/5 border border-primary/20">
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">
                이 프로젝트가 도움이 되셨나요?
              </p>
              <p className="text-xs text-muted-foreground">
                GitHub Sponsor를 통해 프로젝트의 지속 가능한 개발을 지원해주세요
              </p>
            </div>
            <Button
              variant="default"
              className="gap-2 bg-primary hover:bg-primary/90"
              onClick={() => window.open('https://github.com/sponsors/dragon1086', '_blank')}
            >
              <Heart className="w-4 h-4" />
              스폰서 되기
              <ExternalLink className="w-3 h-3" />
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
