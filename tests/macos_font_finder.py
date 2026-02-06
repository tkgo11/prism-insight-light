"""
Very simple Korean font test and solution
"""

import matplotlib.pyplot as plt
import matplotlib
import os
import platform

def test_with_font_path():
    """Method to directly specify font file path"""
    print("System:", platform.system())
    print("Matplotlib version:", matplotlib.__version__)

    # Backend configuration
    matplotlib.use('Agg')  # Non-interactive backend

    # Initialize default settings
    matplotlib.rcParams.update(matplotlib.rcParamsDefault)

    # Set axes.unicode_minus
    matplotlib.rcParams['axes.unicode_minus'] = False

    # Possible Korean font paths
    possible_font_paths = [
        # macOS Korean fonts
        '/System/Library/Fonts/AppleSDGothicNeo.ttc',
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/Library/Fonts/NanumGothic.ttf',
        os.path.expanduser('~/Library/Fonts/NanumGothic.ttf'),

        # Windows Korean fonts
        'C:/Windows/Fonts/malgun.ttf',

        # Linux Korean fonts
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/usr/share/fonts/nanum/NanumGothic.ttf'
    ]

    # Find existing font file
    font_found = False
    used_font_path = None

    for font_path in possible_font_paths:
        if os.path.exists(font_path):
            print(f"Font file found: {font_path}")
            used_font_path = font_path
            font_found = True
            break

    if not font_found:
        print("Cannot find Korean font file on system!")
        return

    # Method 1: Create FontProperties directly and apply to each text element
    from matplotlib.font_manager import FontProperties

    font_prop = FontProperties(fname=used_font_path)

    plt.figure(figsize=(10, 4))
    plt.suptitle(f"Font file: {os.path.basename(used_font_path)}", fontsize=15)

    plt.subplot(121)
    plt.title("Direct FontProperties application", fontproperties=font_prop)
    plt.text(0.5, 0.5, "Hello! Korean test",
             ha='center', va='center', fontsize=14, fontproperties=font_prop)
    plt.axis('off')

    # Method 2: rc parameter configuration
    plt.subplot(122)

    # Get font name from FontProperties
    font_name = font_prop.get_name()

    # Set RC parameters
    plt.rcParams['font.family'] = font_name

    plt.title("rcParams configuration method")
    plt.text(0.5, 0.5, "Hello! Korean test",
             ha='center', va='center', fontsize=14)
    plt.axis('off')

    # Save file
    plt.tight_layout()
    plt.savefig('korean_test_result.png', dpi=100)
    plt.close()

    print(f"Test image saved: {os.path.abspath('korean_test_result.png')}")
    print("Open the image to check if Korean is displayed correctly.")

if __name__ == "__main__":
    test_with_font_path()