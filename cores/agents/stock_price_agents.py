from mcp_agent.agents.agent import Agent
from cores.language_config import Language
from cores.agents.prompt_templates import PromptTemplates

def create_price_volume_analysis_agent(company_name, company_code, reference_date, max_years_ago, max_years, language: str = "ko"):
    """
    주가 및 거래량 분석 에이전트 생성

    Args:
        company_name: 기업명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        max_years_ago: 분석 시작일 (YYYYMMDD)
        max_years: 분석 기간 (년)
        language: Language code ("ko" or "en")

    Returns:
        Agent: 주가 및 거래량 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_price_volume_analysis_prompt(
        company_name, company_code, reference_date, max_years_ago, max_years, lang
    )

    return Agent(
        name="price_volume_analysis_agent",
        instruction=instruction,
        server_names=["kospi_kosdaq"]
    )


def create_investor_trading_analysis_agent(company_name, company_code, reference_date, max_years_ago, max_years, language: str = "ko"):
    """
    투자자 거래 동향 분석 에이전트 생성

    Args:
        company_name: 기업명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        max_years_ago: 분석 시작일 (YYYYMMDD)
        max_years: 분석 기간 (년)
        language: Language code ("ko" or "en")

    Returns:
        Agent: 투자자 거래 동향 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_investor_trading_analysis_prompt(
        company_name, company_code, reference_date, max_years_ago, max_years, lang
    )

    return Agent(
        name="investor_trading_analysis_agent",
        instruction=instruction,
        server_names=["kospi_kosdaq"]
    )
