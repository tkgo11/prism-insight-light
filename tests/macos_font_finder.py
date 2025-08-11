"""
매우 단순한 한글 폰트 테스트 및 해결책
"""

import matplotlib.pyplot as plt
import matplotlib
import os
import platform

def test_with_font_path():
    """폰트 파일 경로를 직접 지정하는 방법"""
    print("시스템:", platform.system())
    print("Matplotlib 버전:", matplotlib.__version__)

    # 백엔드 설정
    matplotlib.use('Agg')  # 비대화형 백엔드

    # 기본 설정 초기화
    matplotlib.rcParams.update(matplotlib.rcParamsDefault)

    # axes.unicode_minus 설정
    matplotlib.rcParams['axes.unicode_minus'] = False

    # 몇 가지 가능한 한글 폰트 경로들
    possible_font_paths = [
        # macOS 한글 폰트
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/Library/Fonts/NanumGothic.ttf',
        os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),

        # Windows 한글 폰트
        'C:/Windows/Fonts/malgun.ttf',

        # Linux 한글 폰트
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/usr/share/fonts/nanum/NanumGothic.ttf'
    ]

    # 존재하는 폰트 파일 찾기
    font_found = False
    used_font_path = None

    for font_path in possible_font_paths:
        if os.path.exists(font_path):
            print(f"폰트 파일 발견: {font_path}")
            used_font_path = font_path
            font_found = True
            break

    if not font_found:
        print("시스템에서 한글 폰트 파일을 찾을 수 없습니다!")
        return

    # 방법 1: FontProperties 직접 생성하여 각 텍스트 요소에 적용
    from matplotlib.font_manager import FontProperties

    font_prop = FontProperties(fname=used_font_path)

    plt.figure(figsize=(10, 4))
    plt.suptitle(f"폰트 파일: {os.path.basename(used_font_path)}", fontsize=15)

    plt.subplot(121)
    plt.title("FontProperties 직접 적용", fontproperties=font_prop)
    plt.text(0.5, 0.5, "안녕하세요! 한글 테스트",
             ha='center', va='center', fontsize=14, fontproperties=font_prop)
    plt.axis('off')

    # 방법 2: rc 파라미터 설정
    plt.subplot(122)

    # FontProperties에서 폰트 이름 얻기
    font_name = font_prop.get_name()

    # RC 파라미터 설정
    plt.rcParams['font.family'] = font_name

    plt.title("rcParams 설정 방식")
    plt.text(0.5, 0.5, "안녕하세요! 한글 테스트",
             ha='center', va='center', fontsize=14)
    plt.axis('off')

    # 파일 저장
    plt.tight_layout()
    plt.savefig('korean_test_result.png', dpi=100)
    plt.close()

    print(f"테스트 이미지가 저장되었습니다: {os.path.abspath('korean_test_result.png')}")
    print("이미지를 열어 한글이 제대로 표시되는지 확인하세요.")

if __name__ == "__main__":
    test_with_font_path()