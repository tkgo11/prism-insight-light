export interface PortfolioSummary {
  total_stocks: number
  total_profit: number
  avg_profit_rate: number
  slot_usage: string
  slot_percentage: number
  sector_distribution: Record<string, number>
  period_distribution: Record<string, number>
}

export interface TradingSummary {
  total_trades: number
  win_count: number
  loss_count: number
  win_rate: number
  avg_profit_rate: number
  avg_holding_days: number
}

export interface AIDecisionsSummary {
  total_decisions: number
  sell_signals: number
  hold_signals: number
  adjustment_needed: number
  avg_confidence: number
}

export interface RealTradingSummary {
  total_stocks: number
  total_eval_amount: number
  total_profit_amount: number
  total_profit_rate: number
  available_amount: number
}

export interface Summary {
  portfolio: PortfolioSummary
  trading: TradingSummary
  ai_decisions: AIDecisionsSummary
  real_trading: RealTradingSummary
}

export interface Holding {
  ticker: string
  company_name?: string
  name?: string
  buy_price?: number
  buy_date?: string
  current_price: number
  last_updated?: string
  target_price?: number
  stop_loss?: number
  profit_rate: number
  holding_days?: number
  sector: string
  investment_period?: string
  quantity?: number
  avg_price?: number
  value?: number
  profit?: number
  weight?: number
  scenario?: {
    portfolio_analysis?: string
    valuation_analysis?: string
    sector_outlook?: string
    buy_score?: number
    min_score?: number
    decision?: string
    target_price?: number
    stop_loss?: number
    investment_period?: string
    rationale?: string
    sector?: string
    market_condition?: string
    max_portfolio_size?: string
    trading_scenarios?: {
      key_levels?: Record<string, any>
      sell_triggers?: string[]
      hold_conditions?: string[]
      portfolio_context?: string
    }
  }
}

export interface Trade {
  id: number
  ticker: string
  company_name: string
  buy_price: number
  buy_date: string
  sell_price: number
  sell_date: string
  profit_rate: number
  holding_days: number
  scenario?: {
    target_price?: number
    stop_loss?: number
    investment_period?: string
    sector?: string
    rationale?: string
  }
}

export interface WatchlistStock {
  id: number
  ticker: string
  company_name: string
  current_price: number
  analyzed_date: string
  buy_score: number
  min_score: number
  decision: string
  skip_reason: string
  target_price: number
  stop_loss: number
  investment_period: string
  sector: string
  portfolio_analysis?: string
  valuation_analysis?: string
  sector_outlook?: string
  market_condition?: string
  rationale?: string
  max_portfolio_size?: string
  trading_scenarios?: {
    key_levels?: Record<string, string>
    sell_triggers?: string[]
    hold_conditions?: string[]
    portfolio_context?: string
  }
  scenario?: {
    portfolio_analysis?: string
    valuation_analysis?: string
    sector_outlook?: string
    buy_score?: number
    min_score?: number
    decision?: string
    target_price?: number
    stop_loss?: number
    investment_period?: string
    rationale?: string
    sector?: string
    market_condition?: string
    max_portfolio_size?: string
    trading_scenarios?: {
      key_levels?: Record<string, string>
      sell_triggers?: string[]
      hold_conditions?: string[]
      portfolio_context?: string
    }
  }
  full_json_data?: any
}

export interface MarketCondition {
  date: string
  kospi_index: number
  kosdaq_index: number
  condition: number
  volatility: number
}

export interface AccountSummary {
  total_eval_amount: number
  total_profit_amount: number
  total_profit_rate: number
  available_amount: number
}

export interface DashboardData {
  generated_at: string
  trading_mode: string
  summary: Summary
  holdings: Holding[]
  real_portfolio: Holding[]
  account_summary: AccountSummary
  trading_history: Trade[]
  watchlist: WatchlistStock[]
  market_condition: MarketCondition[]
  holding_decisions?: HoldingDecision[]
}

export interface HoldingDecision {
  id: number
  ticker: string
  decision_date: string
  decision_time: string
  current_price: number
  should_sell: number
  sell_reason: string
  confidence: number
  technical_trend: string
  volume_analysis: string
  market_condition_impact: string
  time_factor: string
  portfolio_adjustment_needed: number
  adjustment_reason: string
  new_target_price: number
  new_stop_loss: number
  adjustment_urgency: string
  full_json_data?: any
  created_at: string
}
