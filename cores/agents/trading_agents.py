from mcp_agent.agents.agent import Agent
from cores.language_config import Language
from cores.agents.prompt_templates import PromptTemplates


def create_trading_scenario_agent(language: str = "ko"):
    """
    매매 시나리오 생성 에이전트 생성

    주식 분석 보고서를 읽고 매매 시나리오를 JSON 형식으로 생성합니다.
    기본적으로는 가치투자 원칙을 따르되, 상승 모멘텀이 확인될 때는 보다 적극적으로 진입합니다.

    Args:
        language: Language code ("ko" or "en")

    Returns:
        Agent: 매매 시나리오 생성 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_trading_scenario_prompt(lang)

    return Agent(
        name="trading_scenario_agent",
        instruction=instruction,
        server_names=["kospi_kosdaq", "sqlite", "perplexity", "time"]
    )


def create_sell_decision_agent(language: str = "ko"):
    """
    매도 의사결정 에이전트 생성

    보유 종목의 매도 시점을 결정하는 전문 분석가 에이전트입니다.
    현재 보유 중인 종목의 데이터를 종합적으로 분석하여 매도할지 계속 보유할지 결정합니다.

    Args:
        language: Language code ("ko" or "en")

    Returns:
        Agent: 매도 의사결정 에이전트
    """
    lang = Language(language)
    instruction = PromptTemplates.get_sell_decision_prompt(lang)
    return Agent(
        name="sell_decision_agent",
        instruction=instruction,
        server_names=["kospi_kosdaq", "sqlite", "time"]
    )
