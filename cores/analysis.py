import os
from datetime import datetime

from mcp_agent.app import MCPApp

from cores.agents import get_agent_directory
from cores.report_generation import generate_report, generate_summary, generate_investment_strategy, get_disclaimer, generate_market_report
from cores.stock_chart import (
    create_price_chart,
    create_trading_volume_chart,
    create_market_cap_chart,
    create_fundamentals_chart,
    get_chart_as_base64_html
)
from cores.utils import clean_markdown


# 시장 분석 캐시 저장소 (전역 변수)
_market_analysis_cache = {}

async def analyze_stock(company_code: str = "000660", company_name: str = "SK하이닉스", reference_date: str = None):
    """
    주식 종합 분석 보고서 생성
    
    Args:
        company_code: 종목 코드
        company_name: 회사명
        reference_date: 분석 기준일 (YYYYMMDD 형식)
    
    Returns:
        str: 생성된 최종 보고서 마크다운 텍스트
    """
    # 1. 초기 설정 및 전처리
    app = MCPApp(name="stock_analysis")

    # reference_date가 없으면 오늘 날짜를 사용
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")


    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(f"시작: {company_name}({company_code}) 분석 - 기준일: {reference_date}")

        # 2. 공유 리소스로 데이터를 저장할 딕셔너리 생성
        section_reports = {}

        # 3. 분석할 섹션 정의
        base_sections = ["price_volume_analysis", "investor_trading_analysis", "company_status", "company_overview", "news_analysis", "market_index_analysis"]

        # 4. 에이전트 가져오기
        agents = get_agent_directory(company_name, company_code, reference_date, base_sections)

        # 5. 기본 분석 순차적으로 실행 (rate limit 대처를 위해 병렬 대신 순차 실행)
        for section in base_sections:
            if section in agents:
                logger.info(f"Processing {section} for {company_name}...")

                try:
                    agent = agents[section]
                    if section == "market_index_analysis":
                        # 캐시에 데이터가 있는지 확인
                        if "report" in _market_analysis_cache:
                            logger.info(f"Using cached market analysis")
                            report = _market_analysis_cache["report"]
                        else:
                            logger.info(f"Generating new market analysis")
                            report = await generate_market_report(agent, section, reference_date, logger)
                            # 캐시에 저장
                            _market_analysis_cache["report"] = report
                    else:
                        report = await generate_report(agent, section, company_name, company_code, reference_date, logger)
                    section_reports[section] = report
                except Exception as e:
                    logger.error(f"Final failure processing {section}: {e}")
                    section_reports[section] = f"분석 실패: {section}"

        # 6. 다른 보고서들의 내용을 통합
        combined_reports = ""
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

        # 7. 투자 전략 생성
        try:
            logger.info(f"Processing investment_strategy for {company_name}...")

            investment_strategy = await generate_investment_strategy(
                section_reports, combined_reports, company_name, company_code, reference_date, logger
            )
            section_reports["investment_strategy"] = investment_strategy.lstrip('\n')
            logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        except Exception as e:
            logger.error(f"Error processing investment_strategy: {e}")
            section_reports["investment_strategy"] = "투자 전략 분석 실패"

        # 8. 모든 섹션을 포함한 종합 보고서 생성
        all_reports = ""
        for section in base_sections + ["investment_strategy"]:
            if section in section_reports:
                all_reports += f"\n\n--- {section.upper()} ---\n\n"
                all_reports += section_reports[section]

        # 9. 요약 생성
        try:
            executive_summary = await generate_summary(
                section_reports, company_name, company_code, reference_date, logger
            )
        except Exception as e:
            logger.error(f"Error generating executive summary: {e}")
            executive_summary = "# 핵심 투자 포인트\n\n분석 요약을 생성하는 데 문제가 발생했습니다."

        # 10. 차트 생성
        charts_dir = os.path.join("../charts", f"{company_code}_{reference_date}")
        os.makedirs(charts_dir, exist_ok=True)

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

        # 11. 최종 보고서 구성
        disclaimer = get_disclaimer()
        final_report = disclaimer + "\n\n" + executive_summary + "\n\n"

        all_sections = base_sections + ["investment_strategy"]
        for section in all_sections:
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

        # 12. 최종 마크다운 정리
        final_report = clean_markdown(final_report)

        logger.info(f"Finalized report for {company_name} - {len(final_report)} characters")
        logger.info(f"Analysis completed for {company_name}.")

        return final_report
