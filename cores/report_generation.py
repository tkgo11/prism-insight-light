from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM


# Language name mapping for report generation
LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German"
}


@retry(
    stop=stop_after_attempt(2),  # 최대 2번 시도 (초기 + 1번 재시도)
    wait=wait_exponential(multiplier=1, min=10, max=30),  # 지수적으로 증가하는 대기 시간
    retry=retry_if_exception_type(Exception)  # 모든 예외에 대해 재시도
)
async def generate_report(agent, section, company_name, company_code, reference_date, logger, language="ko"):
    """
    에이전트를 사용하여 보고서 생성 - 재시도 로직 포함

    Args:
        agent: 분석 에이전트
        section: 보고서 섹션명
        company_name: 회사명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        logger: 로거
        language: 보고서 작성 언어 코드 (default: "ko")
    """
    language_name = LANGUAGE_NAMES.get(language, language.upper())

    llm = await agent.attach_llm(OpenAIAugmentedLLM)
    report = await llm.generate_str(
        message=f"""{company_name}({company_code})의 {section} 분석 보고서를 작성해주세요.
                                (보고서 작성 언어: {language_name})

                                ## 분석 및 보고서 작성 지침:
                                1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
                                2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
                                3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
                                4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
                                5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.
                                6. **회사명은 반드시 {language_name}으로 번역하여 표기하세요.** (예: "삼성전자" → "Samsung Electronics")

                                ## 형식 요구사항:
                                1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
                                2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
                                3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.

                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
        request_params=RequestParams(
            model="gpt-4.1",
            maxTokens=16000,
            max_iterations=3,
            parallel_tool_calls=True,
            use_history=True
        )
    )
    logger.info(f"Completed {section} - {len(report)} characters")
    return report

async def generate_market_report(agent, section, reference_date, logger, language="ko"):
    """
    에이전트를 사용하여 시장 분석 보고서 생성

    Args:
        agent: 분석 에이전트
        section: 보고서 섹션명
        reference_date: 분석 기준일 (YYYYMMDD)
        logger: 로거
        language: 보고서 작성 언어 코드 (default: "ko")
    """
    language_name = LANGUAGE_NAMES.get(language, language.upper())

    llm = await agent.attach_llm(OpenAIAugmentedLLM)
    report = await llm.generate_str(
        message=f"""시장과 거시환경 분석 보고서를 작성해주세요.
                                (보고서 작성 언어: {language_name})

                                ## 분석 및 보고서 작성 지침:
                                1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
                                2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
                                3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
                                4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
                                5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.
                                6. **회사명은 반드시 {language_name}으로 번역하여 표기하세요.** (예: "삼성전자" → "Samsung Electronics")

                                ## 형식 요구사항:
                                1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
                                2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
                                3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.

                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
        request_params=RequestParams(
            model="gpt-4.1",
            maxTokens=16000,
            max_iterations=3,
            parallel_tool_calls=True,
            use_history=True
        )
    )
    logger.info(f"Completed {section} - {len(report)} characters")
    return report


