from mcp_agent.agents.agent import Agent
from cores.language_config import Language
from cores.agents.prompt_templates import PromptTemplates


def create_market_index_analysis_agent(reference_date, max_years_ago, max_years, language: str = "ko"):
    """
    시장 인덱스 분석 에이전트 생성

    Args:
        reference_date: 분석 기준일 (YYYYMMDD)
        max_years_ago: 분석 시작일 (YYYYMMDD)
        max_years: 분석 기간 (년)
        language: Language code ("ko" or "en")

    Returns:
        Agent: 시장 인덱스 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_market_index_analysis_prompt(
        reference_date, max_years_ago, max_years, lang
    )

    return Agent(
        name="market_index_analysis_agent",
        instruction=instruction,
        server_names=["kospi_kosdaq", "perplexity"]
    )
