import re
import subprocess

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


def cleanup_webresearch_processes():
    """mcp-server-webresearch 프로세스 정리"""
    try:
        subprocess.run(["pkill", "-f", "mcp-server-webresearch"], check=False)
        print("mcp-server-webresearch 프로세스 정리 완료")
    except Exception as e:
        print(f"프로세스 정리 중 오류 발생: {str(e)}")


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """WiseReport URL 생성"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)
