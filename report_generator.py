"""
보고서 생성 및 변환 모듈
"""
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import markdown
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM

# 로거 설정
logger = logging.getLogger(__name__)

# 상수 정의
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # 디렉토리가 없으면 생성
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML 보고서 디렉토리


def get_cached_report(stock_code: str) -> tuple:
    """캐시된 보고서 검색"""
    # 종목 코드로 시작하는 모든 보고서 파일 찾기
    report_files = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

    if not report_files:
        return False, "", None, None

    # 최신순으로 정렬
    latest_file = max(report_files, key=lambda p: p.stat().st_mtime)

    # 파일이 24시간 이내에 생성되었는지 확인
    file_age = datetime.now() - datetime.fromtimestamp(latest_file.stat().st_mtime)
    if file_age.days >= 1:  # 24시간 이상 지난 파일은 캐시로 사용하지 않음
        return False, "", None, None

    # 해당 HTML 파일도 있는지 확인
    html_file = None
    html_files = list(HTML_REPORTS_DIR.glob(f"{stock_code}_*.html"))
    if html_files:
        html_file = max(html_files, key=lambda p: p.stat().st_mtime)

    with open(latest_file, "r", encoding="utf-8") as f:
        content = f.read()

    # HTML 파일이 없으면 생성
    if not html_file:
        html_content = convert_to_html(content)
        html_file = save_html_report_from_content(
            stock_code,
            os.path.basename(latest_file).split('_')[1],  # 회사명 추출
            html_content
        )

    return True, content, latest_file, html_file


def save_report(stock_code: str, company_name: str, content: str) -> Path:
    """보고서를 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.md"
    filepath = REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def convert_to_html(markdown_content: str) -> str:
    """마크다운을 HTML로 변환"""
    try:
        # 마크다운을 HTML로 변환
        html_content = markdown.markdown(
            markdown_content,
            extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables']
        )

        # HTML 템플릿에 내용 삽입
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>주식 분석 보고서</title>
            <style>
                body {{
                    font-family: 'Pretendard', -apple-system, system-ui, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 900px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                h1, h2, h3, h4 {{
                    color: #2563eb;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 15px 0;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px 12px;
                }}
                th {{
                    background-color: #f1f5f9;
                }}
                code {{
                    background-color: #f1f5f9;
                    padding: 2px 4px;
                    border-radius: 4px;
                }}
                pre {{
                    background-color: #f1f5f9;
                    padding: 15px;
                    border-radius: 8px;
                    overflow-x: auto;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"HTML 변환 중 오류: {str(e)}")
        return f"<p>보고서 변환 중 오류가 발생했습니다: {str(e)}</p>"


def save_html_report_from_content(stock_code: str, company_name: str, html_content: str) -> Path:
    """HTML 내용을 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.html"
    filepath = HTML_REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return filepath


def save_html_report(stock_code: str, company_name: str, markdown_content: str) -> Path:
    """마크다운 보고서를 HTML로 변환하여 저장"""
    html_content = convert_to_html(markdown_content)
    return save_html_report_from_content(stock_code, company_name, html_content)


