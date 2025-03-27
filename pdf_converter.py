#!/usr/bin/env python3
"""
마크다운 파일을 PDF로 변환하는 모듈

다양한 변환 방식 제공:
1. pdfkit 기반 HTML 중간 변환 (wkhtmltopdf 필요)
2. reportlab 직접 렌더링
3. mdpdf 간편 변환 (추가됨)
"""
import os
import logging
import markdown
import tempfile
import PyPDF2
import html2text

# 로거 설정
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# CSS 스타일
DEFAULT_CSS = """
body {
    font-family: Arial, sans-serif;
    line-height: 1.6;
    margin: 40px;
    color: #333;
    max-width: 900px;
    margin: 0 auto;
    padding: 20px;
}
h1 {
    color: #0056b3;
    border-bottom: 2px solid #ddd;
    padding-bottom: 5px;
}
h2 {
    color: #0056b3;
    border-bottom: 1px solid #eee;
    padding-bottom: 3px;
}
h3 {
    color: #0056b3;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 20px 0;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px;
    text-align: left;
}
th {
    background-color: #f2f2f2;
}
tr:nth-child(even) {
    background-color: #f9f9f9;
}
code {
    background-color: #f5f5f5;
    padding: 2px 4px;
    border-radius: 4px;
    font-family: Consolas, monospace;
}
blockquote {
    border-left: 4px solid #ddd;
    padding-left: 10px;
    margin-left: 10px;
    color: #555;
}
pre {
    background-color: #f5f5f5;
    padding: 10px;
    border-radius: 4px;
    overflow-x: auto;
}
"""

def markdown_to_html(md_file_path, add_css=True):
    """
    마크다운 파일을 HTML로 변환

    Args:
        md_file_path (str): 마크다운 파일 경로
        add_css (bool): CSS 스타일 추가 여부

    Returns:
        str: 변환된 HTML 문자열
    """
    try:
        # 마크다운 파일이 있는 디렉토리 경로 얻기
        base_dir = os.path.dirname(os.path.abspath(md_file_path))

        # 마크다운 파일 읽기
        with open(md_file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # 이미지 경로를 절대 경로로 변환 (정규식 이용)
        import re

        # HTML 이미지 태그 찾기
        img_tags_pattern = r'<img\s+src="data:image/png;base64,[^"]+"\s+[^>]+>'
        img_tags = re.findall(img_tags_pattern, md_content)

        # 임시 플레이스홀더로 대체
        for i, tag in enumerate(img_tags):
            placeholder = f"___HTML_IMG_TAG_{i}___"
            md_content = md_content.replace(tag, placeholder)

        # 나머지 이미지 처리 (기존 코드)
        def replace_image_path(match):
            img_path = match.group(2)
            if not os.path.isabs(img_path):
                img_path = os.path.abspath(os.path.join(base_dir, img_path))
            return f'![{match.group(1)}]({img_path})'

        # 마크다운의 이미지 링크 패턴을 찾아 절대 경로로 변환
        md_content = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_image_path, md_content)

        # 마크다운을 HTML로 변환 (확장 기능 활성화)
        html = markdown.markdown(
            md_content,
            extensions=[
                'markdown.extensions.tables',
                'markdown.extensions.fenced_code',
                'markdown.extensions.nl2br',
                'markdown.extensions.sane_lists',
                'markdown.extensions.toc',
                'markdown.extensions.attr_list',  # 속성 지원
                'markdown.extensions.extra'       # 추가 기능 (HTML 포함)
            ]
        )

        # HTML 이미지 태그 다시 복원
        for i, tag in enumerate(img_tags):
            placeholder = f"___HTML_IMG_TAG_{i}___"
            html = html.replace(placeholder, tag)

        # 완전한 HTML 문서로 만들기
        if add_css:
            full_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                {DEFAULT_CSS}
                </style>
            </head>
            <body>
                {html}
            </body>
            </html>
            """
        else:
            full_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
            </head>
            <body>
                {html}
            </body>
            </html>
            """

        return full_html

    except Exception as e:
        logger.error(f"HTML 변환 중 오류: {str(e)}")
        raise

