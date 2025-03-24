import streamlit as st
import pandas as pd
from datetime import datetime
import re
import asyncio
import os
from pathlib import Path
import markdown
import base64
from main import analyze_stock
from email_sender import send_email
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Thread
import uuid

# 보고서 저장 디렉토리 설정
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)

# 작업 큐 및 스레드 풀 설정
analysis_queue = Queue()
worker_pool = ThreadPoolExecutor(max_workers=2)

class AnalysisRequest:
    def __init__(self, stock_code: str, company_name: str, email: str, reference_date: str):
        self.id = str(uuid.uuid4())
        self.stock_code = stock_code
        self.company_name = company_name
        self.email = email
        self.reference_date = reference_date
        self.status = "pending"
        self.result = None

class StockAnalysisApp:
    def __init__(self):
        self.setup_page()
        self.initialize_session_state()
        self.start_background_worker()

    def setup_page(self):
        st.set_page_config(
            page_title="주식 종목 분석 서비스",
            page_icon="📊",
            layout="wide"
        )

    def initialize_session_state(self):
        """세션 상태 초기화"""
        if 'requests' not in st.session_state:
            st.session_state.requests = {}

    def start_background_worker(self):
        """백그라운드 작업자 시작"""
        def worker():
            while True:
                request = analysis_queue.get()
                try:
                    self.process_analysis_request(request)
                except Exception as e:
                    print(f"Error processing request {request.id}: {str(e)}")
                finally:
                    analysis_queue.task_done()

        for _ in range(5):  # 5개의 워커 스레드 시작
            Thread(target=worker, daemon=True).start()

    def process_analysis_request(self, request: AnalysisRequest):
        """분석 요청 처리"""
        try:
            # 캐시된 보고서 확인
            is_cached, cached_content, cached_file = self.get_cached_report(
                request.stock_code, request.reference_date
            )

            if is_cached:
                # 캐시된 보고서가 있으면 바로 이메일 전송
                send_email(request.email, cached_content)
                request.result = f"캐시된 분석 보고서가 이메일로 전송되었습니다. (파일: {cached_file.name})"
            else:
                # 새로운 분석 실행
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    report = loop.run_until_complete(analyze_stock(
                        company_code=request.stock_code,
                        company_name=request.company_name,
                        reference_date=request.reference_date
                    ))
                finally:
                    loop.close()

                # 보고서 저장
                saved_file = self.save_report(
                    request.stock_code, request.company_name,
                    request.reference_date, report
                )

                # 이메일 전송
                send_email(request.email, report)
                request.result = f"분석이 완료되었으며, 결과가 이메일로 전송되었습니다. (파일: {saved_file.name})"

            request.status = "completed"

        except Exception as e:
            request.status = "failed"
            request.result = f"분석 중 오류가 발생했습니다: {str(e)}"

    @staticmethod
    def get_cached_report(stock_code: str, reference_date: str) -> tuple[bool, str, Path | None]:
        """캐시된 보고서 검색"""
        report_pattern = f"{stock_code}_*_{reference_date}.md"
        matching_files = list(REPORTS_DIR.glob(report_pattern))

        if matching_files:
            latest_file = max(matching_files, key=lambda p: p.stat().st_mtime)
            with open(latest_file, "r", encoding="utf-8") as f:
                return True, f.read(), latest_file
        return False, "", None

    @staticmethod
    def save_report(stock_code: str, company_name: str, reference_date: str, content: str) -> Path:
        """보고서를 파일로 저장"""
        filename = f"{stock_code}_{company_name}_{reference_date}.md"
        filepath = REPORTS_DIR / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath

    def submit_analysis(self, stock_code: str, company_name: str, email: str, reference_date: str) -> str:
        """분석 요청 제출"""
        request = AnalysisRequest(stock_code, company_name, email, reference_date)
        st.session_state.requests[request.id] = request
        analysis_queue.put(request)
        return request.id

    def render_analysis_form(self):
        """분석 요청 폼 렌더링"""
        st.title("주식 종목 분석 서비스")

        with st.form("analysis_form"):
            col1, col2 = st.columns(2)

            with col1:
                company_name = st.text_input("회사명")
                stock_code = st.text_input("종목코드 (6자리)")

            with col2:
                email = st.text_input("이메일 주소")
                today = datetime.now().date()
                analysis_date = st.date_input(
                    "분석 기준일",
                    value=today,
                    max_value=today
                )

            submitted = st.form_submit_button("분석 시작", use_container_width=True)

        if submitted:
            if not self.validate_inputs(company_name, stock_code, email):
                return

            reference_date = analysis_date.strftime("%Y%m%d")
            request_id = self.submit_analysis(stock_code, company_name, email, reference_date)
            st.success("분석이 요청되었습니다. 완료되면 이메일로 결과가 전송됩니다. 이후 이 웹사이트에 재접속 후 '보고서 보기' 메뉴에서도 보실 수 있습니다.")

        # 진행 중인 요청 상태 표시
        self.show_request_status()

    def show_request_status(self):
        """요청 상태 표시"""
        if st.session_state.requests:
            st.subheader("진행 상태")
            for request_id, request in st.session_state.requests.items():
                status_color = {
                    "pending": "🟡",
                    "completed": "🟢",
                    "failed": "🔴"
                }
                status = status_color.get(request.status, "⚪")
                st.text(f"{status} 종목코드: {request.stock_code}")
                if request.result:
                    st.text(f"   결과: {request.result}")

    def validate_inputs(self, company_name: str, stock_code: str, email: str) -> bool:
        """입력값 유효성 검사"""
        if not company_name:
            st.error("회사명을 입력해주세요.")
            return False

        if not self.is_valid_stock_code(stock_code):
            st.error("올바른 종목코드를 입력해주세요 (6자리 숫자).")
            return False

        if not self.is_valid_email(email):
            st.error("올바른 이메일 주소를 입력해주세요.")
            return False

        return True

    @staticmethod
    def is_valid_stock_code(code: str) -> bool:
        return bool(re.match(r'^\d{6}$', code))

    @staticmethod
    def is_valid_email(email: str) -> bool:
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    async def process_analysis(self, stock_code: str, company_name: str, email: str, reference_date: str) -> tuple[bool, str]:
        """주식 분석 실행 및 이메일 전송"""
        try:
            # 캐시된 보고서 확인
            is_cached, cached_content, cached_file = self.get_cached_report(stock_code, reference_date)

            if is_cached:
                # 캐시된 보고서가 있으면 바로 이메일 전송
                await self.async_send_email(email, cached_content)
                return True, f"캐시된 분석 보고서가 이메일로 전송되었습니다. (파일: {cached_file.name})"

            # 새로운 분석 실행
            report = await analyze_stock(
                company_code=stock_code,
                company_name=company_name,
                reference_date=reference_date
            )

            # 보고서 저장
            saved_file = self.save_report(stock_code, company_name, reference_date, report)

            # 이메일 전송
            await self.async_send_email(email, report)

            return True, f"새로운 분석이 완료되었으며, 결과가 이메일로 전송되었습니다. (파일: {saved_file.name})"

        except Exception as e:
            return False, f"분석 중 오류가 발생했습니다: {str(e)}"

    @staticmethod
    async def async_send_email(email: str, content: str):
        """이메일 전송을 비동기적으로 처리"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(worker_pool, send_email, email, content)

    def handle_analysis_submission(self, stock_code: str, company_name: str, email: str, reference_date: str):
        """분석 제출 처리"""
        if st.session_state.processing:
            st.warning("이미 분석이 진행 중입니다. 잠시만 기다려주세요.")
            return

        st.session_state.processing = True

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, message = loop.run_until_complete(
                self.process_analysis(stock_code, company_name, email, reference_date)
            )
            loop.close()

            if success:
                st.success(message)
            else:
                st.error(message)
        finally:
            st.session_state.processing = False
            st.session_state.last_analysis = datetime.now()

    def render_report_viewer(self):
        """보고서 뷰어 페이지 렌더링"""
        st.title("분석 보고서 뷰어")

        # 보고서 필터링 옵션
        col1, col2 = st.columns(2)

        with col1:
            search_code = st.text_input("종목코드로 검색", "")

        # 저장된 보고서 목록 가져오기
        reports = list(REPORTS_DIR.glob("*.md"))

        if search_code:
            reports = [r for r in reports if search_code in r.stem]

        if not reports:
            st.warning("저장된 보고서가 없습니다.")
            return

        # 보고서 정렬 (최신순)
        reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # 보고서 선택 UI
        selected_report = st.selectbox(
            "보고서 선택",
            options=reports,
            format_func=lambda x: f"{x.stem} (작성일: {datetime.fromtimestamp(x.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})"
        )

        if selected_report:
            self.display_report(selected_report)

    def display_report(self, report_path: Path):
        """선택된 보고서 표시"""
        # 보고서 내용 읽기
        with open(report_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 다운로드 버튼 생성
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(self.get_download_link(report_path, 'md'), unsafe_allow_html=True)
        with col2:
            st.markdown(self.get_download_link(report_path, 'html'), unsafe_allow_html=True)

        # 보고서 내용 표시
        st.markdown("## 보고서 내용")
        st.markdown(content)

    @staticmethod
    def get_download_link(file_path: Path, file_format: str) -> str:
        """다운로드 링크 생성"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = f.read()

        if file_format == 'html':
            # 마크다운을 HTML로 변환
            html_content = markdown.markdown(
                data,
                extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables']
            )
            b64 = base64.b64encode(html_content.encode()).decode()
            extension = 'html'
        else:
            b64 = base64.b64encode(data.encode()).decode()
            extension = 'md'

        filename = f"{file_path.stem}.{extension}"
        return f'<a href="data:file/{extension};base64,{b64}" download="{filename}">💾 {extension.upper()} 형식으로 다운로드</a>'

    def main(self):
        st.sidebar.title("메뉴")
        menu = st.sidebar.radio("선택", ["분석 요청", "보고서 보기"])

        if menu == "분석 요청":
            self.render_analysis_form()
        else:
            self.render_report_viewer()

if __name__ == "__main__":
    app = StockAnalysisApp()
    app.main()