async def generate_summary(section_reports, company_name, company_code, reference_date, logger, language="ko"):
    """
    섹션 보고서들을 바탕으로 요약 생성

    Args:
        section_reports: 각 섹션별 보고서 딕셔너리
        company_name: 회사명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        logger: 로거
        language: 보고서 작성 언어 코드 (default: "ko")
    """
    try:
        from mcp_agent.agents.agent import Agent
        from mcp_agent.workflows.llm.augmented_llm import RequestParams
        from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

        language_name = LANGUAGE_NAMES.get(language, language.upper())

        # 모든 섹션을 포함한 종합 보고서 생성
        all_reports = ""
        for section, report in section_reports.items():
            all_reports += f"\n\n--- {section.upper()} ---\n\n"
            all_reports += report

        logger.info(f"Generating executive summary for {company_name}...")
        summary_agent = Agent(
            name="summary_agent",
            instruction=f"""
                        당신은 {company_name} ({company_code}) 기업분석 보고서의 핵심 요약을 작성하는 투자 전문가입니다.
                        전체 보고서의 각 섹션에서 가장 중요한 3-5개의 핵심 포인트를 추출하여 간결하게 요약해야 합니다.
                        투자자가 빠르게 읽고 핵심을 파악할 수 있는 요약을 제공하세요.

                        **회사명은 반드시 {language_name}으로 번역하여 표기하세요.** (예: "삼성전자" → "Samsung Electronics")

                        ##분석일 : {reference_date}(YYYYMMDD 형식)
                        """
        )

        llm = await summary_agent.attach_llm(OpenAIAugmentedLLM)
        executive_summary = await llm.generate_str(
            message=f"""아래 {company_name}({company_code})의 종합 분석 보고서를 바탕으로 핵심 투자 포인트 요약을 작성해주세요.
                    (보고서 작성 언어: {language_name})

                    요약에는 기업의 현재 상황, 투자 매력 포인트, 주요 리스크 요소, 적합한 투자자 유형 등이 포함되어야 합니다.
                    500-800자 정도의 간결하면서도 통찰력 있는 요약을 작성해주세요.

                    ## 형식 가이드라인:
                    - 제목: "# 핵심 투자 포인트"
                    - 첫 문단: 기업 현재 상황 및 투자 관점 개요
                    - 불릿 포인트: 3-5개의 핵심 투자 포인트
                    - 마지막 문단: 적합한 투자자 유형 및 접근법 제안

                    ## 스타일 가이드라인:
                    - 간결하고 명확한 문장 사용
                    - 투자 결정에 직접적으로 도움되는 실질적 내용 중심
                    - 확정적 표현보다 조건부/확률적 표현 사용
                    - 모든 포인트는 기술적/기본적 분석 데이터에 기반
                    - **회사명은 반드시 {language_name}으로 번역하여 표기하세요.**

                    종합 분석 보고서:
                    {all_reports}
                    """,
            request_params=RequestParams(
                model="gpt-4.1",
                maxTokens=6000,
                max_iterations=2,
                parallel_tool_calls=True,
                use_history=True
            )
        )
        return executive_summary
    except Exception as e:
        logger.error(f"Error generating executive summary: {e}")
        return "# 핵심 투자 포인트\n\n분석 요약을 생성하는 데 문제가 발생했습니다."


