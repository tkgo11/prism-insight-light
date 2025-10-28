"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import type { MarketCondition } from "@/types/dashboard"

interface PerformanceChartProps {
  data: MarketCondition[]
}

export function PerformanceChart({ data }: PerformanceChartProps) {
  const formatNumber = (value: number) => {
    return new Intl.NumberFormat("ko-KR", {
      maximumFractionDigits: 0,
    }).format(value)
  }

  // 데이터를 날짜 기준으로 오름차순 정렬
  const sortedData = [...data].sort((a, b) => {
    return new Date(a.date).getTime() - new Date(b.date).getTime()
  })

  // Y축 도메인 계산 함수
  const getYAxisDomain = (values: number[]) => {
    if (values.length === 0) return [0, 3000]
    
    const min = Math.min(...values)
    const max = Math.max(...values)
    const padding = (max - min) * 0.05
    
    return [Math.floor(min - padding), Math.ceil(max + padding)]
  }

  const kospiValues = sortedData.map(d => d.kospi_index).filter(v => v > 0)
  const kosdaqValues = sortedData.map(d => d.kosdaq_index).filter(v => v > 0)
  
  const [kospiMin, kospiMax] = getYAxisDomain(kospiValues)
  const [kosdaqMin, kosdaqMax] = getYAxisDomain(kosdaqValues)

  // 전일 대비 변화율 계산
  const getLatestChange = (values: number[]) => {
    if (values.length < 2) return { current: 0, change: 0, changePercent: 0 }
    const current = values[values.length - 1]
    const previous = values[values.length - 2]
    const change = current - previous
    const changePercent = (change / previous) * 100
    return { current, change, changePercent }
  }

  const kospiStats = getLatestChange(kospiValues)
  const kosdaqStats = getLatestChange(kosdaqValues)

  if (!data || data.length === 0) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">KOSPI 지수</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-center h-[200px] text-muted-foreground">
              데이터가 없습니다.
            </div>
          </CardContent>
        </Card>
        <Card className="border-border/50">
          <CardHeader>
            <CardTitle className="text-lg font-semibold">KOSDAQ 지수</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-center h-[200px] text-muted-foreground">
              데이터가 없습니다.
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const IndexCard = ({ 
    title, 
    dataKey, 
    color, 
    gradientId,
    yMin,
    yMax,
    stats
  }: { 
    title: string
    dataKey: "kospi_index" | "kosdaq_index"
    color: string
    gradientId: string
    yMin: number
    yMax: number
    stats: { current: number, change: number, changePercent: number }
  }) => (
    <Card className="border-border/50">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg font-semibold">{title}</CardTitle>
          <div className="text-right">
            <p className="text-2xl font-bold">{formatNumber(stats.current)}</p>
            <p className={`text-sm font-medium ${stats.change >= 0 ? 'text-success' : 'text-destructive'}`}>
              {stats.change >= 0 ? '+' : ''}{formatNumber(stats.change)} ({stats.changePercent >= 0 ? '+' : ''}{stats.changePercent.toFixed(2)}%)
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={sortedData}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.8} />
                <stop offset="95%" stopColor={color} stopOpacity={0.1} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis
              dataKey="date"
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={(value) => {
                const date = new Date(value)
                return `${date.getMonth() + 1}/${date.getDate()}`
              }}
            />
            <YAxis
              stroke="hsl(var(--muted-foreground))"
              fontSize={11}
              tickLine={false}
              axisLine={false}
              tickFormatter={formatNumber}
              domain={[yMin, yMax]}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "hsl(var(--popover))",
                border: "1px solid hsl(var(--border))",
                borderRadius: "8px",
                padding: "8px 12px",
              }}
              labelStyle={{ color: "hsl(var(--popover-foreground))", fontWeight: 600 }}
              formatter={(value: number) => [formatNumber(value), title]}
            />
            <Area
              type="monotone"
              dataKey={dataKey}
              stroke={color}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              fillOpacity={1}
            />
          </AreaChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <IndexCard
        title="KOSPI"
        dataKey="kospi_index"
        color="#3b82f6"
        gradientId="colorKospi"
        yMin={kospiMin}
        yMax={kospiMax}
        stats={kospiStats}
      />
      <IndexCard
        title="KOSDAQ"
        dataKey="kosdaq_index"
        color="#10b981"
        gradientId="colorKosdaq"
        yMin={kosdaqMin}
        yMax={kosdaqMax}
        stats={kosdaqStats}
      />
    </div>
  )
}
