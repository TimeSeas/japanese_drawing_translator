"""
日文零件图纸术语自动翻译应用 - 入口模块
===================================
Japanese Mechanical Drawing Terminology Auto-Translator

功能概述:
  识别机械图纸（PDF/JPG/PNG/BMP）中的日文术语，自动翻译为中文，
  并在图纸上以红色字体标注翻译结果。

启动方式:
  python main.py

环境要求:
  Python 3.8+, Windows 10/11
  依赖: pip install -r requirements.txt

项目结构:
  ├── main.py              # 本文件 - 应用入口
  ├── dictionary.json      # 日→中术语词典
  ├── config.yaml          # 配置参数
  ├── requirements.txt     # 依赖清单
  ├── src/                 # 源代码
  │   ├── file_parser.py   # 文件解析
  │   ├── image_processor.py  # 图像预处理
  │   ├── ocr_engine.py    # PaddleOCR 封装
  │   ├── translator.py    # 术语词典翻译
  │   ├── overlay_renderer.py  # 红色标注叠加
  │   ├── data_store.py    # 存储与日志
  │   └── ui/              # 用户界面
  │       ├── main_window.py   # 主窗口
  │       ├── image_viewer.py  # 图纸预览控件
  │       ├── result_panel.py  # 结果展示面板
  │       └── dict_editor.py   # 词典编辑器
  ├── logs/                # 日志目录
  └── output/              # 输出目录
"""

import sys
import os
from pathlib import Path

# ---- 确保 src 在 Python 路径中 ----
PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / 'src'
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SRC_DIR))


def check_dependencies():
    """
    检查关键依赖是否已安装。

    若缺少依赖，打印安装指引并返回 False。
    """
    missing = []

    # 检查 PaddleOCR
    try:
        import paddleocr
    except ImportError:
        missing.append('paddleocr (pip install paddleocr paddlepaddle)')

    # 检查 PyQt5
    try:
        import PyQt5
    except ImportError:
        missing.append('PyQt5 (pip install PyQt5)')

    # 检查 OpenCV
    try:
        import cv2
    except ImportError:
        missing.append('opencv-python (pip install opencv-python)')

    # 检查 Pillow
    try:
        import PIL
    except ImportError:
        missing.append('Pillow (pip install Pillow)')

    # 检查 pdf2image
    try:
        import pdf2image
    except ImportError:
        missing.append('pdf2image (pip install pdf2image)')

    # 检查 FuzzyWuzzy
    try:
        import fuzzywuzzy
    except ImportError:
        missing.append('fuzzywuzzy (pip install fuzzywuzzy python-Levenshtein)')

    if missing:
        print("=" * 60)
        print("缺少以下依赖包，请先安装:")
        for m in missing:
            print(f"  - {m}")
        print()
        print("或一次性安装全部依赖:")
        print(f"  pip install -r {PROJECT_ROOT / 'requirements.txt'}")
        print("=" * 60)
        return False

    return True


def setup_environment():
    """
    设置运行环境。

    创建必要的目录（logs/, output/），配置日志系统。
    """
    # 创建必要目录
    for dir_name in ['logs', 'output']:
        dir_path = PROJECT_ROOT / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)


def main():
    """
    应用主入口。

    流程:
      1. 检查依赖
      2. 设置环境
      3. 初始化日志系统
      4. 启动 Qt 应用和主窗口
    """
    print("日文零件图纸术语自动翻译系统")
    print("Japanese Mechanical Drawing Terminology Auto-Translator")
    print("=" * 50)

    # ---- 依赖检查 ----
    if not check_dependencies():
        input("按 Enter 键退出...")
        sys.exit(1)

    # ---- 环境设置 ----
    setup_environment()

    # ---- 启动 Qt 应用 ----
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFont
    from PyQt5.QtCore import Qt as QtCore_Qt

    # 高 DPI 支持 (PyQt5: 通过 AA_EnableHighDpiScaling 属性)

    app = QApplication(sys.argv)
    app.setApplicationName("日文零件图纸术语自动翻译系统")
    app.setOrganizationName("DrawingTranslator")

    # 设置默认字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # ---- 初始化日志系统 ----
    from src.data_store import get_store
    store = get_store()
    store.setup_logging()

    # ---- 加载配置 ----
    config = {}
    config_path = PROJECT_ROOT / 'config.yaml'
    if config_path.exists():
        try:
            import yaml
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            pass  # 配置加载失败不影响启动

    # ---- 初始化翻译器并加载词典 ----
    from src.translator import get_translator
    translator = get_translator()
    dict_path = PROJECT_ROOT / 'dictionary.json'
    if dict_path.exists():
        try:
            count = translator.load_dictionary(str(dict_path))
            print(f"术语词典已加载: {count} 条")
        except Exception as e:
            print(f"警告: 词典加载失败 - {e}")
    else:
        print("警告: 未找到术语词典文件 dictionary.json")

    # ---- 创建并显示主窗口 ----
    from src.ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    print("应用已启动。")
    print("=" * 50)

    # ---- 进入事件循环 ----
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