def generate_report_response_sync(stock_code: str, company_name: str) -> str:
    """
    종목 상세 보고서를 동기 방식으로 생성 (백그라운드 스레드에서 호출됨)
    """
    try:
        logger.info(f"동기식 보고서 생성 시작: {stock_code} ({company_name})")

        # 현재 날짜를 YYYYMMDD 형식으로 변환
        reference_date = datetime.now().strftime("%Y%m%d")

        # 별도의 프로세스로 분석 수행
        # 이 방법은 새로운 Python 프로세스를 생성하여 분석을 수행하므로 이벤트 루프 충돌 없음
        cmd = [
            sys.executable,  # 현재 Python 인터프리터
            "-c",
            f"""
import asyncio
import json
import sys
from cores.analysis import analyze_stock

async def run():
    try:
        result = await analyze_stock(
            company_code="{stock_code}", 
            company_name="{company_name}", 
            reference_date="{reference_date}"
        )
        # 구분자를 사용하여 결과 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": True, "result": result}}))
        print("RESULT_END")
    except Exception as e:
        # 구분자를 사용하여 에러 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": False, "error": str(e)}}))
        print("RESULT_END")

if __name__ == "__main__":
    asyncio.run(run())
            """
        ]

        logger.info(f"외부 프로세스 실행: {stock_code}")
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=600)  # 10분 타임아웃

        # 출력 초기화 - 경고 방지를 위해 변수 미리 선언
        output = ""

        # 출력 파싱 - 구분자를 사용하여 실제 JSON 출력 부분만 추출
        try:
            output = process.stdout
            # 로그 출력에서 RESULT_START와 RESULT_END 사이의 JSON 데이터만 추출
            if "RESULT_START" in output and "RESULT_END" in output:
                result_start = output.find("RESULT_START") + len("RESULT_START")
                result_end = output.find("RESULT_END")
                json_str = output[result_start:result_end].strip()

                # JSON 파싱
                parsed_output = json.loads(json_str)

                if parsed_output.get('success', False):
                    result = parsed_output.get('result', '')
                    logger.info(f"외부 프로세스 결과: {len(result)} 글자")
                    return result
                else:
                    error = parsed_output.get('error', '알 수 없는 오류')
                    logger.error(f"외부 프로세스 오류: {error}")
                    return f"분석 중 오류가 발생했습니다: {error}"
            else:
                # 구분자를 찾을 수 없는 경우 - 프로세스 실행 자체에 문제가 있을 수 있음
                logger.error(f"외부 프로세스 출력에서 결과 구분자를 찾을 수 없습니다: {output[:500]}")
                # stderr에 에러 로그가 있는지 확인
                if process.stderr:
                    logger.error(f"외부 프로세스 에러 출력: {process.stderr[:500]}")
                return f"분석 결과를 찾을 수 없습니다. 로그를 확인하세요."
        except json.JSONDecodeError as e:
            logger.error(f"외부 프로세스 출력 파싱 실패: {e}")
            logger.error(f"출력 내용: {output[:1000]}")
            return f"분석 결과 파싱 중 오류가 발생했습니다. 로그를 확인하세요."

    except subprocess.TimeoutExpired:
        logger.error(f"외부 프로세스 타임아웃: {stock_code}")
        return f"분석 시간이 초과되었습니다. 다시 시도해주세요."
    except Exception as e:
        logger.error(f"동기식 보고서 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"보고서 생성 중 오류가 발생했습니다: {str(e)}"

import re

def clean_model_response(response):
    # 마지막 평가 문장 패턴
    final_analysis_pattern = r'이제 수집한 정보를 바탕으로.*평가를 해보겠습니다\.'

    # 중간 과정 및 도구 호출 관련 정보 제거
    # 1. '[Calling tool' 포함 라인 제거
    lines = response.split('\n')
    cleaned_lines = [line for line in lines if '[Calling tool' not in line]
    temp_response = '\n'.join(cleaned_lines)

    # 2. 마지막 평가 문장이 있다면, 그 이후 내용만 유지
    final_statement_match = re.search(final_analysis_pattern, temp_response)
    if final_statement_match:
        final_statement_pos = final_statement_match.end()
        cleaned_response = temp_response[final_statement_pos:].strip()
    else:
        # 패턴을 찾지 못한 경우 그냥 도구 호출만 제거된 버전 사용
        cleaned_response = temp_response

    # 앞부분 빈 줄 제거
    cleaned_response = cleaned_response.lstrip()

    return cleaned_response

async def generate_follow_up_response(ticker, ticker_name, conversation_context, user_question, tone):
    """
    추가 질문에 대한 AI 응답 생성 (Agent 방식 사용)
    
    Args:
        ticker (str): 종목 코드
        ticker_name (str): 종목명
        conversation_context (str): 이전 대화 컨텍스트
        user_question (str): 사용자의 새 질문
        tone (str): 응답 톤
    
    Returns:
        str: AI 응답
    """
    try:
        # MCPApp 초기화
        app = MCPApp(name="telegram_ai_bot_followup")

        async with app.run() as app_instance:
            app_logger = app_instance.logger

            # 현재 날짜 정보 가져오기
            current_date = datetime.now().strftime('%Y%m%d')

            # 에이전트 생성
            agent = Agent(
                name="followup_agent",
                instruction=f"""당신은 텔레그램 채팅에서 주식 평가 후속 질문에 답변하는 전문가입니다.
                            
                            ## 기본 정보
                            - 현재 날짜: {current_date}
                            - 종목 코드: {ticker}
                            - 종목 이름: {ticker_name}
                            - 대화 스타일: {tone}
                            
                            ## 이전 대화 컨텍스트
                            {conversation_context}
                            
                            ## 사용자의 새로운 질문
                            {user_question}
                            
                            ## 응답 가이드라인
                            1. 이전 대화에서 제공한 정보와 일관성을 유지하세요
                            2. 필요한 경우 추가 데이터를 조회할 수 있습니다:
                               - get_stock_ohlcv: 최신 주가 데이터 조회
                               - get_stock_trading_volume: 투자자별 거래 데이터
                               - perplexity_ask: 최신 뉴스나 정보 검색
                            3. 사용자가 요청한 스타일({tone})을 유지하세요
                            4. 텔레그램 메시지 형식으로 자연스럽게 작성하세요
                            5. 이모티콘을 적극 활용하세요 (📈 📉 💰 🔥 💎 🚀 등)
                            6. 마크다운 형식은 사용하지 마세요
                            7. 2000자 이내로 작성하세요
                            8. 이전 대화의 맥락을 고려하여 답변하세요
                            
                            ## 주의사항
                            - 사용자의 질문이 이전 대화와 관련이 있다면, 그 맥락을 참고하여 답변
                            - 새로운 정보가 필요한 경우에만 도구를 사용
                            - 도구 호출 과정을 사용자에게 노출하지 마세요
                            """,
                server_names=["perplexity", "kospi_kosdaq"]
            )

            # LLM 연결
            llm = await agent.attach_llm(AnthropicAugmentedLLM)

            # 응답 생성
            response = await llm.generate_str(
                message=f"""사용자의 추가 질문에 대해 답변해주세요.
                        
                        이전 대화를 참고하되, 사용자의 새 질문에 집중하여 답변하세요.
                        필요한 경우 최신 데이터를 조회하여 정확한 정보를 제공하세요.
                        """,
                request_params=RequestParams(
                    model="claude-sonnet-4-5-20250929",
                    maxTokens=2000
                )
            )
            app_logger.info(f"추가 질문 응답 생성 결과: {str(response)[:100]}...")

            return clean_model_response(response)

    except Exception as e:
        logger.error(f"추가 응답 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."


async def generate_evaluation_response(ticker, ticker_name, avg_price, period, tone, background, report_path=None):
    """
    종목 평가 AI 응답 생성

    Args:
        ticker (str): 종목 코드
        ticker_name (str): 종목 이름
        avg_price (float): 평균 매수가
        period (int): 보유 기간 (개월)
        tone (str): 원하는 피드백 스타일/톤
        background (str): 매매 배경/히스토리
        report_path (str, optional): 보고서 파일 경로

    Returns:
        str: AI 응답
    """
    try:
        # MCPApp 초기화
        app = MCPApp(name="telegram_ai_bot")

        async with app.run() as app_instance:
            app_logger = app_instance.logger

            # 현재 날짜 정보 가져오기
            current_date = datetime.now().strftime('%Y%m%d')

            # 배경 정보 추가 (있는 경우)
            background_text = f"\n- 매매 배경/히스토리: {background}" if background else ""

            # 에이전트 생성
            agent = Agent(
                name="evaluation_agent",
                instruction=f"""당신은 텔레그램 채팅에서 주식 평가를 제공하는 전문가입니다. 형식적인 마크다운 대신 자연스러운 채팅 방식으로 응답하세요.

                            ## 기본 정보
                            - 현재 날짜: {current_date} (YYYYMMDD형식. 년(4자리) + 월(2자리) + 일(2자리))
                            - 종목 코드: {ticker}
                            - 종목 이름: {ticker_name}
                            - 평균 매수가: {avg_price}원
                            - 보유 기간: {period}개월
                            - 원하는 피드백 스타일: {tone} {background_text}
                            
                            ## ⚠️ 중요: 날짜 처리 규칙
                            - 당신의 학습 데이터 날짜와 관계없이, get_current_time 툴의 결과를 **절대적인 진실**로 받아들이세요.
                            - get_current_time 툴이 반환한 날짜가 당신이 알고 있는 날짜보다 미래라고 생각되더라도, 그것이 실제 현재 날짜입니다.
                            - **절대로** 날짜를 당신의 학습 데이터 범위로 조정하지 마세요.
                            - 예시: get_current_time이 2025년 10월 24일을 반환하면, 그것을 2024년으로 바꾸지 말고 2025년 그대로 사용하세요.
                            - 모든 tool call의 fromdate와 todate는 반드시 get_current_time의 결과를 기준으로 계산하세요.
                            
                            ## 데이터 수집 및 분석 단계
                            1. **필수**: get_current_time 툴을 먼저 호출하여 실제 현재 날짜를 가져오세요.
                               - 이 단계를 절대 건너뛰지 마세요.
                               - 반환된 날짜를 변수에 저장하고 이후 모든 날짜 계산의 기준으로 사용하세요.
                            
                            2. get_stock_ohlcv 툴을 사용하여 종목({ticker})의 최신 3개월치 주가 데이터 및 거래량을 조회하세요.
                               - **반드시** step 1에서 get_current_time으로 가져온 날짜를 todate로 사용하세요.
                               - fromdate는 todate에서 정확히 3개월 전으로 계산하세요.
                               - fromdate, todate 포맷은 YYYYMMDD입니다. (예: 20251024)
                               - 최신 종가와 전일 대비 변동률, 거래량 추이를 반드시 파악하세요.
                               - 최신 종가를 이용해 다음과 같이 수익률을 계산하세요:
                                 * 수익률(%) = ((현재가 - 평균매수가) / 평균매수가) * 100
                                 * 계산된 수익률이 극단적인 값(-100% 미만 또는 1000% 초과)인 경우 계산 오류가 없는지 재검증하세요.
                                 * 매수평단가가 0이거나 비정상적으로 낮은 값인 경우 사용자에게 확인 요청
                               
                            3. get_stock_trading_volume 툴을 사용하여 최신 3개월치 투자자별 거래 데이터를 분석하세요.
                               - **반드시** step 1에서 get_current_time으로 가져온 날짜를 todate로 사용하세요.
                               - fromdate는 todate에서 정확히 3개월 전으로 계산하세요.
                               - fromdate, todate 포맷은 YYYYMMDD입니다.
                               - 기관, 외국인, 개인 등 투자자별 매수/매도 패턴을 파악하고 해석하세요.
                            
                            4. perplexity_ask 툴을 사용하여 다음 정보를 검색하세요. 
                               - **반드시** step 1에서 get_current_time으로 가져온 날짜를 기준으로 검색하세요.
                               - 검색 쿼리에 구체적인 날짜나 "최근", "2025년" 등의 시간 표현을 포함하세요.
                               - 최대한 1개의 쿼리로 통합해서 검색해주세요.
                               - 검색 내용:
                                 * "종목코드 {ticker}의 정확한 회사 {ticker_name}에 대한 최근 뉴스 및 실적 분석 (유사 이름의 다른 회사와 혼동하지 말 것)"
                                 * "{ticker_name}(종목코드: {ticker}) 소속 업종 동향 및 전망"
                                 * "글로벌과 국내 증시 현황 및 전망"
                                 * "최근 급등 원인(테마 등)"
                               
                            5. 필요에 따라 추가 데이터를 수집하세요.
                            6. 수집된 모든 정보를 종합적으로 분석하여 종목 평가에 활용하세요.
                            
                            ## 날짜 검증 체크리스트
                            ✅ get_current_time을 가장 먼저 호출했는가?
                            ✅ 모든 tool call의 날짜 파라미터가 get_current_time 결과를 기준으로 계산되었는가?
                            ✅ 날짜를 당신의 학습 데이터 범위로 조정하지 않았는가?
                            ✅ 2025년이 미래라고 생각하여 2024년으로 바꾸지 않았는가?
                            
                            ## 스타일 적응형 가이드
                            사용자가 요청한 피드백 스타일("{tone}")을 최대한 정확하게 구현하세요. 다음 프레임워크를 사용하여 어떤 스타일도 적응적으로 구현할 수 있습니다:
                            
                            1. **스타일 속성 분석**:
                               사용자의 "{tone}" 요청을 다음 속성 측면에서 분석하세요:
                               - 격식성 (격식 <--> 비격식)
                               - 직접성 (간접 <--> 직설적)
                               - 감정 표현 (절제 <--> 과장)
                               - 전문성 (일상어 <--> 전문용어)
                               - 태도 (중립 <--> 주관적)
                            
                            2. **키워드 기반 스타일 적용**:
                               - "친구", "동료", "형", "동생" → 친근하고 격식 없는 말투
                               - "전문가", "분석가", "정확히" → 데이터 중심, 격식 있는 분석
                               - "직설적", "솔직", "거침없이" → 매우 솔직한 평가
                               - "취한", "술자리", "흥분" → 감정적이고 과장된 표현
                               - "꼰대", "귀족노조", "연륜" → 교훈적이고 경험 강조
                               - "간결", "짧게" → 핵심만 압축적으로
                               - "자세히", "상세히" → 모든 근거와 분석 단계 설명
                            
                            3. **스타일 조합 및 맞춤화**:
                               사용자의 요청에 여러 키워드가 포함된 경우 적절히 조합하세요.
                               예: "30년지기 친구 + 취한 상태" = 매우 친근하고 과장된 말투와 강한 주관적 조언
                            
                            4. **알 수 없는 스타일 대응**:
                               생소한 스타일 요청이 들어오면:
                               - 요청된 스타일의 핵심 특성을 추론
                               - 언어적 특징, 문장 구조, 어휘 선택 등에서 스타일을 반영
                               - 해당 스타일에 맞는 고유한 표현과 문장 패턴 창조
                            
                            ### 투자 상황별 조언 스타일
                            
                            1. 수익 포지션 (현재가 > 평균매수가):
                               - 더 적극적이고 구체적인 매매 전략 제시
                               - 예: "이익 실현 구간을 명확히 잡아 절반은 익절하고, 절반은 더 끌고가는 전략도 괜찮을 것 같아"
                               - 다음 목표가와 손절선 구체적 제시
                               - 현 상승세의 지속 가능성 분석에 초점
                            
                            2. 손실 포지션 (현재가 < 평균매수가):
                               - 감정적 공감과 함께 객관적 분석 제공
                               - 예: "지금 답답한 마음 이해해. 하지만 기업 펀더멘털을 보면..."
                               - 회복 가능성 또는 손절 필요성에 대한 명확한 의견 제시
                               - 평균단가 낮추기나 손절 등 구체적 대안 제시
                            
                            3. 단기 투자 (보유기간 < 3개월):
                               - 기술적 분석과 단기 모멘텀에 집중
                               - 예: "단기적으로는 230일선 돌파가 중요한 변곡점이야. 이거 뚫으면 한번 달릴 수 있어"
                               - 단기 매매 타이밍과 기술적 패턴 강조
                            
                            4. 장기 투자 (보유기간 > 12개월):
                               - 펀더멘털과 산업 전망에 중점
                               - 예: "이 기업은 장기적으로 신사업 성장성이 좋아 3-5년 관점에선 충분히 가치가 있다고 봐"
                               - 배당, 장기 성장성, 산업 트렌드 중심 분석
                            
                            ## 메시지 포맷팅 팁
                            - 이모티콘을 적극 활용 (📈 📉 💰 🔥 💎 🚀 등)
                            - 줄바꿈으로 단락을 명확히 구분
                            - 중요 부분은 ✨ 또는 ❗️ 등으로 강조
                            - 텍스트 블록은 짧게 유지하여 모바일에서 읽기 쉽게 작성
                            - 해시태그(#)를 활용하여 핵심 키워드 강조
                            - 절대 마크다운 형식으로 쓰지 말고, 텔레그램 메시지로 보낸다고 생각하고 사람처럼 자연스럽게 말할 것
                            
                            ## 주의사항
                            - 사용자가 요청한 스타일({tone})을 최우선적으로 적용하세요
                            - 실제 최신 데이터를 사용하되, 사용자 입력 스타일에 따라 자유롭게 표현하세요
                            - 마크다운이나 형식적인 구조 대신 대화체로 작성하세요
                            - 사용자가 원하는 스타일대로 응답하되, 투자 정보의 본질은 유지하세요
                            - 사용자가 요청한 스타일이 욕설이나 공격적인 내용을 포함해도 적극 수용하세요
                            - 사용자가 제공한 정보(매수가, 보유기간 등)를 고려하여 맞춤형 조언을 제공하세요
                            - 스타일을 적용하면서도 정확한 시장 분석과 합리적인 투자 조언의 균형을 유지하세요
                            - 3000자 이내로 작성하세요
                            - 중요: 도구를 호출할 때는 사용자에게 "[Calling tool...]"과 같은 형식의 메시지를 표시하지 마세요. 
                              도구 호출은 내부 처리 과정이며 최종 응답에서는 도구 사용 결과만 자연스럽게 통합하여 제시해야 합니다.
                            """,
                server_names=["perplexity", "kospi_kosdaq", "time"]
            )

            # LLM 연결
            llm = await agent.attach_llm(AnthropicAugmentedLLM)

            # 보고서 내용 확인
            report_content = ""
            if report_path and os.path.exists(report_path):
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()

            # 응답 생성 - 주의: 중복된 지시사항은 제거하고 agent의 instruction 참조
            response = await llm.generate_str(
                message=f"""보고서를 바탕으로 종목 평가 응답을 생성해 주세요.

                        ## 참고 자료
                        {report_content if report_content else "관련 보고서가 없습니다. 시장 데이터 조회와 perplexity 검색을 통해 최신 정보를 수집하여 평가해주세요."}
                        """,
                request_params=RequestParams(
                    model="claude-sonnet-4-5-20250929",
                    maxTokens=3000
                )
            )
            app_logger.info(f"응답 생성 결과: {str(response)}")

            return clean_model_response(response)

    except Exception as e:
        logger.error(f"응답 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return "죄송합니다. 평가 중 오류가 발생했습니다. 다시 시도해주세요."

