import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import markdown
import markdown.extensions.fenced_code
import markdown.extensions.tables
from config import SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD

def convert_md_to_html(md_content: str) -> str:
    """Convert Markdown to HTML"""
    # GitHub-style CSS
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

    # Convert Markdown to HTML
    html = markdown.markdown(
        md_content,
        extensions=[
            'markdown.extensions.fenced_code',
            'markdown.extensions.tables',
            'markdown.extensions.toc'
        ]
    )

    # Complete HTML document
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
    """Email sending function"""
    try:
        # Create email message
        msg = MIMEMultipart('alternative')
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = "주식 종목 분석 보고서"

        # 1. HTML version (main content)
        html_content = convert_md_to_html(report_content)
        msg.attach(MIMEText(html_content, 'html'))

        # 2. Attach Markdown file
        md_attachment = MIMEText(report_content, 'plain')
        md_attachment.add_header('Content-Disposition', 'attachment', filename='analysis_report.md')
        msg.attach(md_attachment)

        # 3. Attach HTML file
        html_attachment = MIMEText(html_content, 'html')
        html_attachment.add_header('Content-Disposition', 'attachment', filename='analysis_report.html')
        msg.attach(html_attachment)

        # Connect to SMTP server and login
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)

        # Send email
        server.send_message(msg)
        server.quit()
        return True

    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False