import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import markdown
import markdown.extensions.fenced_code
import markdown.extensions.tables
from config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD

def convert_md_to_html(md_content: str) -> str:
    """마크다운을 HTML로 변환"""
    # GitHub 스타일의 CSS
    css = """
    <style>
        body { 
            font-family: -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;
            line-height: 1.6;
            max-width: 1000px;
            margin: 0 auto;
            padding: 20px;
        }
        h1 { color: #24292e; border-bottom: 1px solid #eaecef; }
        h2 { color: #24292e; border-bottom: 1px solid #eaecef; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #dfe2e5; padding: 6px 13px; }
        th { background-color: #f6f8fa; }
        code { background-color: #f6f8fa; padding: 2px 4px; border-radius: 3px; }
        pre { background-color: #f6f8fa; padding: 16px; overflow: auto; border-radius: 3px; }
    </style>
    """

    # 마크다운을 HTML로 변환
    html = markdown.markdown(
        md_content,
        extensions=[
            'markdown.extensions.fenced_code',
            'markdown.extensions.tables',
            'markdown.extensions.toc'
        ]
    )

    # 완성된 HTML 문서
    complete_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        {css}
    </head>
    <body>
        {html}
    </body>
    </html>
    """

    return complete_html

def send_email(to_email: str, report_content: str) -> bool:
    """이메일 전송 함수"""
    try:
        # 이메일 메시지 생성
        msg = MIMEMultipart('alternative')
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "주식 종목 분석 보고서"

        # 1. HTML 버전 (메인 컨텐츠)
        html_content = convert_md_to_html(report_content)
        msg.attach(MIMEText(html_content, 'html'))

        # 2. 마크다운 파일 첨부
        md_attachment = MIMEText(report_content, 'plain')
        md_attachment.add_header('Content-Disposition', 'attachment', filename='analysis_report.md')
        msg.attach(md_attachment)

        # 3. HTML 파일 첨부
        html_attachment = MIMEText(html_content, 'html')
        html_attachment.add_header('Content-Disposition', 'attachment', filename='analysis_report.html')
        msg.attach(html_attachment)

        # SMTP 서버 연결 및 로그인
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)

        # 이메일 발송
        server.send_message(msg)
        server.quit()
        return True

    except Exception as e:
        print(f"이메일 전송 중 오류 발생: {str(e)}")
        return False