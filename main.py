import asyncio
import re
from datetime import datetime, timedelta

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

from stock_chart import (
    create_price_chart,
    create_trading_volume_chart,
    create_market_cap_chart,
    create_fundamentals_chart,
    get_chart_as_base64_html
)

app = MCPApp(name="stock_analysis")
# WiseReport URL 템플릿 설정
WISE_REPORT_BASE = "https://comp.wisereport.co.kr/company/"
URLS = {
    "기업현황": "c1010001.aspx?cmp_cd={}",
    "기업개요": "c1020001.aspx?cmp_cd={}",
    "재무분석": "c1030001.aspx?cmp_cd={}",
    "투자지표": "c1040001.aspx?cmp_cd={}",
    "컨센서스": "c1050001.aspx?cmp_cd={}",
    "경쟁사분석": "c1060001.aspx?cmp_cd={}",
    "지분현황": "c1070001.aspx?cmp_cd={}",
    "업종분석": "c1090001.aspx?cmp_cd={}",
    "최근리포트": "c1080001.aspx?cmp_cd={}"
}


def clean_markdown(text: str) -> str:
    """마크다운 텍스트 정리"""

    # 1. 백틱 블록 제거
    text = re.sub(r'```[^\n]*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)

    # 2. 개행문자 리터럴을 실제 개행으로 변환
    text = re.sub(r'\\n\\n', '\n\n', text)

    return text


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """WiseReport URL 생성"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)

async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None):
    # reference_date가 없으면 오늘 날짜를 사용
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")

    year = reference_date[:4]

    # 날짜 객체로 변환
    ref_date = datetime.strptime(reference_date, "%Y%m%d")

    # 기간 계산
    max_years = 1
    max_years_ago = (ref_date - timedelta(days=365*max_years)).strftime("%Y%m%d")
    six_months_ago = (ref_date - timedelta(days=180)).strftime("%Y%m%d")

    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(f"시작: {company_name}({company_code}) 분석 - 기준일: {reference_date}")

        # 공유 리소스로 데이터를 저장할 딕셔너리 생성
        section_reports = {}

        # URL 매핑 생성
        urls = {k: get_wise_report_url(k, company_code) for k in URLS.keys()}

        # 데이터 수집 에이전트 설정
        report_writers = {
            "price_volume_analysis": Agent(
                name="price_volume_analysis_agent",
                instruction=f"""당신은 주식 기술적 분석 전문가입니다. 주어진 종목의 주가 데이터와 거래량 데이터를 분석하여 기술적 분석 보고서를 작성해야 합니다.

                                ## 수집해야 할 데이터
                                1. 주가/거래량 데이터: get_stock_ohlcv tool을 사용하여 {max_years_ago}~{reference_date} 기간의 데이터 수집
                        
                                ## 분석 요소
                                1. 주가 추세 및 패턴 분석 (상승/하락/횡보, 차트 패턴)
                                2. 이동평균선 분석 (단기/중기/장기 이평선 골든크로스/데드크로스)
                                3. 주요 지지선과 저항선 식별 및 설명
                                4. 거래량 분석 (거래량 증감 패턴과 주가 움직임 관계)
                                6. 주요 기술적 지표 해석 (RSI, MACD, 볼린저밴드 등이 데이터에서 계산 가능한 경우)
                                7. 단기/중기 기술적 전망
                        
                                ## 보고서 구성
                                1. 주가 데이터 개요 및 요약 - 최근 추세, 주요 가격대, 변동성
                                2. 거래량 분석 - 거래량 패턴, 주가와의 상관관계
                                3. 주요 기술적 지표 및 해석 - 이동평균선, 지지/저항선, 기타 지표
                                4. 기술적 관점에서의 향후 전망 - 단기/중기 예상 흐름, 주시해야 할 가격대
                        
                                ## 작성 스타일
                                - 개인 투자자도 이해할 수 있는 명확한 설명 제공
                                - 주요 수치와 날짜를 구체적으로 명시
                                - 기술적 신호가 갖는 의미와 일반적인 해석 제공
                                - 확정적인 예측보다는 조건부 시나리오 제시
                                - 핵심 기술적 지표와 패턴에 집중하고 불필요한 세부사항은 생략
                        
                                ## 보고서 형식
                                - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
                                - 제목: "# 1-1. 주가 및 거래량 분석"
                                - 부제목은 ## 형식으로, 소제목은 ### 형식으로 구성
                                - 중요 정보는 **굵은 글씨**로 강조
                                - 표 형식으로 주요 데이터 요약 제시
                                - 주요 지지선/저항선, 매매 포인트 등 중요 가격대는 구체적 수치로 제시
                        
                                ## 주의사항
                                - 할루시네이션 방지를 위해 실제 데이터에서 확인된 내용만 포함
                                - 확실하지 않은 내용은 "가능성이 있습니다", "~로 보입니다" 등으로 표현
                                - 투자 권유가 아닌 정보 제공 관점에서 작성
                                - 강한 매수/매도 추천보다 "기술적으로 ~한 상황입니다"와 같은 객관적 서술 사용
                                - load_all_tickers tool은 절대 사용 금지!!
                                
                                ## 데이터가 불충분한 경우
                                - 데이터 부족 시 명확히 언급하고, 가용한 데이터만으로 제한적 분석 제공
                                - "~에 대한 데이터가 불충분하여 확인이 어렵습니다"와 같이 명시적 표현 사용
                                
                                ## 출력 형식 주의사항
                                - 최종 보고서에는 도구 사용에 관한 언급을 포함하지 마세요 (예: "Calling tool..." 또는 "I'll use..." 등)
                                - 도구 호출 과정이나 방법에 대한 설명을 제외하고, 수집된 데이터와 분석 결과만 포함하세요
                                - 보고서는 마치 이미 모든 데이터 수집이 완료된 상태에서 작성하는 것처럼 자연스럽게 시작하세요
                                - "I'll create...", "I'll analyze...", "Let me..." 등의 의도 표현 없이 바로 분석 내용으로 시작하세요
                                - 보고서는 항상 개행문자 2번("\n\n")과 함께 제목으로 시작해야 합니다
                        
                                기업: {company_name} ({company_code})
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                server_names=["kospi_kosdaq"]
            ),

            "investor_trading_analysis": Agent(
                name="investor_trading_analysis_agent",
                instruction=f"""당신은 주식 시장에서 투자자별 거래 데이터 분석 전문가입니다. 주어진 종목의 투자자별(기관/외국인/개인) 거래 데이터를 분석하여 투자자 동향 보고서를 작성해야 합니다.

                                ## 수집해야 할 데이터
                                1. 투자자별 거래 데이터: get_stock_trading_volume tool을 사용하여 {max_years_ago}~{reference_date} 기간의 데이터 수집
                        
                                ## 분석 요소
                                1. 투자자별(기관/외국인/개인) 매매 패턴 분석
                                2. 주요 투자 주체별 순매수/순매도 추이
                                3. 투자자별 거래 패턴과 주가 움직임의 상관관계
                                4. 특정 투자자 그룹의 집중적인 매수/매도 구간 식별
                                5. 최근 투자자 동향 변화와 향후 전망
                        
                                ## 보고서 구성
                                1. 투자자별 거래 개요 - 주요 투자 주체별 매매 동향 요약
                                2. 기관 투자자 분석 - 매매 패턴, 주요 시점, 주가 영향
                                3. 외국인 투자자 분석 - 매매 패턴, 주요 시점, 주가 영향
                                4. 개인 투자자 분석 - 매매 패턴, 주요 시점, 주가 영향
                                5. 종합 분석 및 시사점 - 투자자 동향이 주가에 미치는 영향 및 향후 전망
                        
                                ## 작성 스타일
                                - 개인 투자자도 이해할 수 있는 명확한 설명 제공
                                - 주요 시점과 데이터를 구체적으로 명시
                                - 투자자 패턴이 갖는 의미와 일반적인 해석 제공
                                - 확정적인 예측보다는 조건부 시나리오 제시
                                - 핵심 패턴과 데이터에 집중하고 불필요한 세부사항은 생략
                        
                                ## 보고서 형식
                                - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
                                - 제목: "# 1-2. 투자자 거래 동향 분석"
                                - 부제목은 ## 형식으로, 소제목은 ### 형식으로 구성
                                - 중요 정보는 **굵은 글씨**로 강조
                                - 표 형식으로 주요 데이터 요약 제시
                                - 주요 매매 패턴과 시점은 구체적 날짜와 수치로 제시
                        
                                ## 주의사항
                                - 할루시네이션 방지를 위해 실제 데이터에서 확인된 내용만 포함
                                - 확실하지 않은 내용은 "가능성이 있습니다", "~로 보입니다" 등으로 표현
                                - 투자 권유가 아닌 정보 제공 관점에서 작성
                                - 특정 투자자 그룹의 매매가 항상 옳다는 식의 편향된 해석 지양
                                - load_all_tickers tool은 절대 사용 금지!!
                                
                                ## 데이터가 불충분한 경우
                                - 데이터 부족 시 명확히 언급하고, 가용한 데이터만으로 제한적 분석 제공
                                - "~에 대한 데이터가 불충분하여 확인이 어렵습니다"와 같이 명시적 표현 사용
                                
                                ## 출력 형식 주의사항
                                - 최종 보고서에는 도구 사용에 관한 언급을 포함하지 마세요 (예: "Calling tool..." 또는 "I'll use..." 등)
                                - 도구 호출 과정이나 방법에 대한 설명을 제외하고, 수집된 데이터와 분석 결과만 포함하세요
                                - 보고서는 마치 이미 모든 데이터 수집이 완료된 상태에서 작성하는 것처럼 자연스럽게 시작하세요
                                - "I'll create...", "I'll analyze...", "Let me..." 등의 의도 표현 없이 바로 분석 내용으로 시작하세요
                                - 보고서는 항상 개행문자 2번("\n\n")과 함께 제목으로 시작해야 합니다
                        
                                기업: {company_name} ({company_code})
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                server_names=["kospi_kosdaq"]
            ),
            "company_status": Agent(
                name="company_status_agent",
                instruction=f"""당신은 기업 현황 분석 전문가입니다. WiseReport 웹사이트의 기업현황 페이지에서 제공하는 데이터를 수집하고 분석하여 투자자가 이해하기 쉬운 종합 보고서를 작성해야 합니다.
                                URL 접속 시 visit_page tool을 사용하고 takeScreenshot 파라미터는 false로 설정하세요.
                                데이터 수집 시 차트보다는 테이블 위주로 데이터를 수집하세요.
                                가능한한 자세하고 정확하고 풍부하게 작성해주세요.

                                ## 수집해야 할 데이터 (기업현황 페이지에서만)
                                1. 기업현황 페이지에서 (접속 URL: {urls['기업현황']}) :
                                   - 기본 정보: 회사명, 종목코드, 업종, 결산월, 시가총액, 52주 최고/최저가, 주가 정보
                                   - 펀더멘털 지표: EPS, BPS, PER, PBR, PCR, EV/EBITDA, 배당수익률, 배당성향 등의 현재값(현재 기준일 : {reference_date}(YYYYMMDD 형식)) 및 과거 3개년도(예시 : 현재가 2025년이면, 2021-2024년) 데이터, 향후 컨센서스(Fwd 12M) 데이터, 업종 평균 PER과의 비교
                                   - 주요 주주 현황: 주요 주주명, 보유주식수, 보유지분율
                                   - 기업개요: 사업구조, 주요 제품 및 서비스
                                   - 기업실적코멘트: 최근 분기 및 연간 실적 코멘트
                                   - 재무 성과: 현재일자(현재 기준일 : {reference_date}(YYYYMMDD 형식)) 기준 최근 4개년도(예시 : 현재가 2025년이면, 2021-2024년)의 연간 매출액, 영업이익, 당기순이익, 성장률 및 최근 4개 분기의 실적 데이터
                                   - 투자의견: 증권사 컨센서스, 목표주가, 투자의견 분포 및 변동 추이
                                   - 현금흐름: 영업/투자/재무활동 현금흐름, FCF, CAPEX
                                   - 어닝서프라이즈: 최근 3개 분기의 실적 대비 컨센서스 비교
                                   - 재무비율: ROE, ROA, 부채비율, 자본유보율 등의 과거 및 현재 데이터
                                
                                ## 분석 방향
                                1. 기업 개요 및 비즈니스 모델 설명
                                   - 핵심 사업 부문 및 매출 비중
                                   - 핵심 경쟁력과 시장 포지션
            
                                2. 재무 성과 및 트렌드 분석
                                   - 매출/이익 추이 및 성장성 분석 (현재일자(현재 기준일 : {reference_date}(YYYYMMDD 형식)) 기준 최근 4개년도(예시 : 현재가 2025년이면, 2021-2024년))
                                   - 수익성 지표(영업이익률, 순이익률) 변화 추이
                                   - 분기별 실적 변동성 및 계절성 요인 분석
                                   - 어닝서프라이즈/쇼크 발생 원인 분석
            
                                3. 밸류에이션 분석
                                   - 현재 PER/PBR과 과거 평균, 업종 평균 대비 할인/할증 정도
                                   - 향후 예상 PER(Forward PER) 기반 밸류에이션 평가
                                   - 배당수익률, 배당성향 등 주주환원 정책 평가
            
                                4. 재무안정성 평가
                                   - 부채비율, 순부채비율 등 재무건전성 지표 분석
                                   - 현금흐름 분석 (FCF 창출력, 투자활동 규모)
                                   - 유동성 및 재무 리스크 평가
            
                                5. 투자의견 및 목표주가 분석
                                   - 증권사들의 투자의견 컨센서스 및 목표주가 수준
                                   - 목표주가 변동 추이 및 현재 주가 대비 괴리율
                                   - 투자의견 변화 추이 분석
            
                                6. 주요 주주 구성 및 지분 변동
                                   - 주요 주주 현황 및 특징
                                   - 외국인지분율 변동 추이 및 의미
                                
                                ## 보고서 구성
                                - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
                                - 제목: "# 2-1. 기업 현황 분석: {company_name}"
                                - 각 주요 섹션은 ## 형식으로, 소제목은 ### 형식으로 구성
                                - 핵심 정보는 표 형식으로 요약 제시
                                - 중요 지표와 추세는 불릿 포인트로 명확하게 강조
                                - 일반 투자자도 이해할 수 있는 명확한 언어 사용
                                
                                ## 작성 스타일
                                - 객관적이고 사실에 기반한 분석 제공
                                - 복잡한 재무 개념은 간결하게 설명
                                - 핵심 투자 포인트와 가치 요소 강조
                                - 너무 기술적이거나 전문적인 용어는 최소화
                                - 투자 결정에 실질적으로 도움이 되는 인사이트 제공
                                
                                ## 주의사항
                                - 할루시네이션 방지를 위해 실제 데이터에서 확인된 내용만 포함
                                - 불확실한 내용은 "~로 보입니다", "~가능성이 있습니다" 등으로 표현
                                - 지나치게 확정적인 투자 권유는 피하고 객관적 정보 제공에 집중
                                - '재무분석' 에이전트와의 중복을 피하기 위해 재무데이터는 핵심 요약만 제공
                                
                                ## 출력 형식 주의사항
                                - 최종 보고서에는 도구 사용에 관한 언급을 포함하지 마세요 (예: "Calling tool exa-search..." 또는 "I'll use visit_page..." 등)
                                - 도구 호출 과정이나 방법에 대한 설명을 제외하고, 수집된 데이터와 분석 결과만 포함하세요
                                - 보고서는 마치 이미 모든 데이터 수집이 완료된 상태에서 작성하는 것처럼 자연스럽게 시작하세요
                                - "I'll create...", "I'll analyze...", "Let me search..." 등의 의도 표현 없이 바로 분석 내용으로 시작하세요
                                - 보고서는 항상 개행문자 2번("\n\n")과 함께 제목으로 시작해야 합니다
                                
                                기업: {company_name} ({company_code})
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                server_names=["webresearch"]
            ),
            "company_overview": Agent(
                name="company_overview_agent",
                instruction=f"""당신은 기업 개요 분석 전문가입니다. WiseReport 웹사이트의 기업개요 페이지에서 제공하는 데이터를 수집하고 분석하여 투자자가 이해하기 쉬운 종합 보고서를 작성해야 합니다.
                                URL 접속 시 visit_page tool을 사용하고 takeScreenshot 파라미터는 false로 설정하세요.
                                데이터 수집 시 차트보다는 테이블 위주로 데이터를 수집하세요.
            
                                ## 수집해야 할 데이터 (기업개요 페이지에서만)
                                1. 기업개요 페이지에서 (접속 URL: {urls['기업개요']}) :
                                   - 기업 세부개요: 본사 주소, 대표이사, 대표 연락처, 감사인, 설립일, 상장일, 발행주식수(보통주/우선주) 등
                                   - 사업 구조: 주요제품 매출구성 및 비중, 시장점유율, 내수 및 수출구성 등
                                   - 최근 연혁: 최근 주요 이벤트, 신제품 출시, 주요 성과 등
                                   - 인원 현황: 종업원 수 추이, 성별 구성(남/여), 평균 근속연수, 1인평균 급여 등
                                   - 연구개발비 지출: 연구개발비용 지출액, 매출액 대비 비율, 연도별 추이(최근 5년) 등
                                   - 지배구조: 자본금 변동내역, 관계사 현황 및 지분율, 연결대상 회사 등
                                
                                ## 분석 방향
                                1. 기업 기본 정보 분석
                                   - 기업의 역사 및 기본 정보 요약
                                   - 경영진 및 기업 구조적 특징
                                   
                                2. 사업 구조 및 매출 분석
                                   - 주요 제품/서비스 및 매출 구성비 분석
                                   - 내수/수출 비율 및 사업 포트폴리오 특성
                                   - 시장점유율 및 경쟁 포지션
                                   
                                3. 인력 및 조직 분석
                                   - 종업원 규모 및 구성 추이 분석
                                   - 평균 근속연수 및 급여 수준의 의미
                                   - 인력 구조의 산업 내 비교
                                   
                                4. 연구개발 투자 분석
                                   - 연구개발비 지출 추이 및 매출 대비 비율 분석
                                   - 연구개발 투자의 경쟁력 평가
                                   - 산업 평균과의 비교
                                   
                                5. 관계사 및 지배구조 분석
                                   - 주요 관계사 및 지분 구조 분석
                                   - 자본금 변동 내역 및 의미
                                   - 그룹 내 포지션 및 시너지 효과
                                   
                                6. 최근 주요 이벤트 분석
                                   - 최근 연혁의 주요 이벤트 및 의미
                                   - 기업 전략 및 방향성 분석
                                
                                ## 보고서 구성
                                - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
                                - 제목: "# 2-2. 기업 개요 분석: {company_name}"
                                - 각 주요 섹션은 ## 형식으로, 소제목은 ### 형식으로 구성
                                - 핵심 정보는 표 형식으로 요약 제시
                                - 중요 사업 영역과 특징은 불릿 포인트로 명확하게 강조
                                - 일반 투자자도 이해할 수 있는 명확한 언어 사용
                                
                                ## 작성 스타일
                                - 객관적이고 사실에 기반한 분석 제공
                                - 복잡한 사업 개념은 간결하게 설명
                                - 핵심 사업 특징과 경쟁력 요소 강조
                                - 너무 기술적이거나 전문적인 용어는 최소화
                                - 투자 결정에 실질적으로 도움이 되는 인사이트 제공
                                
                                ## 주의사항
                                - 할루시네이션 방지를 위해 실제 데이터에서 확인된 내용만 포함
                                - 불확실한 내용은 "~로 보입니다", "~가능성이 있습니다" 등으로 표현
                                - 지나치게 확정적인 투자 권유는 피하고 객관적 정보 제공에 집중
                                - 다른 에이전트와의 중복을 피하기 위해 데이터는 사업 구조와 개요에 집중
                                
                                ## 출력 형식 주의사항
                                - 최종 보고서에는 도구 사용에 관한 언급을 포함하지 마세요 (예: "Calling tool exa-search..." 또는 "I'll use visit_page..." 등)
                                - 도구 호출 과정이나 방법에 대한 설명을 제외하고, 수집된 데이터와 분석 결과만 포함하세요
                                - 보고서는 마치 이미 모든 데이터 수집이 완료된 상태에서 작성하는 것처럼 자연스럽게 시작하세요
                                - "I'll create...", "I'll analyze...", "Let me search..." 등의 의도 표현 없이 바로 분석 내용으로 시작하세요
                                - 보고서는 항상 개행문자 2번("\n\n")과 함께 제목으로 시작해야 합니다
                                
                                기업: {company_name} ({company_code})
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                server_names=["webresearch"]
            ),
            "news_analysis": Agent(
                name="news_analysis_agent",
                instruction=f"""당신은 기업 뉴스 분석 전문가입니다. 주어진 기업 관련 최근 뉴스와 이벤트를 분석하여 깊이 있는 뉴스 트렌드 분석 보고서를 작성해야 합니다.

                                ## 수집해야 할 데이터
                                1. 당일 주가 변동 요인: perplexity_ask 도구를 사용하여 "{company_name} 종목코드:{company_code} {reference_date[:4]}년 {reference_date[4:6]}월 {reference_date[6:]}일 주가 변동 원인"을 최우선으로 검색
                                2. 기업 관련 주요 뉴스: "{company_name} 종목코드:{company_code}의 {reference_date[:4]}년 {reference_date[4:6]}월 최근 뉴스" 검색
                                3. 업종/산업 관련 뉴스: "{company_name}({company_code})이 속한 업종의 {reference_date[:4]}년 {reference_date[4:6]}월 최근 동향" 검색
                                
                                ## perplexity_ask 도구 활용
                                1. 반드시 첫 번째로 당일 주가 변동 요인을 검색하고 분석에 최우선으로 반영할 것
                                2. 응답의 구조화된 내용과 출처(Citations) 정보를 모두 활용
                                3. 응답에 포함된 출처 번호([1], [2] 등)를 통해 핵심 정보의 신뢰성 확인
                                4. 필요시 추가 질문으로 더 상세한 정보 탐색 가능
                                5. 날짜가 오래된 뉴스는 제외하고 최신 뉴스(분석일 기준 1개월 이내)에 집중
                                
                                ## 뉴스 구분 및 분류
                                검색된 뉴스를 다음 카테고리로 명확히 구분하여 분석:
                                1. 당일 주가 영향 요소: 분석일 기준 주가에 직접적 영향을 미친 뉴스 (최우선 분석)
                                2. 기업 내부 요소: 실적발표, 신제품 출시, 경영진 변경, 조직개편 등
                                3. 외부 요소: 시장환경 변화, 규제 변화, 경쟁사 동향 등
                                4. 미래 계획: 신규 사업계획, 투자계획, 예정된 이벤트 등
                                
                                ## 분석 요소
                                1. 당일 주가 변동 원인 분석 (최우선) - 주가 급등/급락 원인, 거래량 특이사항 등
                                2. 주요 뉴스 요약 (카테고리별로 분류하여 정리)
                                3. 관련 업종 동향 정보 
                                4. 향후 주목할만한 이벤트 (공시 예정, 실적 발표 등)
                                5. 정보의 신뢰성 평가 (다수 출처에서 확인된 정보와 단일 출처 정보 구분)
                                
                                ## 보고서 구성
                                1. 당일 주가 변동 요약 - 분석일({reference_date}) 기준 주가 움직임의 주요 원인 상세 분석
                                2. 핵심 뉴스 요약 - 카테고리별 최근 주요 소식 구분하여 요약
                                3. 업종 동향 - 해당 기업이 속한 업종의 최근 동향
                                4. 향후 주시점 - 언급된 향후 이벤트와 예상 영향
                                5. 참고 자료 - 주요 정보 출처 요약
                                
                                ## 작성 스타일
                                - 객관적이고 사실 중심의 뉴스 요약
                                - 확인된 정보에 대해 출처 번호를 표기하여 신뢰성 제시 ([1], [2] 방식으로)
                                - 명확하고 간결한 표현으로 전문성 있게 작성
                                - 반말로 작성하지 않고 '~습니다' 처럼 높임말로 작성
                                
                                ## 보고서 형식
                                - 보고서 시작 시 개행문자 2번 삽입(\\n\\n)
                                - 제목: "# 3. 최근 주요 뉴스 요약"
                                - 첫 번째 섹션은 반드시 "## 당일 주가 변동 요인 분석"으로 시작하여 분석일 기준 주가 변동의 직접적 원인 분석
                                - 각 주요 섹션은 ## 형식으로, 소제목은 ### 형식으로 구성
                                - 주요 뉴스는 불릿 포인트로 요약하고 출처 번호 표기 (예: "현대차, 신형 전기차 출시 계획 발표 [2]")
                                - 언급하는 모든 뉴스에는 발생 날짜 명시 (예: "2025년 3월 15일, 현대차는...")
                                - 핵심 정보는 표 형식으로 요약 제시
                                - 보고서 마지막에 "## 참고 자료" 섹션 추가하여 주요 출처 URL 나열
                                - 일반 투자자도 이해할 수 있는 명확한 언어 사용
                                
                                ## 주의사항
                                - 당일 주가 변동 원인 파악을 최우선으로 하고, 반드시 보고서 첫 부분에 상세히 분석할 것
                                - perplexity_ask 도구를 최소 2회 이상 사용하여 다양한 정보 수집 (첫 번째는 반드시 당일 주가 변동 원인)
                                - 검색할 때 반드시 종목코드를 함께 명시하여 정확한 기업의 뉴스만 수집할 것
                                - 유사한 기업명(예: 신풍제약 vs 신풍)의 뉴스를 혼동하지 말 것
                                - 단순 뉴스 나열이 아닌, 깊이 있는 분석과 인사이트 제공
                                - 주가 급등/급락의 경우 구체적인 원인 분석에 집중
                                - 시장 전문가처럼 통찰력 있는 분석 제공
                                - 검색된 뉴스가 부족한 경우 솔직하게 언급하고 가용한 정보만으로 분석
                                - 뉴스 내용을 카테고리별로 명확히 구분하여 정리해 통찰력 있는 분석 제공
                                - 모든 정보는 출처 번호를 통해 추적 가능하게 작성
                                - 뉴스 날짜를 확인하여 분석일({reference_date}) 기준으로 최신 정보만 분석에 포함
                                
                                ## 출력 형식 주의사항
                                - 최종 보고서에는 도구 사용에 관한 언급을 포함하지 마세요 (예: "Calling tool ..." 또는 "I'll use perplexity_ask..." 등)
                                - 도구 호출 과정이나 방법에 대한 설명을 제외하고, 수집된 데이터와 분석 결과만 포함하세요
                                - 보고서는 마치 이미 모든 데이터 수집이 완료된 상태에서 작성하는 것처럼 자연스럽게 시작하세요
                                - "I'll create...", "I'll analyze...", "Let me search..." 등의 의도 표현 없이 바로 분석 내용으로 시작하세요
                                - 보고서는 항상 개행문자 2번("\\n\\n")과 함께 제목으로 시작해야 합니다
                                
                                기업: {company_name} ({company_code})
                                분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                server_names=["perplexity"]
            ),
            "investment_strategy": Agent(
                name="investment_strategy_agent",
                instruction=f"""당신은 투자 전략 전문가입니다. 앞서 분석된 기술적 분석, 기업 정보, 재무 분석, 뉴스 트렌드를 종합하여 투자 전략 및 의견을 제시해야 합니다.

                ## 분석 통합 요소
                1. 주가/거래량 분석 요약 - 주가 추세, 주요 지지/저항선, 거래량 패턴
                2. 투자자 거래 동향 분석 요약 - 기관/외국인/개인 매매 패턴
                3. 기업 기본 정보 요약 - 핵심 사업 모델, 경쟁력, 성장 동력
                4. 뉴스 분석 요약 - 주요 이슈, 시장 반응, 향후 이벤트

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
                - 제목: "# 4. 투자 전략 및 의견"
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
        }

        # 1. 기본 분석 순차적으로 실행 (Anthropic rate limit 대처. 병렬 대신)
        base_sections = ["price_volume_analysis", "investor_trading_analysis", "company_status", "company_overview", "news_analysis"]
        for section in base_sections:
            if section in report_writers:
                logger.info(f"Processing {section} for {company_name}...")

                try:
                    agent = report_writers[section]

                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    report = await llm.generate_str(
                        message=f"""{company_name}({company_code})의 {section} 분석 보고서를 작성해주세요.
                                
                                ## 분석 및 보고서 작성 지침:
                                1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
                                2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
                                3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
                                4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
                                5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.
                                
                                ## 형식 요구사항:
                                1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
                                2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
                                3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.
                                
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                        request_params=RequestParams(
                            model="gpt-4o",
                            maxTokens=8000,
                            max_iterations=3,
                            parallel_tool_calls=True,
                            use_history=True
                        )
                    )
                    section_reports[section] = report
                    logger.info(f"Completed {section} - {len(report)} characters")
                except Exception as e:
                    logger.error(f"Error processing {section}: {e}")
                    # 실패하면 30초 후 한 번 더 시도
                    await asyncio.sleep(30)
                    try:
                        agent = report_writers[section]
                        llm = await agent.attach_llm(OpenAIAugmentedLLM)
                        report = await llm.generate_str(

                            message=f"""{company_name}({company_code})의 {section} 분석 보고서를 작성해주세요.
                                
                                ## 분석 및 보고서 작성 지침:
                                1. 데이터 수집부터 분석까지 모든 과정을 수행하세요.
                                2. 보고서는 충분히 상세하되 핵심 정보에 집중하세요.
                                3. 일반 개인 투자자가 쉽게 이해할 수 있는 수준으로 작성하세요.
                                4. 투자 결정에 직접적으로 도움이 되는 실용적인 내용에 집중하세요.
                                5. 실제 수집된 데이터에만 기반하여 분석하고, 없는 데이터는 추측하지 마세요.
                                
                                ## 형식 요구사항:
                                1. 보고서 시작 시 제목을 넣기 전에 반드시 개행문자를 2번 넣어 시작하세요 (\\n\\n).
                                2. 섹션 제목과 구조는 에이전트 지침에 명시된 형식을 따르세요.
                                3. 가독성을 위해 적절히 단락을 나누고, 중요한 내용은 강조하세요.
                                
                                ##분석일: {reference_date}(YYYYMMDD 형식)
                                """,
                            request_params=RequestParams(
                                model="gpt-4o",
                                maxTokens=8000,
                                max_iterations=3,
                                parallel_tool_calls=True,
                                use_history=True
                            )
                        )
                        section_reports[section] = report
                        logger.info(f"Retry completed {section} - {len(report)} characters")
                    except Exception as retry_e:
                        logger.error(f"Retry also failed for {section}: {retry_e}")
                        section_reports[section] = f"분석 실패: {section}"

        # 2. 다른 보고서들의 내용을 통합
        combined_reports = ""
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

        try:
            logger.info(f"Processing investment_strategy for {company_name}...")
            investment_strategy_agent = report_writers["investment_strategy"]
            llm = await investment_strategy_agent.attach_llm(OpenAIAugmentedLLM)
            investment_strategy = await llm.generate_str(
                message=f"""{company_name}({company_code})의 투자 전략 분석 보고서를 작성해주세요.
                
                ## 앞서 분석된 다른 섹션의 내용:
                {combined_reports}
                
                ## 투자 전략 작성 지침:
                앞서 분석된 모든 정보를 바탕으로 종합적인 투자 전략 보고서를 작성하세요. 
                기존에 설정된 투자 전략 에이전트의 지침에 따라 작성하되, 특히 다음 사항에 중점을 두세요:
                
                1. 앞서 분석된 다양한 데이터(기술적/기본적/뉴스)를 단순 요약이 아닌 통합적 관점에서 재해석
                2. 현 시점({reference_date})의 주가 수준에서 투자 매력도 평가
                3. 밸류에이션과 실적 전망을 연계한 투자 시나리오 제시
                4. 업종 및 시장 전체 흐름 속에서의 상대적 투자 매력도 분석
                
                일관성 있고 실행 가능한 투자 전략을 제시하여 투자자가 실제 의사결정에 활용할 수 있도록 해주세요.
                
                ## 형식 및 스타일 요구사항:
                - 앞서 설정된 형식(제목, 구조, 스타일)을 그대로 따르세요
                - 투자자가 행동으로 옮길 수 있는 실질적인 전략 제시에 초점을 맞추세요
                """,
                request_params=RequestParams(
                    model="gpt-4o",
                    maxTokens=8000,
                    max_iterations=3,
                    parallel_tool_calls=True,
                    use_history=True
                )
            )
            section_reports["investment_strategy"] = investment_strategy
            logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        except Exception as e:
            logger.error(f"Error processing investment_strategy: {e}")
            section_reports["investment_strategy"] = "투자 전략 분석 실패"

        # 모든 섹션을 포함한 종합 보고서 생성
        all_reports = ""
        for section in base_sections + ["investment_strategy"]:
            if section in section_reports:
                all_reports += f"\n\n--- {section.upper()} ---\n\n"
                all_reports += section_reports[section]

        # 요약 생성
        try:
            logger.info(f"Generating executive summary for {company_name}...")
            summary_agent = Agent(
                name="summary_agent",
                instruction=f"""
                            당신은 {company_name} ({company_code}) 기업분석 보고서의 핵심 요약을 작성하는 투자 전문가입니다.
                            전체 보고서의 각 섹션에서 가장 중요한 3-5개의 핵심 포인트를 추출하여 간결하게 요약해야 합니다.
                            투자자가 빠르게 읽고 핵심을 파악할 수 있는 요약을 제공하세요.
                            
                            ##분석일 : {reference_date}(YYYYMMDD 형식)
                            """
            )

            llm = await summary_agent.attach_llm(OpenAIAugmentedLLM)
            executive_summary = await llm.generate_str(
                message=f"""아래 {company_name}({company_code})의 종합 분석 보고서를 바탕으로 핵심 투자 포인트 요약을 작성해주세요.
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
                        
                        종합 분석 보고서:
                        {all_reports}
                        """,
                request_params=RequestParams(
                    model="gpt-4o",
                    maxTokens=2000,
                    max_iterations=2,
                    parallel_tool_calls=True,
                    use_history=True
                )
            )
        except Exception as e:
            logger.error(f"Error generating executive summary: {e}")
            executive_summary = "# 핵심 투자 포인트\n\n분석 요약을 생성하는 데 문제가 발생했습니다."

        # 면책 문구 정의
        disclaimer = """
# 투자 유의사항

본 보고서는 정보 제공을 목적으로 작성되었으며, 투자 권유를 목적으로 하지 않습니다. 
본 보고서에 기재된 내용은 작성 시점 기준으로 신뢰할 수 있는 자료에 근거하여 AI로 작성되었으나, 
그 정확성과 완전성을 보장하지 않습니다.

투자는 본인의 판단과 책임 하에 신중하게 이루어져야 하며, 
본 보고서를 참고하여 발생하는 투자 결과에 대한 책임은 투자자 본인에게 있습니다.
"""

        # 차트 저장 경로 설정
        charts_dir = os.path.join("charts", f"{company_code}_{reference_date}")
        os.makedirs(charts_dir, exist_ok=True)

        # 섹션별로 추가
        base_sections = ["price_volume_analysis", "investor_trading_analysis", "company_status", "company_overview", "news_analysis", "investment_strategy"]
        final_report = disclaimer + "\n\n" + executive_summary + "\n\n"

        ##fixme : 아래가 null인듯. 그리고 base64로 안되면 다른 방법 찾자.
        try:
            # 차트 이미지 생성
            price_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_price_chart, '가격 차트', width=900, dpi=80, image_format='jpg', compress=True,
                days=730, adjusted=True
            )

            volume_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_trading_volume_chart, '거래량 차트', width=900, dpi=80, image_format='jpg', compress=True,
                days=730
            )

            market_cap_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_market_cap_chart, '시가총액 추이', width=900, dpi=80, image_format='jpg', compress=True,
                days=730
            )

            fundamentals_chart_html = get_chart_as_base64_html(
                company_code, company_name, create_fundamentals_chart, '기본 지표', width=900, dpi=80, image_format='jpg', compress=True,
                days=730
            )
        except Exception as e:
            logger.error(f"차트 생성 중 오류 발생: {str(e)}")
            price_chart_html = None
            volume_chart_html = None
            market_cap_chart_html = None
            fundamentals_chart_html = None

        for section in base_sections:
            if section in section_reports:
                final_report += section_reports[section] + "\n\n"

                # price_volume_analysis 섹션 다음에 가격 차트와 거래량 차트 추가
                if section == "price_volume_analysis" and (price_chart_html or volume_chart_html):
                    final_report += "\n## 가격 및 거래량 차트\n\n"

                    if price_chart_html:
                        final_report += f"### 가격 차트\n\n"
                        final_report += price_chart_html + "\n\n"

                    if volume_chart_html:
                        final_report += f"### 거래량 차트\n\n"
                        final_report += volume_chart_html + "\n\n"

                # company_status 섹션 다음에 시가총액 차트와 기본 지표 차트 추가
                elif section == "company_status" and (market_cap_chart_html or fundamentals_chart_html):
                    final_report += "\n## 시가총액 및 기본 지표 차트\n\n"

                    if market_cap_chart_html:
                        final_report += f"### 시가총액 추이\n\n"
                        final_report += market_cap_chart_html + "\n\n"

                    if fundamentals_chart_html:
                        final_report += f"### 기본 지표 분석\n\n"
                        final_report += fundamentals_chart_html + "\n\n"

        # 최종 마크다운 정리
        final_report = clean_markdown(final_report)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")

        return final_report


if __name__ == "__main__":
    import time
    import threading
    import os
    import signal

    # 30분 후에 프로세스를 종료하는 타이머 함수
    def exit_after_timeout():
        time.sleep(1800)  # 30분 대기
        print("30분 타임아웃 도달: 프로세스 강제 종료")
        os.kill(os.getpid(), signal.SIGTERM)

    # 백그라운드 스레드로 타이머 시작
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    start = time.time()

    # 특정 날짜를 기준으로 분석 실행
    result = asyncio.run(analyze_stock(company_code="005380", company_name="현대차", reference_date="20250324"))

    # 결과 저장
    with open(f"현대차_분석보고서_{datetime.now().strftime('%Y%m%d')}_gpt4o.md", "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"총 실행 시간: {end - start:.2f}초")
    print(f"최종 보고서 길이: {len(result):,} 글자")
