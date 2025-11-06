"use client"

import { Github, Send } from "lucide-react"
import { Card } from "@/components/ui/card"

export function ProjectFooter() {
  return (
    <footer className="mt-12 border-t border-border/40">
      <div className="container mx-auto px-4 py-8 max-w-[1600px]">
        <Card className="bg-gradient-to-br from-background/50 to-muted/30 border-border/50 backdrop-blur-sm">
          <div className="p-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              {/* 프로젝트 소개 */}
              <div className="space-y-3">
                <h3 className="text-lg font-bold bg-gradient-to-r from-primary to-primary/60 bg-clip-text text-transparent">
                  🔍 PRISM-INSIGHT
                </h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  AI 기반 주식 분석 및 매매 시스템
                  <br />
                  <span className="text-xs">
                    완전 오픈소스 무료 프로젝트 • MIT License
                  </span>
                </p>
                <div className="pt-2">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
                      <span>GPT-4.1</span>
                    </div>
                    <span>•</span>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
                      <span>GPT-5</span>
                    </div>
                    <span>•</span>
                    <div className="flex items-center gap-1">
                      <div className="w-2 h-2 rounded-full bg-purple-500 animate-pulse" />
                      <span>Claude 4.5</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* 주요 기능 */}
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-foreground/80">주요 기능</h4>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>12개 AI 에이전트 협업 분석</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>급등주 자동 포착 & 리포트</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>매매 시뮬레이션 & 자동매매</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-primary mt-0.5">✓</span>
                    <span>실시간 성과 대시보드</span>
                  </li>
                </ul>
              </div>

              {/* 링크 */}
              <div className="space-y-3">
                <h4 className="text-sm font-semibold text-foreground/80">바로가기</h4>
                <div className="flex flex-col gap-3">
                  {/* GitHub */}
                  <a
                    href="https://github.com/dragon1086/prism-insight"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-background/60 hover:bg-background/80 border border-border/50 hover:border-border transition-all duration-200 hover:shadow-md"
                  >
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-primary/20 to-primary/5 group-hover:from-primary/30 group-hover:to-primary/10 transition-all">
                      <Github className="h-5 w-5 text-primary" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-foreground group-hover:text-primary transition-colors">
                        GitHub Repository
                      </div>
                      <div className="text-xs text-muted-foreground">
                        소스코드 & 이슈 관리
                      </div>
                    </div>
                  </a>

                  {/* Telegram */}
                  <a
                    href="https://t.me/stock_ai_agent"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group flex items-center gap-3 p-3 rounded-lg bg-background/60 hover:bg-background/80 border border-border/50 hover:border-border transition-all duration-200 hover:shadow-md"
                  >
                    <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-gradient-to-br from-blue-500/20 to-blue-500/5 group-hover:from-blue-500/30 group-hover:to-blue-500/10 transition-all">
                      <Send className="h-5 w-5 text-blue-500" />
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-foreground group-hover:text-blue-500 transition-colors">
                        Telegram Channel
                      </div>
                      <div className="text-xs text-muted-foreground">
                        급등주 & 분석 리포트
                      </div>
                    </div>
                  </a>
                </div>

                {/* Star 통계 */}
                <div className="pt-2">
                  <a
                    href="https://github.com/dragon1086/prism-insight"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-primary transition-colors"
                  >
                    <span>⭐</span>
                    <span>10주 만에 250+ Stars 달성</span>
                  </a>
                </div>
              </div>
            </div>

            {/* 하단 구분선 및 저작권 */}
            <div className="mt-6 pt-6 border-t border-border/30">
              <div className="flex flex-col md:flex-row justify-between items-center gap-4 text-xs text-muted-foreground">
                <div className="flex items-center gap-2">
                  <span>© 2025 PRISM-INSIGHT</span>
                  <span className="hidden md:inline">•</span>
                  <span className="text-xs">All rights reserved</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="px-2 py-1 rounded-md bg-primary/10 text-primary text-xs font-medium">
                    Open Source
                  </span>
                  <span className="px-2 py-1 rounded-md bg-green-500/10 text-green-500 text-xs font-medium">
                    MIT License
                  </span>
                </div>
              </div>
            </div>

            {/* 면책 조항 */}
            <div className="mt-4 pt-4 border-t border-border/20">
              <p className="text-xs text-muted-foreground/60 text-center leading-relaxed">
                ⚠️ 본 시스템에서 제공하는 분석 정보는 투자 참고용이며, 투자 권유를 목적으로 하지 않습니다. 
                모든 투자 결정과 그에 따른 손익은 투자자 본인의 책임입니다.
              </p>
            </div>
          </div>
        </Card>
      </div>
    </footer>
  )
}
