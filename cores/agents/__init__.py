def get_agent_directory(company_name, company_code, reference_date, base_sections):
    """각 섹션별 에이전트 디렉토리를 반환"""
    from cores.agents.stock_price_agents import (
        create_price_volume_analysis_agent,
        create_investor_trading_analysis_agent
    )
    from cores.agents.company_info_agents import (
        create_company_status_agent,
        create_company_overview_agent
    )
    from cores.agents.news_strategy_agents import (
        create_news_analysis_agent,
        create_investment_strategy_agent
    )
    from cores.utils import get_wise_report_url
    
    # URL 매핑 생성
    urls = {k: get_wise_report_url(k, company_code) for k in [
        "기업현황", "기업개요", "재무분석", "투자지표", 
        "컨센서스", "경쟁사분석", "지분현황", "업종분석", "최근리포트"
    ]}
    
    # 날짜 계산
    from datetime import datetime, timedelta
    ref_date = datetime.strptime(reference_date, "%Y%m%d")
    max_years = 2
    max_years_ago = (ref_date - timedelta(days=365*max_years)).strftime("%Y%m%d")
    
    agent_creators = {
        "price_volume_analysis": lambda: create_price_volume_analysis_agent(
            company_name, company_code, reference_date, max_years_ago, max_years
        ),
        "investor_trading_analysis": lambda: create_investor_trading_analysis_agent(
            company_name, company_code, reference_date, max_years_ago, max_years
        ),
        "company_status": lambda: create_company_status_agent(
            company_name, company_code, reference_date, urls
        ),
        "company_overview": lambda: create_company_overview_agent(
            company_name, company_code, reference_date, urls
        ),
        "news_analysis": lambda: create_news_analysis_agent(
            company_name, company_code, reference_date
        ),
        "investment_strategy": lambda: create_investment_strategy_agent(
            company_name, company_code, reference_date
        )
    }
    
    agents = {}
    for section in base_sections:
        if section in agent_creators:
            agents[section] = agent_creators[section]()
    
    return agents
