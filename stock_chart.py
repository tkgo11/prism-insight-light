"""
elegant_stock_charts.py - 전문 주식 시각화 도구 for AI 주식 리포팅

프로페셔널 퀄리티의 주식 차트를 생성합니다.
투자 전문가 수준의 시각화를 제공하고 데이터 인사이트를 강조합니다.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import seaborn as sns
from matplotlib.ticker import FuncFormatter
import mplfinance as mpf
from datetime import datetime, timedelta
import warnings
import os
import platform
import matplotlib as mpl
import base64
from io import BytesIO

import matplotlib
matplotlib.use('Agg')  # 그래픽 백엔드를 Agg(비인터랙티브)로 명시적 설정

# 한글 폰트 설정을 위한 매우 강력한 방법
def configure_korean_font():
    """한글 폰트 설정을 위한 강력한 함수"""
    global KOREAN_FONT_PATH, KOREAN_FONT_PROP

    system = platform.system()

    # 기본 설정
    plt.rcParams['axes.unicode_minus'] = False

    if system == 'Darwin':  # macOS
        # macOS 폰트 경로들
        font_paths = [
            '/System/Library/Fonts/AppleSDGothicNeo.ttc',
            '/Library/Fonts/AppleSDGothicNeo.ttc',
            '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
            # 나눔 폰트가 설치된 경우의 경로
            '/Library/Fonts/NanumGothic.ttf',
            # 사용자 폰트 디렉토리 추가
            os.path.expanduser('~/Library/Fonts/AppleSDGothicNeo.ttc'),
            os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),
        ]

        # 폰트 파일이 존재하는지 확인
        for path in font_paths:
            if os.path.exists(path):
                # 폰트 매니저에 등록
                fm.fontManager.addfont(path)
                KOREAN_FONT_PATH = path
                KOREAN_FONT_PROP = fm.FontProperties(fname=path)

                # matplotlib 설정
                plt.rcParams['font.family'] = 'AppleSDGothicNeo'
                mpl.rcParams['font.family'] = 'AppleSDGothicNeo'

                print(f"한글 폰트 설정 완료: {path}")
                return path

        # 경로로 찾기 실패시, 이름으로 시도
        korean_font_list = ['AppleSDGothicNeo-Regular', 'Apple SD Gothic Neo', 'AppleGothic', 'Malgun Gothic', 'NanumGothic']
        for font_name in korean_font_list:
            try:
                # 시스템에 설치된 폰트 중에서 해당 이름을 가진 폰트 찾기
                font_path = fm.findfont(fm.FontProperties(family=font_name))
                if font_path and not font_path.endswith('afm'):
                    # matplotlib 설정
                    plt.rcParams['font.family'] = font_name
                    mpl.rcParams['font.family'] = font_name
                    KOREAN_FONT_PATH = font_path
                    KOREAN_FONT_PROP = fm.FontProperties(family=font_name)

                    print(f"한글 폰트 설정 완료 (이름): {font_name}, 경로: {font_path}")
                    return font_path
            except:
                continue

    elif system == 'Windows':
        # Windows의 경우
        try:
            plt.rcParams['font.family'] = 'Malgun Gothic'
            mpl.rcParams['font.family'] = 'Malgun Gothic'
            KOREAN_FONT_PROP = fm.FontProperties(family='Malgun Gothic')
            print("한글 폰트 설정 완료: Malgun Gothic (Windows)")
            return "Malgun Gothic"
        except:
            pass

    else:  # Linux
        # Linux 폰트 경로
        font_paths = [
            '/usr/share/fonts/google-nanum/NanumGothic.ttf',
            '/usr/share/fonts/nanum/NanumGothicCoding.ttf',
            '/usr/share/fonts/korean/NanumGothic.ttf',
            '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
            '/usr/share/fonts/NanumGothic.ttf'
        ]

        for path in font_paths:
            if os.path.exists(path):
                # 폰트 매니저에 등록
                fm.fontManager.addfont(path)
                KOREAN_FONT_PATH = path
                KOREAN_FONT_PROP = fm.FontProperties(fname=path)

                # matplotlib 설정
                plt.rcParams['font.family'] = 'NanumGothic'
                mpl.rcParams['font.family'] = 'NanumGothic'

                print(f"한글 폰트 설정 완료: {path}")
                return path

    print("⚠️ 한글 폰트 설정 실패: 한글이 제대로 표시되지 않을 수 있습니다.")
    return None

# 전역 변수 초기화
KOREAN_FONT_PATH = None
KOREAN_FONT_PROP = None

# 실행 시 즉시 설정
KOREAN_FONT_PATH = configure_korean_font()

def get_chart_as_base64_html(ticker, company_name, chart_function, chart_name, width=900,
                             dpi=80, image_format='jpg', compress=True, **kwargs):
    """
    차트를 생성하고 압축한 후 Base64 인코딩된 HTML 이미지 태그로 반환

    Args:
        ticker: 종목 코드
        company_name: 회사명
        chart_function: 차트 생성 함수
        chart_name: 차트명
        width: 이미지 너비 (픽셀)
        dpi: 해상도 (dots per inch), 낮을수록 파일 크기 감소
        image_format: 이미지 형식 ('png', 'jpg', 'jpeg')
        compress: 압축 적용 여부
        **kwargs: 차트 함수에 전달할 추가 매개변수

    Returns:
        HTML 이미지 태그가 포함된 문자열
    """
    # 차트 생성 매개변수 설정
    chart_kwargs = {
        'ticker': ticker,
        'company_name': company_name,
        'save_path': None
    }
    chart_kwargs.update(kwargs)

    # 차트 생성
    fig = chart_function(**chart_kwargs)

    if fig is None:
        return None

    # 이미지를 메모리에 저장 (압축 설정 적용)
    buffer = BytesIO()

    # 이미지 형식에 따라 저장 옵션 설정
    save_kwargs = {
        'format': image_format,
        'bbox_inches': 'tight',
        'dpi': dpi
    }

    if image_format.lower() == 'png' and compress:
        save_kwargs['transparent'] = False
        save_kwargs['facecolor'] = 'white'
        save_kwargs['compress_level'] = 9  # 최대 압축 (0-9)

    # quality 매개변수 없이 저장
    fig.savefig(buffer, **save_kwargs)

    plt.close(fig)  # 메모리 누수 방지
    buffer.seek(0)

    # 추가 이미지 압축 (PIL 사용)
    if compress and image_format.lower() in ['jpg', 'jpeg']:
        try:
            from PIL import Image
            # 버퍼에서 이미지 읽기
            img = Image.open(buffer)
            # 새 버퍼에 압축하여 저장
            new_buffer = BytesIO()
            img.save(new_buffer, format='JPEG', quality=85, optimize=True)
            buffer = new_buffer
            buffer.seek(0)
        except ImportError:
            # PIL 라이브러리가 없으면 그냥 진행
            pass

    # Base64로 인코딩
    img_str = base64.b64encode(buffer.getvalue()).decode('utf-8')

    # 파일 크기 디버깅용 (선택적)
    # size_kb = len(buffer.getvalue()) / 1024
    # print(f"Chart '{chart_name}' size: {size_kb:.1f} KB")

    # 이미지 형식에 따른 MIME 타입 설정
    content_type = f"image/{image_format.lower()}"
    if image_format.lower() == 'jpg':
        content_type = 'image/jpeg'

    # HTML 이미지 태그 생성
    return f'<img src="data:{content_type};base64,{img_str}" alt="{company_name} {chart_name}" width="{width}" />'

# mplfinance에서 한글 표시하기 위한 사용자 지정 스타일
def create_mpf_style(base_mpl_style='seaborn-v0_8-whitegrid'):
    """mplfinance 라이브러리에서 사용할 수 있는 한글 지원 스타일 생성"""
    # 기본 스타일 사용
    plt.style.use(base_mpl_style)

    # 한글 폰트 설정을 포함한 RC 파라미터
    rc_font = {
        'font.family': plt.rcParams['font.family'],
        'font.size': 10,
        'axes.unicode_minus': False
    }

    # 차트 컬러 설정
    mc = mpf.make_marketcolors(
        up='#089981', down='#F23645',
        edge='inherit',
        wick='inherit',
        volume={'up': '#a3f7b5', 'down': '#ffa5a5'},
    )

    # mplfinance 스타일 생성
    s = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle='-',
        gridcolor='#e6e6e6',
        gridaxis='both',
        rc=rc_font,
        facecolor='white'
    )

    return s

# stock_api에서 함수들 임포트
from pykrx.stock.stock_api import (
    get_market_ohlcv_by_date,
    get_market_cap_by_date,
    get_market_fundamental_by_date,
    get_market_trading_volume_by_investor,
    get_market_trading_value_by_investor,
    get_market_trading_volume_by_date,
    get_market_trading_value_by_date,
    get_market_ticker_name
)

# 전문적인 차트 스타일 설정
sns.set_context("paper", font_scale=1.2)
warnings.filterwarnings('ignore')

# 컬러 팔레트 - 전문적인 금융 보고서 스타일
PRIMARY_COLORS = ["#0066cc", "#ff9500", "#00cc99", "#cc3300", "#6600cc"]
SECONDARY_COLORS = ["#e6f2ff", "#fff4e6", "#e6fff7", "#ffe6e6", "#f2e6ff"]
DIVERGING_COLORS = ["#d73027", "#fc8d59", "#fee090", "#e0f3f8", "#91bfdb", "#4575b4"]

def format_thousands(x, pos):
    """천 단위 구분자로 숫자 포맷팅"""
    return f'{int(x):,}'

def format_millions(x, pos):
    """백만 단위로 숫자 포맷팅"""
    return f'{x/1000000:.1f}M'

def format_billions(x, pos):
    """십억 단위로 숫자 포맷팅"""
    return f'{x/1000000000:.1f}B'

def format_trillions(x, pos):
    """조 단위로 숫자 포맷팅"""
    return f'{x/1000000000000:.1f}T'

def format_percentage(x, pos):
    """퍼센트로 숫자 포맷팅"""
    return f'{x:.1f}%'

def select_number_formatter(max_value):
    """데이터 크기에 따라 적절한 포맷터 선택"""
    if max_value < 1000000:
        return FuncFormatter(format_thousands)
    elif max_value < 1000000000:
        return FuncFormatter(format_millions)
    elif max_value < 1000000000000:
        return FuncFormatter(format_billions)
    else:
        return FuncFormatter(format_trillions)

def create_price_chart(ticker, company_name=None, days=730, save_path=None, adjusted=True):
    """
    우아한 OHLCV 가격 차트 생성 (캔들스틱, 거래량, 이동평균선 포함)

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼
    company_name : str, optional
        회사명 (제목용)
    days : int, optional
        조회 기간 (일)
    save_path : str, optional
        차트 저장 경로 (None이면 화면에 표시)
    adjusted : bool, optional
        수정주가 여부

    Returns:
    --------
    fig : matplotlib figure
        차트가 포함된 figure 객체
    """
    # 날짜 범위 계산
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # 회사명이 없으면 가져오기
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except:
            company_name = ticker

    # 주식 데이터 가져오기
    df = get_market_ohlcv_by_date(start_date, end_date, ticker, adjusted=adjusted)

    if df is None or len(df) == 0:
        print(f"{ticker}에 대한 데이터가 없습니다.")
        return None

    # 인덱스가 datetime인지 확인
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # 날짜 오름차순 정렬
    df = df.sort_index()

    # 이동평균선 계산
    df['MA20'] = df['종가'].rolling(window=20).mean()
    df['MA60'] = df['종가'].rolling(window=60).mean()
    df['MA120'] = df['종가'].rolling(window=120).mean()

    # mplfinance에 맞는 컬럼명으로 변경
    ohlc_df = df.rename(columns={
        '시가': 'Open',
        '고가': 'High',
        '저가': 'Low',
        '종가': 'Close',
        '거래량': 'Volume'
    })

    # 한글 지원 mplfinance 스타일 생성
    s = create_mpf_style()

    # 플롯 타이틀
    title = f"{company_name} ({ticker}) - 가격 차트"

    # 추가 데이터 플롯 설정
    additional_plots = [
        mpf.make_addplot(df['MA20'], color='#ff9500', width=1),
        mpf.make_addplot(df['MA60'], color='#0066cc', width=1.5),
        mpf.make_addplot(df['MA120'], color='#cc3300', width=1.5, linestyle='--'),
    ]

    if KOREAN_FONT_PATH:
        # 한글 폰트 직접 등록 (mplfinance 전용)
        font_prop = fm.FontProperties(fname=KOREAN_FONT_PATH)
        plt.rcParams['font.family'] = font_prop.get_name()
        mpl.rcParams['font.family'] = font_prop.get_name()

    # 차트 생성
    fig, axes = mpf.plot(
        ohlc_df,
        type='candle',
        style=s,
        title=title,
        ylabel='가격',
        volume=True,
        figsize=(12, 8),
        tight_layout=True,
        addplot=additional_plots,
        panel_ratios=(4, 1),
        returnfig=True
    )

    if KOREAN_FONT_PROP:
        # 제목 다시 설정
        fig.suptitle(
            f"{company_name} ({ticker}) - 가격 차트",
            fontproperties=KOREAN_FONT_PROP,
            fontsize=16,
            fontweight='bold'
        )

    # 가격과 거래량 축 가져오기
    ax1, ax2 = axes[0], axes[2]

    # 이평선 범례 추가
    if KOREAN_FONT_PROP:
        ax1.legend(['20일 이평선', '60일 이평선', '120일 이평선'], loc='upper left', prop=KOREAN_FONT_PROP)
    else:
        ax1.legend(['MA20', 'MA60', 'MA120'], loc='upper left')

    # 중요 가격 포인트에 주석 추가
    max_point = df['종가'].idxmax()
    min_point = df['종가'].idxmin()
    last_point = df.index[-1]

    # 주석에 한글 폰트 적용
    if KOREAN_FONT_PROP:
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

        ax1.annotate(
            f"고점: {df.loc[max_point, '종가']:,.0f}",
            xy=(max_point, df.loc[max_point, '종가']),
            xytext=(0, 15),
            textcoords='offset points',
            ha='center',
            va='bottom',
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )

        ax1.annotate(
            f"저점: {df.loc[min_point, '종가']:,.0f}",
            xy=(min_point, df.loc[min_point, '종가']),
            xytext=(0, -15),
            textcoords='offset points',
            ha='center',
            va='top',
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )

        ax1.annotate(
            f"현재: {df.loc[last_point, '종가']:,.0f}",
            xy=(last_point, df.loc[last_point, '종가']),
            xytext=(15, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        # 영문 대체 버전
        bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

        ax1.annotate(
            f"High: {df.loc[max_point, '종가']:,.0f}",
            xy=(max_point, df.loc[max_point, '종가']),
            xytext=(0, 15),
            textcoords='offset points',
            ha='center',
            va='bottom',
            bbox=bbox_props
        )

        ax1.annotate(
            f"Low: {df.loc[min_point, '종가']:,.0f}",
            xy=(min_point, df.loc[min_point, '종가']),
            xytext=(0, -15),
            textcoords='offset points',
            ha='center',
            va='top',
            bbox=bbox_props
        )

        ax1.annotate(
            f"Current: {df.loc[last_point, '종가']:,.0f}",
            xy=(last_point, df.loc[last_point, '종가']),
            xytext=(15, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            bbox=bbox_props
        )

    # Y축 포맷터 설정
    max_price = df['고가'].max()
    formatter = select_number_formatter(max_price)
    ax1.yaxis.set_major_formatter(formatter)

    # 워터마크 추가
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    if save_path:
        # 저장 전 한글 폰트 다시 확인
        if KOREAN_FONT_PROP:
            # 중요 요소에 한글 폰트 재적용
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)
                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                # 범례에도 폰트 적용
                if ax.legend_ is not None:
                    for text in ax.legend_.get_texts():
                        text.set_fontproperties(KOREAN_FONT_PROP)

        # 해상도를 높이고 백엔드 명시
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        plt.tight_layout()
        return fig

def create_market_cap_chart(ticker, company_name=None, days=730, save_path=None):
    """
    시가총액 차트 생성

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼
    company_name : str, optional
        회사명 (제목용)
    days : int, optional
        조회 기간 (일)
    save_path : str, optional
        차트 저장 경로 (None이면 화면에 표시)

    Returns:
    --------
    fig : matplotlib figure
        차트가 포함된 figure 객체
    """
    # 날짜 범위 계산
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # 회사명이 없으면 가져오기
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except:
            company_name = ticker

    # 주식 데이터 가져오기
    df = get_market_cap_by_date(start_date, end_date, ticker)

    if df is None or len(df) == 0:
        print(f"{ticker}에 대한 시가총액 데이터가 없습니다.")
        return None

    # 인덱스가 datetime인지 확인
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # 날짜 오름차순 정렬
    df = df.sort_index()

    # 차트 생성
    fig, ax = plt.subplots(figsize=(12, 6))

    # 시가총액 플롯
    ax.fill_between(
        df.index,
        0,
        df['시가총액'],
        color=PRIMARY_COLORS[0],
        alpha=0.2
    )
    ax.plot(
        df.index,
        df['시가총액'],
        color=PRIMARY_COLORS[0],
        linewidth=2.5
    )

    # 제목 및 레이블 설정
    title = f"{company_name} ({ticker}) - 시가총액 추이"
    if KOREAN_FONT_PROP:
        ax.set_title(title, fontsize=16, fontweight='bold', pad=15, fontproperties=KOREAN_FONT_PROP)
        ax.set_ylabel('시가총액', fontsize=12, labelpad=10, fontproperties=KOREAN_FONT_PROP)
    else:
        ax.set_title(title, fontsize=16, fontweight='bold', pad=15)
        ax.set_ylabel('시가총액', fontsize=12, labelpad=10)

    ax.set_xlabel('', fontsize=12)

    # Y축 포맷터 설정
    max_cap = df['시가총액'].max()
    formatter = select_number_formatter(max_cap)
    ax.yaxis.set_major_formatter(formatter)

    # X축 날짜 포맷팅
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45)

    # 그리드 추가
    ax.grid(axis='y', linestyle='--', alpha=0.7)

    # 주요 포인트 주석 추가
    latest_point = df.iloc[-1]
    max_point_idx = df['시가총액'].idxmax()
    max_point = df.loc[max_point_idx]
    min_point_idx = df['시가총액'].idxmin()
    min_point = df.loc[min_point_idx]

    # 최신 포인트
    bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

    # 주석에 한글 폰트 적용
    if KOREAN_FONT_PROP:
        ax.annotate(
            f"{latest_point['시가총액']/1000000000000:.2f}조원",
            xy=(latest_point.name, latest_point['시가총액']),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=10,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )

        # 최고점
        if max_point_idx != df.index[-1]:
            ax.annotate(
                f"최고: {max_point['시가총액']/1000000000000:.2f}조원",
                xy=(max_point.name, max_point['시가총액']),
                xytext=(0, 15),
                textcoords='offset points',
                ha='center',
                va='bottom',
                fontsize=9,
                bbox=bbox_props,
                fontproperties=KOREAN_FONT_PROP
            )

        # 최저점
        if min_point_idx != df.index[0]:
            ax.annotate(
                f"최저: {min_point['시가총액']/1000000000000:.2f}조원",
                xy=(min_point.name, min_point['시가총액']),
                xytext=(0, -15),
                textcoords='offset points',
                ha='center',
                va='top',
                fontsize=9,
                bbox=bbox_props,
                fontproperties=KOREAN_FONT_PROP
            )
    else:
        # 영문 대체 버전 주석
        ax.annotate(
            f"{latest_point['시가총액']/1000000000000:.2f}T KRW",
            xy=(latest_point.name, latest_point['시가총액']),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=10,
            bbox=bbox_props
        )

    # YTD 또는 1년 변화율 계산 및 표시
    first_point = df.iloc[0]
    pct_change = (latest_point['시가총액'] - first_point['시가총액']) / first_point['시가총액'] * 100
    period = "YTD" if df.index[0].year == df.index[-1].year else "1년"
    change_text = f"{period} 변화율: {pct_change:.1f}%"

    # 기간 변화 텍스트 추가
    if KOREAN_FONT_PROP:
        ax.text(
            0.02, 0.95,
            change_text,
            transform=ax.transAxes,
            ha='left',
            va='top',
            fontsize=10,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        ax.text(
            0.02, 0.95,
            change_text,
            transform=ax.transAxes,
            ha='left',
            va='top',
            fontsize=10,
            bbox=bbox_props
        )

    # 워터마크 추가
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()

    if save_path:
        # 저장 전 한글 폰트 다시 확인
        if KOREAN_FONT_PROP:
            # 중요 요소에 한글 폰트 재적용
            for text in ax.texts:
                text.set_fontproperties(KOREAN_FONT_PROP)

            for label in ax.get_xticklabels() + ax.get_yticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)

            if ax.title is not None:
                ax.title.set_fontproperties(KOREAN_FONT_PROP)

            ax.yaxis.label.set_fontproperties(KOREAN_FONT_PROP)

        # 해상도를 높이고 백엔드 명시
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_fundamentals_chart(ticker, company_name=None, days=730, save_path=None):
    """
    기본 지표 차트 생성 (PER, PBR, 배당수익률)

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼
    company_name : str, optional
        회사명 (제목용)
    days : int, optional
        조회 기간 (일)
    save_path : str, optional
        차트 저장 경로 (None이면 화면에 표시)

    Returns:
    --------
    fig : matplotlib figure
        차트가 포함된 figure 객체
    """
    # 날짜 범위 계산
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # 회사명이 없으면 가져오기
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except:
            company_name = ticker

    # 주식 데이터 가져오기
    df = get_market_fundamental_by_date(start_date, end_date, ticker)

    if df is None or len(df) == 0:
        print(f"{ticker}에 대한 기본 지표 데이터가 없습니다.")
        return None

    # 인덱스가 datetime인지 확인
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)

    # 날짜 오름차순 정렬
    df = df.sort_index()

    # 서브플롯 생성
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)

    # X축 날짜 포맷팅
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))

    # 1. PER (주가수익비율) 플롯
    axes[0].plot(df.index, df['PER'], color=PRIMARY_COLORS[0], linewidth=2.5)

    # 제목 및 라벨 설정에 한글 폰트 적용
    if KOREAN_FONT_PROP:
        axes[0].set_title("주가수익비율 (PER)", fontsize=12, loc='left', fontproperties=KOREAN_FONT_PROP)
        axes[0].set_ylabel('PER', fontsize=11, fontproperties=KOREAN_FONT_PROP)
    else:
        axes[0].set_title("Price-to-Earnings Ratio (PER)", fontsize=12, loc='left')
        axes[0].set_ylabel('PER', fontsize=11)

    axes[0].grid(linestyle='--', alpha=0.7)

    # PER 주석 추가
    latest_per = df['PER'].iloc[-1]
    min_per_idx = df['PER'].idxmin()
    max_per_idx = df['PER'].idxmax()

    bbox_props = dict(boxstyle="round,pad=0.3", fc="#f8f9fa", ec="none", alpha=0.9)

    if KOREAN_FONT_PROP:
        axes[0].annotate(
            f"현재: {latest_per:.2f}",
            xy=(df.index[-1], latest_per),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=9,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        axes[0].annotate(
            f"Current: {latest_per:.2f}",
            xy=(df.index[-1], latest_per),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=9,
            bbox=bbox_props
        )

    # 업종 평균 비교 추가 (예시 - 실제 데이터는 API로 가져와야 함)
    avg_per = df['PER'].mean()  # 평균값 사용
    axes[0].axhline(y=avg_per, color='gray', linestyle='--', alpha=0.7)

    if KOREAN_FONT_PROP:
        axes[0].annotate(
            f"평균: {avg_per:.2f}",
            xy=(df.index[0], avg_per),
            xytext=(5, 5),
            textcoords='offset points',
            ha='left',
            va='bottom',
            fontsize=8,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        axes[0].annotate(
            f"Avg: {avg_per:.2f}",
            xy=(df.index[0], avg_per),
            xytext=(5, 5),
            textcoords='offset points',
            ha='left',
            va='bottom',
            fontsize=8,
            bbox=bbox_props
        )

    # 2. PBR (주가순자산비율) 플롯
    axes[1].plot(df.index, df['PBR'], color=PRIMARY_COLORS[1], linewidth=2.5)

    if KOREAN_FONT_PROP:
        axes[1].set_title("주가순자산비율 (PBR)", fontsize=12, loc='left', fontproperties=KOREAN_FONT_PROP)
        axes[1].set_ylabel('PBR', fontsize=11, fontproperties=KOREAN_FONT_PROP)
    else:
        axes[1].set_title("Price-to-Book Ratio (PBR)", fontsize=12, loc='left')
        axes[1].set_ylabel('PBR', fontsize=11)

    axes[1].grid(linestyle='--', alpha=0.7)

    # PBR 주석 추가
    latest_pbr = df['PBR'].iloc[-1]

    if KOREAN_FONT_PROP:
        axes[1].annotate(
            f"현재: {latest_pbr:.2f}",
            xy=(df.index[-1], latest_pbr),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=9,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        axes[1].annotate(
            f"Current: {latest_pbr:.2f}",
            xy=(df.index[-1], latest_pbr),
            xytext=(10, 0),
            textcoords='offset points',
            ha='left',
            va='center',
            fontsize=9,
            bbox=bbox_props
        )

    # 업종 평균 비교 추가
    avg_pbr = df['PBR'].mean()  # 평균값 사용
    axes[1].axhline(y=avg_pbr, color='gray', linestyle='--', alpha=0.7)

    if KOREAN_FONT_PROP:
        axes[1].annotate(
            f"평균: {avg_pbr:.2f}",
            xy=(df.index[0], avg_pbr),
            xytext=(5, 5),
            textcoords='offset points',
            ha='left',
            va='bottom',
            fontsize=8,
            bbox=bbox_props,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        axes[1].annotate(
            f"Avg: {avg_pbr:.2f}",
            xy=(df.index[0], avg_pbr),
            xytext=(5, 5),
            textcoords='offset points',
            ha='left',
            va='bottom',
            fontsize=8,
            bbox=bbox_props
        )

    # 3. 배당수익률 (DIV) 플롯
    if 'DIV' in df.columns:
        axes[2].plot(df.index, df['DIV'], color=PRIMARY_COLORS[2], linewidth=2.5)

        if KOREAN_FONT_PROP:
            axes[2].set_title("배당수익률 (%)", fontsize=12, loc='left', fontproperties=KOREAN_FONT_PROP)
            axes[2].set_ylabel('수익률 (%)', fontsize=11, fontproperties=KOREAN_FONT_PROP)
        else:
            axes[2].set_title("Dividend Yield (%)", fontsize=12, loc='left')
            axes[2].set_ylabel('Yield (%)', fontsize=11)

        axes[2].grid(linestyle='--', alpha=0.7)

        # 배당 주석 추가
        latest_div = df['DIV'].iloc[-1]

        if KOREAN_FONT_PROP:
            axes[2].annotate(
                f"현재: {latest_div:.2f}%",
                xy=(df.index[-1], latest_div),
                xytext=(10, 0),
                textcoords='offset points',
                ha='left',
                va='center',
                fontsize=9,
                bbox=bbox_props,
                fontproperties=KOREAN_FONT_PROP
            )
        else:
            axes[2].annotate(
                f"Current: {latest_div:.2f}%",
                xy=(df.index[-1], latest_div),
                xytext=(10, 0),
                textcoords='offset points',
                ha='left',
                va='center',
                fontsize=9,
                bbox=bbox_props
            )

        # 업종 평균 비교 추가
        avg_div = df['DIV'].mean()  # 평균값 사용
        axes[2].axhline(y=avg_div, color='gray', linestyle='--', alpha=0.7)

        if KOREAN_FONT_PROP:
            axes[2].annotate(
                f"평균: {avg_div:.2f}%",
                xy=(df.index[0], avg_div),
                xytext=(5, 5),
                textcoords='offset points',
                ha='left',
                va='bottom',
                fontsize=8,
                bbox=bbox_props,
                fontproperties=KOREAN_FONT_PROP
            )
        else:
            axes[2].annotate(
                f"Avg: {avg_div:.2f}%",
                xy=(df.index[0], avg_div),
                xytext=(5, 5),
                textcoords='offset points',
                ha='left',
                va='bottom',
                fontsize=8,
                bbox=bbox_props
            )

    # 전체 제목
    if KOREAN_FONT_PROP:
        fig.suptitle(
            f"{company_name} ({ticker}) - 기본 지표 분석",
            fontsize=16,
            fontweight='bold',
            y=0.98,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        fig.suptitle(
            f"{company_name} ({ticker}) - Fundamental Analysis",
            fontsize=16,
            fontweight='bold',
            y=0.98
        )

    # 레이블 폰트 설정 강화
    for ax in axes:
        ax.tick_params(labelsize=9)  # 축 레이블 폰트 크기

        # 한글 폰트 설정
        if KOREAN_FONT_PROP:
            for label in ax.get_yticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)
            for label in ax.get_xticklabels():
                label.set_fontproperties(KOREAN_FONT_PROP)

    # X축 날짜 표시 형식
    plt.xticks(rotation=45)

    # 워터마크 추가
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()
    plt.subplots_adjust(top=0.93)

    if save_path:
        # 저장 전 한글 폰트 명시적 재적용
        if KOREAN_FONT_PROP:
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)

                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'yaxis') and hasattr(ax.yaxis, 'label'):
                    ax.yaxis.label.set_fontproperties(KOREAN_FONT_PROP)

            if hasattr(fig, '_suptitle') and fig._suptitle is not None:
                fig._suptitle.set_fontproperties(KOREAN_FONT_PROP)

        # 해상도를 높이고 백엔드 명시
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_trading_volume_chart(ticker, company_name=None, days=730, save_path=None):
    """
    투자자별 거래량 차트 생성

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼
    company_name : str, optional
        회사명 (제목용)
    days : int, optional
        조회 기간 (일)
    save_path : str, optional
        차트 저장 경로 (None이면 화면에 표시)

    Returns:
    --------
    fig : matplotlib figure
        차트가 포함된 figure 객체
    """
    # 날짜 범위 계산
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')

    # 회사명이 없으면 가져오기
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except:
            company_name = ticker

    # 주식 데이터 가져오기 - 투자자별 거래량
    df_volume = get_market_trading_volume_by_investor(start_date, end_date, ticker)

    if df_volume is None or len(df_volume) == 0:
        print(f"{ticker}에 대한 거래량 데이터가 없습니다.")
        return None

    # 일별 순매수 데이터 가져오기
    df_daily = get_market_trading_volume_by_date(start_date, end_date, ticker)

    if df_daily is None or len(df_daily) == 0:
        print(f"{ticker}에 대한 일별 거래량 데이터가 없습니다.")
        return None

    # 차트 생성
    fig, axes = plt.subplots(2, 1, figsize=(12, 10), gridspec_kw={'height_ratios': [2, 3]})

    # 1. 주요 투자자 누적 순매수량
    # 주요 투자자 선택
    investor_types = ['기관합계', '외국인합계', '개인', '기타법인']

    # 순매수량 컬럼 선택
    if '순매수' in df_volume.columns:
        investor_data = df_volume['순매수']

        # 차트 그리기
        bar_width = 0.6
        pos = np.arange(len(investor_data))

        bars = axes[0].bar(
            pos,
            investor_data,
            bar_width,
            color=[PRIMARY_COLORS[i % len(PRIMARY_COLORS)] for i in range(len(investor_data))],
            alpha=0.7
        )

        # 라벨 및 제목 설정
        if KOREAN_FONT_PROP:
            axes[0].set_title("투자자별 순매수량", fontsize=12, loc='left', fontproperties=KOREAN_FONT_PROP)
        else:
            axes[0].set_title("Net Purchase by Investor Type", fontsize=12, loc='left')

        axes[0].set_xticks(pos)

        # X축 라벨에 한글 폰트 적용
        if KOREAN_FONT_PROP:
            axes[0].set_xticklabels(investor_data.index, rotation=45, fontproperties=KOREAN_FONT_PROP)
        else:
            axes[0].set_xticklabels(investor_data.index, rotation=45)

        axes[0].axhline(y=0, color='black', linestyle='-', alpha=0.3)
        axes[0].grid(axis='y', linestyle='--', alpha=0.7)

        # 숫자 포맷팅
        max_vol = investor_data.max()
        min_vol = investor_data.min()
        formatter = select_number_formatter(max(abs(max_vol), abs(min_vol)))
        axes[0].yaxis.set_major_formatter(formatter)

        # 데이터 레이블 추가
        for bar in bars:
            height = bar.get_height()
            value = int(height)
            va = 'bottom' if height >= 0 else 'top'
            y_pos = 0.3 if height >= 0 else -0.3

            # 한글 폰트 설정
            if KOREAN_FONT_PROP:
                axes[0].text(
                    bar.get_x() + bar.get_width()/2.,
                    height + (height * 0.02 if height >= 0 else height * 0.02),
                    f'{value:,}',
                    ha='center',
                    va=va,
                    fontsize=9,
                    rotation=0,
                    fontproperties=KOREAN_FONT_PROP
                )
            else:
                axes[0].text(
                    bar.get_x() + bar.get_width()/2.,
                    height + (height * 0.02 if height >= 0 else height * 0.02),
                    f'{value:,}',
                    ha='center',
                    va=va,
                    fontsize=9,
                    rotation=0
                )

    # 2. 일별 순매수량 추이
    # 인덱스가 datetime인지 확인
    if not isinstance(df_daily.index, pd.DatetimeIndex):
        df_daily.index = pd.to_datetime(df_daily.index)

    # 날짜 오름차순 정렬
    df_daily = df_daily.sort_index()

    # 주요 투자자만 선택
    key_investors = [col for col in df_daily.columns if col in investor_types]

    # 누적 순매수량 계산
    df_cumulative = df_daily[key_investors].cumsum()

    # 차트 그리기
    for i, investor in enumerate(key_investors):
        axes[1].plot(
            df_cumulative.index,
            df_cumulative[investor],
            color=PRIMARY_COLORS[i % len(PRIMARY_COLORS)],
            linewidth=2,
            label=investor
        )

    # 라벨 및 제목 설정
    if KOREAN_FONT_PROP:
        axes[1].set_title("일별 누적 순매수량 추이", fontsize=12, loc='left', fontproperties=KOREAN_FONT_PROP)
    else:
        axes[1].set_title("Daily Cumulative Net Purchase Trend", fontsize=12, loc='left')

    axes[1].set_xlabel('')
    axes[1].axhline(y=0, color='black', linestyle='-', alpha=0.3)
    axes[1].grid(linestyle='--', alpha=0.7)

    # 한글 폰트를 범례에 적용
    if KOREAN_FONT_PROP:
        legend = axes[1].legend(loc='upper left', prop=KOREAN_FONT_PROP)
    else:
        legend = axes[1].legend(loc='upper left')

    # 숫자 포맷팅
    max_vol = df_cumulative.max().max()
    min_vol = df_cumulative.min().min()
    formatter = select_number_formatter(max(abs(max_vol), abs(min_vol)))
    axes[1].yaxis.set_major_formatter(formatter)

    # X축 날짜 포맷팅
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    axes[1].xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
    plt.setp(axes[1].xaxis.get_majorticklabels(), rotation=45, ha='right')

    # 전체 제목
    if KOREAN_FONT_PROP:
        fig.suptitle(
            f"{company_name} ({ticker}) - 투자자별 거래 동향",
            fontsize=16,
            fontweight='bold',
            y=0.98,
            fontproperties=KOREAN_FONT_PROP
        )
    else:
        fig.suptitle(
            f"{company_name} ({ticker}) - Trading by Investor Type",
            fontsize=16,
            fontweight='bold',
            y=0.98
        )

    # 워터마크 추가
    fig.text(
        0.99, 0.01,
        "AI Stock Analysis",
        ha='right', va='bottom',
        color='#cccccc',
        fontsize=8
    )

    plt.tight_layout()
    plt.subplots_adjust(top=0.93)

    if save_path:
        # 저장 전 한글 폰트 명시적 재적용
        if KOREAN_FONT_PROP:
            for ax in fig.axes:
                for text in ax.texts:
                    text.set_fontproperties(KOREAN_FONT_PROP)

                for label in ax.get_xticklabels() + ax.get_yticklabels():
                    label.set_fontproperties(KOREAN_FONT_PROP)

                if hasattr(ax, 'title') and ax.title is not None:
                    ax.title.set_fontproperties(KOREAN_FONT_PROP)

                # 범례 텍스트에도 폰트 적용
                if ax.legend_ is not None:
                    for text in ax.legend_.get_texts():
                        text.set_fontproperties(KOREAN_FONT_PROP)

            if hasattr(fig, '_suptitle') and fig._suptitle is not None:
                fig._suptitle.set_fontproperties(KOREAN_FONT_PROP)

        # 해상도를 높이고 백엔드 명시
        plt.savefig(save_path, dpi=300, bbox_inches='tight', backend='agg')
        plt.close()
        return save_path
    else:
        return fig

def create_comprehensive_report(ticker, company_name=None, days=730, output_dir='charts'):
    """
    여러 차트가 포함된 종합 주식 분석 보고서 생성

    Parameters:
    -----------
    ticker : str
        주식 티커 심볼
    company_name : str, optional
        회사명 (제목용)
    days : int, optional
        조회 기간 (일)
    output_dir : str, optional
        차트 저장 디렉토리

    Returns:
    --------
    report_paths : dict
        생성된 차트 이미지 경로 딕셔너리
    """
    # 회사명이 없으면 가져오기
    if company_name is None:
        try:
            company_name = get_market_ticker_name(ticker)
        except:
            company_name = ticker

    # 출력 디렉토리 생성 (없는 경우)
    os.makedirs(output_dir, exist_ok=True)

    # 보고서 ID 생성
    report_id = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 보고서 하위 디렉토리 생성
    report_dir = os.path.join(output_dir, f"{ticker}_{report_id}")
    os.makedirs(report_dir, exist_ok=True)

    report_paths = {}

    # 가격 차트 생성
    price_path = os.path.join(report_dir, f"{ticker}_price.png")
    try:
        create_price_chart(ticker, company_name, days, save_path=price_path)
        report_paths['price_chart'] = price_path
    except Exception as e:
        print(f"가격 차트 생성 오류: {e}")

    # 시가총액 차트 생성
    marketcap_path = os.path.join(report_dir, f"{ticker}_marketcap.png")
    try:
        create_market_cap_chart(ticker, company_name, days, save_path=marketcap_path)
        report_paths['market_cap_chart'] = marketcap_path
    except Exception as e:
        print(f"시가총액 차트 생성 오류: {e}")

    # 기본 지표 차트 생성
    fundamentals_path = os.path.join(report_dir, f"{ticker}_fundamentals.png")
    try:
        create_fundamentals_chart(ticker, company_name, days, save_path=fundamentals_path)
        report_paths['fundamentals_chart'] = fundamentals_path
    except Exception as e:
        print(f"기본 지표 차트 생성 오류: {e}")

    # 거래량 차트 생성 (더 짧은 기간 사용)
    volume_path = os.path.join(report_dir, f"{ticker}_volume.png")
    try:
        create_trading_volume_chart(ticker, company_name, min(60, days), save_path=volume_path)
        report_paths['trading_volume_chart'] = volume_path
    except Exception as e:
        print(f"거래량 차트 생성 오류: {e}")

    return report_paths

def check_font_available():
    """한글 폰트 사용 가능 여부 확인 및 안내"""
    # 사용 가능한 폰트 출력
    available_fonts = [f.name for f in fm.fontManager.ttflist]

    # 추정되는 한글 폰트 이름 목록
    possible_korean_fonts = [
        f for f in available_fonts if any(keyword in f for keyword in
                                          ['Gothic', 'Hangul', '고딕', 'Apple', 'Nanum', '나눔', 'Malgun', 'SD', 'Gulim', '굴림', 'Batang', '바탕', 'Dotum', '돋움'])
    ]

    # 한글 문자가 있는지 검사
    korean_fonts = [f for f in available_fonts if any(char in f for char in '가나다라마바사아자차카타파하')]

    # 두 리스트 합치기 (중복 제거)
    all_korean_fonts = list(set(possible_korean_fonts + korean_fonts))

    if not all_korean_fonts:
        print("⚠️ 경고: 시스템에 한글 폰트가 없습니다!")
        print("다음 방법으로 한글 폰트를 설치해주세요:")

        if platform.system() == 'Windows':
            print("Windows는 기본적으로 '맑은 고딕' 폰트가 설치되어 있어야 합니다.")
        elif platform.system() == 'Darwin':  # macOS
            print("macOS에서 다음 한글 폰트를 설치해보세요:")
            print("- 나눔고딕 (https://hangeul.naver.com/font)")
            print("- 또는 macOS에 기본 내장된 'Apple SD Gothic Neo'를 사용해보세요.")
        else:  # Linux
            if os.path.exists('/etc/rocky-release'):  # Rocky Linux인 경우
                print("Rocky Linux 8 한글 폰트 설치 방법:")
                print("sudo dnf install google-nanum-fonts")
            else:  # 기타 Linux
                print("Linux: 나눔고딕 폰트 설치 방법")
                print("Ubuntu/Debian: sudo apt-get install fonts-nanum")
                print("CentOS/RHEL: sudo dnf install google-nanum-fonts")
                print("또는 나눔고딕 폰트를 다운로드하여 ~/.fonts 폴더에 복사")

        print("\n폰트 설치 후 다음 명령어로 matplotlib 폰트 캐시를 갱신하세요:")
        print("import matplotlib.font_manager as fm")
        print("fm.fontManager.rebuild()  # 최신 버전의 matplotlib")
        print("# 또는 fm._rebuild()  # 구버전의 matplotlib")
    else:
        print(f"사용 가능한 한글 폰트 {len(all_korean_fonts)}개 발견:")
        for i, font in enumerate(all_korean_fonts[:10]):  # 최대 10개만 표시
            print(f"  {i+1}. {font}")
        if len(all_korean_fonts) > 10:
            print(f"  ... 외 {len(all_korean_fonts)-10}개")

    return all_korean_fonts

def main():
    """
    주식 차트 생성 함수 사용 예시
    """
    # 한글 폰트 확인
    korean_fonts = check_font_available()

    # 예시: 삼성전자 차트 생성
    ticker = "005930"  # 삼성전자
    company_name = "삼성전자"

    # 종합 보고서 생성
    report_paths = create_comprehensive_report(ticker, company_name)

    print(f"다음 차트가 포함된 보고서가 생성되었습니다:")
    for chart_type, path in report_paths.items():
        print(f"- {chart_type}: {path}")

    # 개별 차트 예시
    # 주석 해제하여 개별 차트 생성 및 표시

    # 가격 차트
    #fig_price = create_price_chart(ticker, company_name)
    #plt.figure(fig_price.number)
    #plt.show()

    # 시가총액 차트
    #fig_mc = create_market_cap_chart(ticker, company_name)
    #plt.figure(fig_mc.number)
    #plt.show()

    # 기본 지표 차트
    #fig_fund = create_fundamentals_chart(ticker, company_name)
    #plt.figure(fig_fund.number)
    #plt.show()

    # 거래량 차트
    #fig_vol = create_trading_volume_chart(ticker, company_name, days=60)
    #plt.figure(fig_vol.number)
    #plt.show()

if __name__ == "__main__":
    main()