async def generate_investment_strategy(section_reports, combined_reports, company_name, company_code, reference_date, logger, language="ko"):
    """
    투자 전략 보고서 생성

    Args:
        section_reports: 각 섹션별 보고서 딕셔너리
        combined_reports: 통합 보고서 내용
        company_name: 회사명
        company_code: 종목 코드
        reference_date: 분석 기준일 (YYYYMMDD)
        logger: 로거
        language: 보고서 작성 언어 코드 (default: "ko")
    """
    from mcp_agent.agents.agent import Agent
    from mcp_agent.workflows.llm.augmented_llm import RequestParams
    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

    language_name = LANGUAGE_NAMES.get(language, language.upper())

    try:
        logger.info(f"Processing investment_strategy for {company_name}...")
        investment_strategy_agent = Agent(
            name="investment_strategy_agent",
            instruction=f"""당신은 투자 전략 전문가입니다. 앞서 분석된 기술적 분석, 기업 정보, 재무 분석, 뉴스 트렌드, 시장분석을 종합하여 투자 전략 및 의견을 제시해야 합니다.

            **회사명은 반드시 {language_name}으로 번역하여 표기하세요.** (예: "삼성전자" → "Samsung Electronics")

            ## 분석 통합 요소
            1. 주가/거래량 분석 요약 - 주가 추세, 주요 지지/저항선, 거래량 패턴
            2. 투자자 거래 동향 분석 요약 - 기관/외국인/개인 매매 패턴
            3. 기업 기본 정보 요약 - 핵심 사업 모델, 경쟁력, 성장 동력
            4. 뉴스 분석 요약 - 주요 이슈, 시장 반응, 향후 이벤트
            5. 시장 분석 요약 - 시장 변동 요인, 현황, 추세, 거시환경, 기술적 분석, 시장 투자 전략

            ## 투자 전략 구성 요소
            1. 종합 투자 관점 - 기술적/기본적 분석을 종합한 투자 전망
            2. 투자자 유형별 전략
               - 단기 트레이더 관점 (1개월 이내)
               - 스윙 트레이더 관점 (1-3개월)
               - 중기 투자자 관점 (3-12개월)
               - 장기 투자자 관점 (1년 이상)
               - 신규 진입자, 기존 보유자 각각의 관점 (비중 활용한 설명)
            3. 주요 매매 포인트
               - 매수 고려 가격대 및 조건
               - 매도/손절 가격대 및 조건
               - 수익 실현 전략
            4. 핵심 모니터링 요소
               - 주시해야 할 기술적 신호
               - 주목해야 할 실적 지표
               - 체크해야 할 뉴스 및 이벤트
               - 체크해야 할 시장 환경
            5. 리스크 요소
               - 잠재적 하방 리스크
               - 상방 기회 요소
               - 리스크 관리 방안

            ## 작성 스타일
            - 객관적인 데이터에 기반한 투자 견해 제시
            - 확정적 예측보다는 조건부 시나리오 제시
            - 다양한 투자 성향과 기간을 고려한 차별화된 전략 제공
            - 구체적인 가격대와 실행 가능한 전략 제시
            - 균형 잡힌 리스크-리워드 분석

            ## 보고서 형식
            - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
            - 제목: "# 5. 투자 전략 및 의견"
            - 부제목은 ## 형식으로, 소제목은 ### 형식으로 구성
            - 투자자 유형별 전략은 명확히 구분하여 제시
            - 주요 매매 포인트는 구체적인 가격대와 조건으로 표현
            - 리스크 요소는 중요도에 따라 구분하여 설명

            ## 주의사항
            - "투자 권유"가 아닌 "투자 참고 정보" 형태로 제공
            - 일방적인 매수/매도 권유는 피하고, 조건부 접근법 제시
            - 과도한 낙관론이나 비관론은 지양
            - 모든 투자 전략은 기술적/기본적 분석의 실제 데이터에 근거
            - "반드시", "확실히" 등의 단정적 표현보다 "~할 가능성", "~로 예상" 등 사용
            - 모든 투자에는 리스크가 있음을 명시

            ## 결론 부분
            - 마지막에 간략한 요약과 핵심 투자 포인트 3-5개 제시
            - "본 보고서는 투자 참고용이며, 투자 책임은 투자자 본인에게 있습니다." 문구 포함

            기업: {company_name} ({company_code})
            ##분석일: {reference_date}(YYYYMMDD 형식)
            """
        )

        llm = await investment_strategy_agent.attach_llm(OpenAIAugmentedLLM)
        investment_strategy = await llm.generate_str(
            message=f"""{company_name}({company_code})의 투자 전략 분석 보고서를 작성해주세요.
            (보고서 작성 언어: {language_name})

            ## 앞서 분석된 다른 섹션의 내용:
            {combined_reports}

            ## 투자 전략 작성 지침:
            앞서 분석된 모든 정보를 바탕으로 종합적인 투자 전략 보고서를 작성하세요.
            기존에 설정된 투자 전략 에이전트의 지침에 따라 작성하되, 특히 다음 사항에 중점을 두세요:

            1. 앞서 분석된 다양한 데이터(기술적/기본적/뉴스)를 단순 요약이 아닌 통합적 관점에서 재해석
            2. 현 시점({reference_date})의 주가 수준에서 투자 매력도 평가
            3. 밸류에이션과 실적 전망을 연계한 투자 시나리오 제시
            4. 업종 및 시장 전체 흐름 속에서의 상대적 투자 매력도 분석
            5. **회사명은 반드시 {language_name}으로 번역하여 표기하세요.**

            일관성 있고 실행 가능한 투자 전략을 제시하여 투자자가 실제 의사결정에 활용할 수 있도록 해주세요.

            ## 형식 및 스타일 요구사항:
            - 앞서 설정된 형식(제목, 구조, 스타일)을 그대로 따르세요
            - 투자자가 행동으로 옮길 수 있는 실질적인 전략 제시에 초점을 맞추세요
            """,
            request_params=RequestParams(
                model="gpt-4.1",
                maxTokens=16000,
                max_iterations=3,
                parallel_tool_calls=True,
                use_history=True
            )
        )
        logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        return investment_strategy
    except Exception as e:
        logger.error(f"Error processing investment_strategy: {e}")
        return "투자 전략 분석 실패"


def get_disclaimer():
    """면책 문구 정의"""
    return """
# 투자 유의사항

본 보고서는 정보 제공을 목적으로 작성되었으며, 투자 권유를 목적으로 하지 않습니다. 
본 보고서에 기재된 내용은 작성 시점 기준으로 신뢰할 수 있는 자료에 근거하여 AI로 작성되었으나, 
그 정확성과 완전성을 보장하지 않습니다.

투자는 본인의 판단과 책임 하에 신중하게 이루어져야 하며, 
본 보고서를 참고하여 발생하는 투자 결과에 대한 책임은 투자자 본인에게 있습니다.
"""
