from mcp_agent.agents.agent import Agent
from cores.language_config import Language
from cores.agents.prompt_templates import PromptTemplates


def create_news_analysis_agent(company_name, company_code, reference_date, language: str = "ko"):
    """
    뉴스 분석 에이전트 생성

    Args:
        company_name: 기업명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        language: Language code ("ko" or "en")

    Returns:
        Agent: 뉴스 분석 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_news_analysis_prompt(
        company_name, company_code, reference_date, lang
    )

    return Agent(
        name="news_analysis_agent",
        instruction=instruction,
        server_names=["perplexity", "firecrawl"]
    )
