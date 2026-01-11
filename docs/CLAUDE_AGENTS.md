# AI Agent System - PRISM-INSIGHT

> **Note**: This is a detailed reference for the AI Agent System. For quick overview, see main [CLAUDE.md](../CLAUDE.md).

---

## The 13 Specialized Agents

### Analysis Team (6 Agents) - GPT-5 Based

**1. Technical Analyst** (`create_price_volume_analysis_agent`)
- **File**: `cores/agents/stock_price_agents.py`
- **Purpose**: Stock price and volume technical analysis
- **Analyzes**: Trends, moving averages, support/resistance, RSI, MACD, Bollinger Bands
- **Output**: Technical analysis section of report

**2. Trading Flow Analyst** (`create_investor_trading_analysis_agent`)
- **File**: `cores/agents/stock_price_agents.py`
- **Purpose**: Investor trading pattern analysis
- **Analyzes**: Institutional/foreign/individual trading flows, volume patterns
- **Output**: Trading flow section

**3. Financial Analyst** (`create_company_status_agent`)
- **File**: `cores/agents/company_info_agents.py`
- **Purpose**: Financial metrics and valuation
- **Analyzes**: PER, PBR, ROE, debt ratio, target prices, consensus
- **Output**: Company status section

**4. Industry Analyst** (`create_company_overview_agent`)
- **File**: `cores/agents/company_info_agents.py`
- **Purpose**: Business model and competitive position
- **Analyzes**: Business portfolio, market share, competitors, R&D, growth drivers
- **Output**: Company overview section

**5. Information Analyst** (`create_news_analysis_agent`)
- **File**: `cores/agents/news_strategy_agents.py`
- **Purpose**: News and catalyst identification
- **Analyzes**: Recent news, disclosures, industry trends, political/economic issues
- **Output**: News analysis section

**6. Market Analyst** (`create_market_index_analysis_agent`)
- **File**: `cores/agents/market_index_agents.py`
- **Purpose**: Market and macro environment
- **Analyzes**: KOSPI/KOSDAQ indices, macro indicators, global correlations
- **Output**: Market analysis section
- **Note**: Results are cached to reduce API calls

### Strategy Team (1 Agent) - GPT-5 Based

**7. Investment Strategist** (`create_investment_strategy_agent`)
- **File**: `cores/agents/news_strategy_agents.py`
- **Purpose**: Synthesize all analyses into actionable strategy
- **Integrates**: All 6 analysis reports
- **Output**: Investment strategy with recommendations for different investor types

### Communication Team (3 Agents)

**8-1. Summary Optimizer** (`telegram_summary_optimizer_agent`)
- **File**: `cores/agents/telegram_summary_optimizer_agent.py`
- **Model**: GPT-5
- **Purpose**: Convert detailed reports to Telegram-optimized summaries
- **Constraints**: 400 characters max, key points extraction
- **Output**: Concise Telegram message

**8-2. Quality Evaluator** (`telegram_summary_evaluator_agent`)
- **File**: `cores/agents/telegram_summary_evaluator_agent.py`
- **Model**: GPT-5
- **Purpose**: Evaluate summary quality and suggest improvements
- **Checks**: Accuracy, clarity, format compliance, hallucination detection
- **Process**: Iterative improvement loop until EXCELLENT rating

**8-3. Translation Specialist** (`translate_telegram_message`)
- **File**: `cores/agents/telegram_translator_agent.py`
- **Model**: GPT-5
- **Purpose**: Multi-language translation
- **Languages**: en, ja, zh, es, fr, de
- **Preserves**: Technical terms, market context, formatting

### Trading Simulation Team (2 Agents) - GPT-5 Based

> **Note**: All agents now use GPT-5 (gpt-5) as the default model. GPT-5 output formatting requires additional cleanup in `cores/utils.py` (tool artifacts, headers).

**9-1. Buy Specialist** (`create_trading_scenario_agent`)
- **File**: `cores/agents/trading_agents.py`
- **Purpose**: Buy decision-making and entry strategy
- **Evaluates**: Valuation, momentum, portfolio constraints
- **Market-Adaptive Criteria**:
  - Bull Market: Min score 6, Risk/Reward ≥ 1.5, Stop loss ≤ 10%
  - Bear/Sideways: Min score 7, Risk/Reward ≥ 2.0, Stop loss ≤ 7%
- **Output**: JSON trading scenario with entry/exit strategy

**9-2. Sell Specialist** (`create_sell_decision_agent`)
- **File**: `cores/agents/trading_agents.py`
- **Purpose**: Monitor holdings and determine sell timing
- **Monitors**: Stop-loss, profit targets, technical trends, market conditions
- **Output**: JSON sell decision with confidence score

### User Consultation Team (2 Agents) - Claude Sonnet 4.5

**10-1. Portfolio Consultant**
- **File**: `telegram_ai_bot.py`
- **Purpose**: User portfolio evaluation and advice
- **Features**: Custom advice based on user's positions, market data, latest news
- **Adapts**: Response style to user preferences

**10-2. Dialogue Manager**
- **File**: `telegram_ai_bot.py`
- **Purpose**: Maintain conversation context
- **Features**: Context memory, follow-up handling, data lookup

---

## Agent Collaboration Pattern

```python
# Pattern in cores/analysis.py
async def analyze_stock(company_name, company_code, reference_date, language="ko"):
    # 1. Get agent directory
    agents = get_agent_directory(company_name, company_code, reference_date,
                                  base_sections, language)

    # 2. Sequential execution (rate limit friendly)
    section_reports = {}
    for section in base_sections:
        if section in agents:
            agent = agents[section]

            # Special handling for market analysis (use cache)
            if section == "market_index_analysis":
                report = get_cached_or_generate_market_analysis(...)
            else:
                report = await generate_report(agent, section, ...)

            section_reports[section] = report

    # 3. Generate investment strategy (integrates all reports)
    strategy = await generate_investment_strategy(
        agents["investment_strategy"],
        section_reports,
        ...
    )

    return {
        "sections": section_reports,
        "strategy": strategy
    }
```

---

## Creating New Agents

**Template Pattern**:

```python
# File: cores/agents/your_agent.py
from mcp_agent import Agent

def create_your_agent(company_name, company_code, reference_date, language="ko"):
    """
    Create your custom agent.

    Args:
        company_name: Company name
        company_code: Stock code
        reference_date: Analysis date (YYYY-MM-DD)
        language: "ko" or "en"

    Returns:
        Agent instance
    """
    if language == "en":
        instruction = """
        You are a specialized analyst focusing on [YOUR DOMAIN].

        Analyze the stock data and provide:
        1. [Specific point 1]
        2. [Specific point 2]

        Be concise and data-driven.
        """
    else:  # Korean (default)
        instruction = """
        당신은 [도메인]을 전문으로 하는 애널리스트입니다.

        다음을 분석하세요:
        1. [분석 항목 1]
        2. [분석 항목 2]

        간결하고 데이터 중심으로 작성하세요.
        """

    return Agent(
        instruction=instruction,
        description=f"Custom Agent for {company_name}",
        # Add MCP tools if needed
        mcp_servers=["kospi_kosdaq", "firecrawl", "perplexity"],
    )

# Register in cores/agents/__init__.py
def get_agent_directory(...):
    agents = {
        # ... existing agents
        "your_section_name": lambda: create_your_agent(...),
    }
    return agents
```

---

*See also: [CLAUDE.md](../CLAUDE.md) | [CLAUDE_TASKS.md](CLAUDE_TASKS.md) | [CLAUDE_TROUBLESHOOTING.md](CLAUDE_TROUBLESHOOTING.md)*
