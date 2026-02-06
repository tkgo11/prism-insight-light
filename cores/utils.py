import re
import subprocess

# WiseReport URL template configuration
WISE_REPORT_BASE = "https://comp.wisereport.co.kr/company/"
URLS = {
    "기업현황": "c1010001.aspx?cmp_cd={}",  # Company status (Korean key for API)
    "기업개요": "c1020001.aspx?cmp_cd={}",  # Company overview (Korean key for API)
    "재무분석": "c1030001.aspx?cmp_cd={}",  # Financial analysis (Korean key for API)
    "투자지표": "c1040001.aspx?cmp_cd={}",  # Investment indicators (Korean key for API)
    "컨센서스": "c1050001.aspx?cmp_cd={}",  # Consensus (Korean key for API)
    "경쟁사분석": "c1060001.aspx?cmp_cd={}",  # Competitor analysis (Korean key for API)
    "지분현황": "c1070001.aspx?cmp_cd={}",  # Shareholding status (Korean key for API)
    "업종분석": "c1090001.aspx?cmp_cd={}",  # Industry analysis (Korean key for API)
    "최근리포트": "c1080001.aspx?cmp_cd={}"  # Recent reports (Korean key for API)
}


def clean_markdown(text: str) -> str:
    """Clean markdown text"""

    # 0. Remove GPT-5.2 artifacts
    # Remove tool call JSON patterns (e.g., {"name":"kospi_kosdaq-get_stock_ohlcv","arguments":{...}})
    text = re.sub(r'\{"name":\s*"[^"]+",\s*"arguments":\s*\{[^}]*\}\}', '', text)
    # Remove internal tokens (e.g., <|ipynb_marker|>, <|endoftext|>, etc.)
    text = re.sub(r'<\|[^|]+\|>', '', text)

    # 1. Remove backtick code blocks
    text = re.sub(r'```[^\n]*\n(.*?)\n```', r'\1', text, flags=re.DOTALL)

    # 2. Convert literal newline characters to actual newlines (GPT-5.2 compatibility)
    # Process double newlines first
    text = text.replace('\\n\\n', '\n\n')
    # Process single newlines
    text = text.replace('\\n', '\n')

    # 3. Remove unnecessary newlines between Korean characters (GPT-5.2 output cleanup)
    # Pattern matches Korean characters (Hangul syllables range: 가-힣)
    # Apply repeatedly until no more matches
    prev_text = None
    while prev_text != text:
        prev_text = text
        text = re.sub(r'([가-힣])\n([가-힣])', r'\1\2', text)

    # 4. Remove newlines inside table rows (markdown table correction)
    # Table rows must start with | and end with |
    lines = text.split('\n')
    cleaned_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # If table row starts with | but doesn't end with |, merge with next line
        if line.strip().startswith('|') and not line.strip().endswith('|'):
            merged = line
            while i + 1 < len(lines) and not merged.strip().endswith('|'):
                i += 1
                merged += lines[i]
            cleaned_lines.append(merged)
        else:
            cleaned_lines.append(line)
        i += 1
    text = '\n'.join(cleaned_lines)

    # 5. Preserve and clean markdown headings
    # Valid section title keywords (relaxed length limit: up to 50 characters)
    valid_section_keywords = [
        # Korean keywords
        '분석', '현황', '개요', '전략', '요약', '지표', '동향', '차트', '투자',
        '기술적', '펀더멘털', '뉴스', '시장', '핵심', '포인트', '의견',
        # English keywords
        'Analysis', 'Overview', 'Status', 'Strategy', 'Summary', 'Chart',
        'Technical', 'Fundamental', 'News', 'Market', 'Investment', 'Key', 'Point', 'Opinion',
        # Numbered section patterns
        '1.', '2.', '3.', '4.', '5.', '1-1', '1-2', '2-1', '2-2', '3-1', '4-1', '5-1',
        'Executive'
    ]

    def is_valid_section_header(header_text):
        """Check if this is a valid section header"""
        header_text = header_text.strip()
        # Consider it a valid header if <= 50 chars and contains keywords
        if len(header_text) <= 50:
            for keyword in valid_section_keywords:
                if keyword in header_text:
                    return True
        # Short titles starting with numbers (e.g., "1. Technical Analysis")
        if len(header_text) <= 50 and header_text and header_text[0].isdigit():
            return True
        return False

    lines = text.split('\n')
    processed_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Handle # ~ #### headings (preserve all valid markdown heading levels)
        heading_match = re.match(r'^(#{1,4})\s+(.+)$', stripped)
        if heading_match:
            heading_level = heading_match.group(1)  # #, ##, ###, or ####
            header_content = heading_match.group(2)
            if is_valid_section_header(header_content):
                # Keep valid section headers as-is
                processed_lines.append(stripped)
            else:
                # Convert ## or higher to text if > 50 chars or no keywords
                if len(heading_level) >= 2:
                    # Remove headings used for emphasis
                    processed_lines.append(header_content)
                else:
                    # Keep # (h1) as-is (report title)
                    processed_lines.append(stripped)
        else:
            processed_lines.append(line)
    text = '\n'.join(processed_lines)

    # 5-1. Ensure blank lines before and after headings
    # Add blank line before heading if missing
    text = re.sub(r'([^\n])\n(#{1,4}\s)', r'\1\n\n\2', text)
    # Add blank line after heading if missing
    text = re.sub(r'(#{1,4}\s[^\n]+)\n([^\n#])', r'\1\n\n\2', text)

    # 5-2. Ensure blank lines before and after tables (essential for markdown table parsing)
    lines = text.split('\n')
    result_lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        is_table_line = stripped.startswith('|')
        prev_line = lines[i - 1].strip() if i > 0 else ''
        prev_is_table = prev_line.startswith('|')
        prev_is_empty = prev_line == ''

        # Add blank line before table start (if previous line is not a table and not empty)
        if is_table_line and not prev_is_table and not prev_is_empty:
            result_lines.append('')

        result_lines.append(line)

        # Add blank line after table end (if next line is not a table and not empty)
        # This part is handled in the next iteration

    # Add blank line after table end
    final_lines = []
    for i, line in enumerate(result_lines):
        final_lines.append(line)
        stripped = line.strip()
        is_table_line = stripped.startswith('|')
        if is_table_line and i + 1 < len(result_lines):
            next_line = result_lines[i + 1].strip()
            next_is_table = next_line.startswith('|')
            next_is_empty = next_line == ''
            if not next_is_table and not next_is_empty:
                final_lines.append('')

    text = '\n'.join(final_lines)

    # 6. Add missing newlines after headers/subheadings (when GPT-5.2 concatenates without newlines)
    # Korean header endings and sentence starters for fixing concatenated text
    # Common Korean section endings: perspective, plan, interpretation, trend, status, overview, strategy, summary, background, conclusion
    header_endings = ['관점', '계획', '해석', '동향', '현황', '개요', '전략', '요약', '배경', '결론']
    # Common Korean sentence starters: this, next, this is, this time, that, actual, current, however, therefore, especially, also, but, meanwhile
    sentence_starters = ['본', '다음', '이는', '이번', '해당', '실제', '현재', '그러', '따라', '특히', '또한', '다만', '한편']

    for ending in header_endings:
        for starter in sentence_starters:
            # Fix concatenated Korean text (when concatenated without newlines)
            text = text.replace(f'{ending}{starter}', f'{ending}\n\n{starter}')

    # 7. Add missing newlines after numbered subheadings
    # Fix numbered Korean subheadings concatenated with sentence starters
    for starter in sentence_starters:
        # Handle patterns with numbered sections
        # Pattern matches: "n) Korean_title (plan|status|analysis|trend|overview|background)" + sentence_starter
        text = re.sub(rf'(\d+\)\s*[가-힣]+\s*(?:계획|현황|분석|동향|개요|배경))({starter})', rf'\1\n\n\2', text)

    return text


def get_wise_report_url(report_type: str, company_code: str) -> str:
    """Generate WiseReport URL"""
    return WISE_REPORT_BASE + URLS[report_type].format(company_code)