def markdown_to_pdf_pdfkit(md_file_path, pdf_file_path):
    """
    pdfkit(wkhtmltopdf)를 사용하여 마크다운을 PDF로 변환

    Linux 설치 방법: dnf install wkhtmltopdf

    Args:
        md_file_path (str): 마크다운 파일 경로
        pdf_file_path (str): 출력 PDF 파일 경로
    """
    try:
        # pdfkit 임포트 (설치 필요: pip install pdfkit + wkhtmltopdf 바이너리)
        import pdfkit

        # 마크다운을 HTML로 변환
        html_content = markdown_to_html(md_file_path)

        # HTML을 임시 파일로 저장
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            f.write(html_content.encode('utf-8'))
            temp_html = f.name

        # 옵션 설정
        options = {
            'encoding': 'UTF-8',
            'page-size': 'A4',
            'margin-top': '20mm',
            'margin-right': '20mm',
            'margin-bottom': '20mm',
            'margin-left': '20mm',
            'enable-local-file-access': None,
            'quiet': ''
        }

        # HTML을 PDF로 변환
        pdfkit.from_file(temp_html, pdf_file_path, options=options)

        # 임시 파일 삭제
        os.unlink(temp_html)

        logger.info(f"pdfkit로 PDF 변환 완료: {pdf_file_path}")

    except ImportError:
        logger.error("pdfkit 라이브러리가 설치되지 않았습니다. pip install pdfkit로 설치하세요.")
        raise
    except Exception as e:
        logger.error(f"pdfkit 변환 중 오류: {str(e)}")
        raise

def markdown_to_pdf_reportlab(md_file_path, pdf_file_path):
    """
    ReportLab을 사용하여 마크다운을 PDF로 직접 변환

    Args:
        md_file_path (str): 마크다운 파일 경로
        pdf_file_path (str): 출력 PDF 파일 경로
    """
    try:
        # ReportLab 임포트 (설치 필요: pip install reportlab)
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        # 마크다운 파일 읽기
        with open(md_file_path, 'r', encoding='utf-8') as f:
            md_content = f.read()

        # 제목 추출 (# 으로 시작하는 첫 번째 줄)
        title = "보고서"
        for line in md_content.split('\n'):
            if line.startswith('# '):
                title = line[2:].strip()
                break

        # PDF 문서 생성
        doc = SimpleDocTemplate(
            pdf_file_path,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72
        )

        # 스타일 설정
        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='Heading1',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=12
        ))
        styles.add(ParagraphStyle(
            name='Heading2',
            parent=styles['Heading2'],
            fontSize=14,
            spaceBefore=12,
            spaceAfter=6
        ))
        styles.add(ParagraphStyle(
            name='Heading3',
            parent=styles['Heading3'],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=4
        ))

        # 콘텐츠 파싱 및 변환 (간단한 구현)
        elements = []

        # 제목 추가
        elements.append(Paragraph(title, styles['Heading1']))
        elements.append(Spacer(1, 0.25*inch))

        # 내용 추가 (간단한 파싱)
        current_section = []
        in_code_block = False

        for line in md_content.split('\n'):
            # 코드 블록 처리
            if line.startswith('```'):
                in_code_block = not in_code_block
                continue

            if in_code_block:
                current_section.append(line)
                continue

            # 제목 처리
            if line.startswith('# '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[2:], styles['Heading1']))
            elif line.startswith('## '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[3:], styles['Heading2']))
            elif line.startswith('### '):
                if current_section:
                    elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                    current_section = []
                elements.append(Paragraph(line[4:], styles['Heading3']))
            # 일반 텍스트
            elif line.strip():
                current_section.append(line)
            # 빈 줄은 문단 구분
            elif current_section:
                elements.append(Paragraph('\n'.join(current_section), styles['Normal']))
                current_section = []
                elements.append(Spacer(1, 0.1*inch))

        # 마지막 섹션 추가
        if current_section:
            elements.append(Paragraph('\n'.join(current_section), styles['Normal']))

        # PDF 빌드
        doc.build(elements)

        logger.info(f"ReportLab으로 PDF 변환 완료: {pdf_file_path}")

    except ImportError:
        logger.error("ReportLab 라이브러리가 설치되지 않았습니다. pip install reportlab로 설치하세요.")
        raise
    except Exception as e:
        logger.error(f"ReportLab 변환 중 오류: {str(e)}")
        raise

