"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { History, TrendingUp, TrendingDown, Award, Calendar, Target, Brain, Trophy, AlertCircle } from "lucide-react"
import type { Trade, Summary } from "@/types/dashboard"

interface TradingHistoryPageProps {
  history: Trade[]
  summary: Summary
}

export function TradingHistoryPage({ history, summary }: TradingHistoryPageProps) {
  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      style: "currency",
      currency: "KRW",
      maximumFractionDigits: 0,
    }).format(value)
  }

  const formatPercent = (value: number) => {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    return date.toLocaleDateString("ko-KR", { 
      year: "numeric",
      month: "long", 
      day: "numeric"
    })
  }

  const { total_trades, win_rate, avg_profit_rate, avg_holding_days } = summary.trading

  // 최고/최저 수익 종목
  const bestTrade = history.reduce((best, trade) => 
    (trade.profit_rate > best.profit_rate) ? trade : best
  , history[0] || { profit_rate: 0 })
  
  const worstTrade = history.reduce((worst, trade) => 
    (trade.profit_rate < worst.profit_rate) ? trade : worst
  , history[0] || { profit_rate: 0 })

  // 섹터별 수익률 (scenario.sector 활용)
  const sectorPerformance = history.reduce((acc, trade) => {
    const sector = trade.scenario?.sector || "기타"
    if (!acc[sector]) {
      acc[sector] = { total: 0, count: 0, avgProfit: 0 }
    }
    acc[sector].total += trade.profit_rate
    acc[sector].count += 1
    acc[sector].avgProfit = acc[sector].total / acc[sector].count
    return acc
  }, {} as Record<string, { total: number; count: number; avgProfit: number }>)

  const sortedSectors = Object.entries(sectorPerformance)
    .sort(([, a], [, b]) => b.avgProfit - a.avgProfit)
    .slice(0, 3)

  // 투자기간별 수익률
  const periodPerformance = history.reduce((acc, trade) => {
    const period = trade.scenario?.investment_period || "미분류"
    if (!acc[period]) {
      acc[period] = { total: 0, count: 0, avgProfit: 0 }
    }
    acc[period].total += trade.profit_rate
    acc[period].count += 1
    acc[period].avgProfit = acc[period].total / acc[period].count
    return acc
  }, {} as Record<string, { total: number; count: number; avgProfit: number }>)

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-3 rounded-lg bg-gradient-to-br from-blue-500/20 to-indigo-500/20">
            <History className="w-6 h-6 text-blue-600 dark:text-blue-400" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-foreground">거래 내역</h2>
            <p className="text-sm text-muted-foreground">매매 완료 종목 분석 및 AI 시나리오 비교</p>
          </div>
        </div>
        <Badge variant="outline" className="text-sm">
          총 {total_trades || 0}건 매매
        </Badge>
      </div>

      {/* 통계 카드 */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <History className="w-5 h-5 text-primary" />
              <span className="text-sm text-muted-foreground">총 거래</span>
            </div>
            <p className="text-3xl font-bold text-foreground">{total_trades || 0}회</p>
            <p className="text-xs text-muted-foreground mt-1">
              매매 완료 건수
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Award className="w-5 h-5 text-success" />
              <span className="text-sm text-muted-foreground">승률</span>
            </div>
            <p className="text-3xl font-bold text-success">{(win_rate || 0).toFixed(0)}%</p>
            <p className="text-xs text-muted-foreground mt-1">
              수익 거래 비율
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <TrendingUp className="w-5 h-5 text-chart-3" />
              <span className="text-sm text-muted-foreground">평균 수익률</span>
            </div>
            <p className="text-3xl font-bold text-chart-3">{formatPercent(avg_profit_rate || 0)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              거래당 평균 성과
            </p>
          </CardContent>
        </Card>

        <Card className="border-border/50">
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-2">
              <Calendar className="w-5 h-5 text-chart-4" />
              <span className="text-sm text-muted-foreground">평균 보유</span>
            </div>
            <p className="text-3xl font-bold text-chart-4">{(avg_holding_days || 0).toFixed(0)}일</p>
            <p className="text-xs text-muted-foreground mt-1">
              매수 후 매도까지
            </p>
          </CardContent>
        </Card>
      </div>

      {/* 베스트/워스트 거래 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card className="border-border/50 bg-gradient-to-br from-success/5 to-transparent">
            <CardHeader>
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <Trophy className="w-5 h-5 text-success" />
                최고 수익 거래
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <p className="text-lg font-bold text-foreground">{bestTrade.company_name}</p>
                  <p className="text-sm text-muted-foreground">{bestTrade.ticker}</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">수익률</p>
                    <p className="text-xl font-bold text-success">{formatPercent(bestTrade.profit_rate)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">보유기간</p>
                    <p className="text-xl font-bold text-foreground">{bestTrade.holding_days}일</p>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/30">
                  <p className="text-xs text-muted-foreground">
                    {formatDate(bestTrade.buy_date)} → {formatDate(bestTrade.sell_date)}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-border/50 bg-gradient-to-br from-destructive/5 to-transparent">
            <CardHeader>
              <CardTitle className="text-base font-semibold flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-destructive" />
                최저 수익 거래
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <p className="text-lg font-bold text-foreground">{worstTrade.company_name}</p>
                  <p className="text-sm text-muted-foreground">{worstTrade.ticker}</p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">수익률</p>
                    <p className="text-xl font-bold text-destructive">{formatPercent(worstTrade.profit_rate)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">보유기간</p>
                    <p className="text-xl font-bold text-foreground">{worstTrade.holding_days}일</p>
                  </div>
                </div>
                <div className="pt-2 border-t border-border/30">
                  <p className="text-xs text-muted-foreground">
                    {formatDate(worstTrade.buy_date)} → {formatDate(worstTrade.sell_date)}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 섹터별 & 기간별 성과 */}
      {history.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {sortedSectors.length > 0 && (
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-primary" />
                  섹터별 수익률 TOP 3
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {sortedSectors.map(([sector, data], index) => (
                    <div key={sector} className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/20 text-primary font-bold text-sm">
                          {index + 1}
                        </div>
                        <div>
                          <p className="font-medium text-foreground">{sector}</p>
                          <p className="text-xs text-muted-foreground">{data.count}건 거래</p>
                        </div>
                      </div>
                      <p className={`text-lg font-bold ${data.avgProfit >= 0 ? "text-success" : "text-destructive"}`}>
                        {formatPercent(data.avgProfit)}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {Object.keys(periodPerformance).length > 0 && (
            <Card className="border-border/50">
              <CardHeader>
                <CardTitle className="text-base font-semibold flex items-center gap-2">
                  <Calendar className="w-5 h-5 text-chart-3" />
                  투자기간별 성과
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {Object.entries(periodPerformance).map(([period, data]) => (
                    <div key={period} className="flex items-center justify-between p-3 rounded-lg bg-muted/30">
                      <div>
                        <p className="font-medium text-foreground">{period}</p>
                        <p className="text-xs text-muted-foreground">{data.count}건 거래</p>
                      </div>
                      <p className={`text-lg font-bold ${data.avgProfit >= 0 ? "text-success" : "text-destructive"}`}>
                        {formatPercent(data.avgProfit)}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* 거래 상세 내역 */}
      <Card className="border-border/50">
        <CardHeader>
          <CardTitle className="text-lg font-semibold">거래 상세 내역</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {history.map((trade) => (
              <Card key={trade.id} className="border-border/30 bg-muted/20">
                <CardContent className="p-6">
                  <div className="space-y-4">
                    {/* 종목 헤더 */}
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-3 mb-2">
                          <h3 className="text-lg font-bold text-foreground">{trade.company_name}</h3>
                          <Badge variant="outline" className="text-xs">{trade.ticker}</Badge>
                          {trade.scenario?.sector && (
                            <Badge variant="secondary" className="text-xs">{trade.scenario.sector}</Badge>
                          )}
                          {trade.scenario?.investment_period && (
                            <Badge variant="secondary" className="text-xs">{trade.scenario.investment_period}</Badge>
                          )}
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {formatDate(trade.buy_date)} → {formatDate(trade.sell_date)} ({trade.holding_days}일 보유)
                        </p>
                      </div>
                      <div className="text-right">
                        <p className="text-sm text-muted-foreground mb-1">수익률</p>
                        <p className={`text-2xl font-bold ${trade.profit_rate >= 0 ? "text-success" : "text-destructive"}`}>
                          {formatPercent(trade.profit_rate)}
                        </p>
                      </div>
                    </div>

                    {/* 거래 정보 */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-lg bg-background border border-border/50">
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">매수가</p>
                        <p className="font-semibold text-foreground">{formatCurrency(trade.buy_price)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">매도가</p>
                        <p className="font-semibold text-foreground">{formatCurrency(trade.sell_price)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">AI 목표가</p>
                        <p className="font-semibold text-success">
                          {trade.scenario?.target_price ? formatCurrency(trade.scenario.target_price) : "-"}
                        </p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground mb-1">목표 달성률</p>
                        <p className="font-semibold text-foreground">
                          {trade.scenario?.target_price 
                            ? `${((trade.sell_price / trade.scenario.target_price) * 100).toFixed(0)}%`
                            : "-"}
                        </p>
                      </div>
                    </div>

                    {/* AI 시나리오 */}
                    {trade.scenario && (
                      <div className="space-y-3">
                        {/* 투자 근거 */}
                        <div className="p-4 rounded-lg bg-primary/10 border border-primary/20">
                          <div className="flex items-start gap-2">
                            <Brain className="w-4 h-4 text-primary mt-0.5 flex-shrink-0" />
                            <div className="flex-1">
                              <p className="text-sm font-semibold text-primary mb-2">AI 투자 근거</p>
                              {trade.scenario.rationale && (
                                <p className="text-sm text-foreground leading-relaxed">{trade.scenario.rationale}</p>
                              )}
                            </div>
                          </div>
                        </div>

                        {/* 포트폴리오 분석 */}
                        {trade.scenario.portfolio_analysis && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">포트폴리오 분석</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.portfolio_analysis}</p>
                          </div>
                        )}

                        {/* 밸류에이션 분석 */}
                        {trade.scenario.valuation_analysis && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">밸류에이션 분석</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.valuation_analysis}</p>
                          </div>
                        )}

                        {/* 섹터 전망 */}
                        {trade.scenario.sector_outlook && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">섹터 전망</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.sector_outlook}</p>
                          </div>
                        )}

                        {/* 시장 상황 */}
                        {trade.scenario.market_condition && (
                          <div className="p-4 rounded-lg bg-muted/30 border border-border/50">
                            <p className="text-xs font-semibold text-muted-foreground mb-2">당시 시장 상황</p>
                            <p className="text-sm text-foreground leading-relaxed">{trade.scenario.market_condition}</p>
                          </div>
                        )}

                        {/* 매매 시나리오 상세 */}
                        {trade.scenario.trading_scenarios && (
                          <div className="p-4 rounded-lg bg-chart-1/10 border border-chart-1/20">
                            <p className="text-sm font-semibold text-chart-1 mb-3">AI 매매 시나리오 상세</p>
                            
                            {/* 주요 레벨 */}
                            {trade.scenario.trading_scenarios.key_levels && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">주요 가격대</p>
                                <div className="grid grid-cols-2 gap-2 text-xs">
                                  {trade.scenario.trading_scenarios.key_levels.primary_support && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">1차 지지: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.primary_support}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.secondary_support && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">2차 지지: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.secondary_support}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.primary_resistance && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">1차 저항: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.primary_resistance}</span>
                                    </div>
                                  )}
                                  {trade.scenario.trading_scenarios.key_levels.secondary_resistance && (
                                    <div className="p-2 rounded bg-background/50">
                                      <span className="text-muted-foreground">2차 저항: </span>
                                      <span className="font-medium text-foreground">{trade.scenario.trading_scenarios.key_levels.secondary_resistance}</span>
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}

                            {/* 매도 트리거 */}
                            {trade.scenario.trading_scenarios.sell_triggers && trade.scenario.trading_scenarios.sell_triggers.length > 0 && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">매도 트리거</p>
                                <ul className="space-y-1.5">
                                  {trade.scenario.trading_scenarios.sell_triggers.map((trigger, idx) => (
                                    <li key={idx} className="text-xs text-foreground leading-relaxed pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-chart-1">
                                      {trigger}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* 보유 조건 */}
                            {trade.scenario.trading_scenarios.hold_conditions && trade.scenario.trading_scenarios.hold_conditions.length > 0 && (
                              <div className="mb-4">
                                <p className="text-xs font-semibold text-muted-foreground mb-2">보유 조건</p>
                                <ul className="space-y-1.5">
                                  {trade.scenario.trading_scenarios.hold_conditions.map((condition, idx) => (
                                    <li key={idx} className="text-xs text-foreground leading-relaxed pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-success">
                                      {condition}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}

                            {/* 포트폴리오 맥락 */}
                            {trade.scenario.trading_scenarios.portfolio_context && (
                              <div>
                                <p className="text-xs font-semibold text-muted-foreground mb-2">포트폴리오 맥락</p>
                                <p className="text-xs text-foreground leading-relaxed">{trade.scenario.trading_scenarios.portfolio_context}</p>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </CardContent>
      </Card>

      {history.length === 0 && (
        <Card className="border-border/50">
          <CardContent className="p-12 text-center">
            <History className="w-12 h-12 text-muted-foreground mx-auto mb-4 opacity-50" />
            <p className="text-muted-foreground">아직 거래 내역이 없습니다.</p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