def markdown_to_pdf_mdpdf(md_file_path, pdf_file_path):
    """
    mdpdf 라이브러리를 사용하여 마크다운을 PDF로 변환

    설치: pip install mdpdf

    Args:
        md_file_path (str): 마크다운 파일 경로
        pdf_file_path (str): 출력 PDF 파일 경로
    """
    try:
        # mdpdf 임포트 (설치 필요: pip install mdpdf)
        from mdpdf import MarkdownPdf

        # 마크다운을 PDF로 변환
        md = MarkdownPdf()
        md.convert(md_file_path, pdf_file_path)

        logger.info(f"mdpdf로 PDF 변환 완료: {pdf_file_path}")

    except ImportError:
        logger.error("mdpdf 라이브러리가 설치되지 않았습니다. pip install mdpdf로 설치하세요.")
        raise
    except Exception as e:
        logger.error(f"mdpdf 변환 중 오류: {str(e)}")
        raise

def markdown_to_pdf(md_file_path, pdf_file_path, method='pdfkit'):
    """
    마크다운 파일을 PDF로 변환 (기본 메서드 선택)

    Args:
        md_file_path (str): 마크다운 파일 경로
        pdf_file_path (str): 출력 PDF 파일 경로
        method (str): 변환 방식 ('pdfkit', 'reportlab', 'mdpdf')
    """
    logger.info(f"마크다운 PDF 변환 시작: {md_file_path} -> {pdf_file_path}")

    try:
        if method == 'pdfkit':
            markdown_to_pdf_pdfkit(md_file_path, pdf_file_path)
        elif method == 'reportlab':
            markdown_to_pdf_reportlab(md_file_path, pdf_file_path)
        elif method == 'mdpdf':
            markdown_to_pdf_mdpdf(md_file_path, pdf_file_path)
        else:
            # 기본값은 pdfkit 시도 후 실패하면 reportlab, 마지막으로 mdpdf
            try:
                markdown_to_pdf_pdfkit(md_file_path, pdf_file_path)
            except Exception as e1:
                logger.warning(f"pdfkit 실패, ReportLab 시도: {str(e1)}")
                try:
                    markdown_to_pdf_reportlab(md_file_path, pdf_file_path)
                except Exception as e2:
                    logger.warning(f"ReportLab 실패, mdpdf 시도: {str(e2)}")
                    markdown_to_pdf_mdpdf(md_file_path, pdf_file_path)

    except Exception as e:
        logger.error(f"PDF 변환 실패: {str(e)}")
        raise


# PDF에서 텍스트 추출
def extract_text_from_pdf(pdf_path):
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        text = ""
        for page in reader.pages:
            text += page.extract_text()
    return text

# 텍스트를 마크다운으로 변환
def convert_to_markdown(text):
    h = html2text.HTML2Text()
    h.ignore_links = False
    markdown_text = h.handle(text)
    return markdown_text

# PDF to markdown_text
def pdf_to_markdown_text(pdf_path):
    text = extract_text_from_pdf(pdf_path)
    return convert_to_markdown(text)


if __name__ == "__main__":
    # 테스트 코드
    import sys

    if len(sys.argv) < 3:
        print(f"사용법: {sys.argv[0]} <마크다운파일> <PDF파일> [변환방식]")
        sys.exit(1)

    md_file = sys.argv[1]
    pdf_file = sys.argv[2]
    method = sys.argv[3] if len(sys.argv) > 3 else 'auto'

    markdown_to_pdf(md_file, pdf_file, method)

    markdown_text_content = pdf_to_markdown_text(pdf_file)
    print(f"markdown_text_content: {markdown_text_content}")
