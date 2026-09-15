"""日文零件图纸术语自动翻译系统 —— 启动入口。

读 config.yaml，把相对路径补成绝对路径，交给主窗口。

    python main.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CONFIG_PATH = ROOT / 'config.yaml'

# 这些键的值是路径，要按项目根目录展开，否则从别处启动就找不到文件
PATH_KEYS = {
    ('translator', 'dictionary_path'),
    ('logging', 'log_dir'),
    ('output', 'default_dir'),
}


def load_config() -> dict:
    try:
        import yaml
    except ImportError:
        sys.exit("缺少 PyYAML，先装依赖：pip install -r requirements.txt")

    if not CONFIG_PATH.exists():
        sys.exit(f"找不到配置文件：{CONFIG_PATH}")

    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}

    for section, key in PATH_KEYS:
        if config.get(section, {}).get(key):
            config[section][key] = str(ROOT / config[section][key])

    return config


def check_dependencies() -> bool:
    """缺依赖时给出安装命令，而不是等 import 到一半崩掉。"""
    checks = [
        ('paddleocr', 'paddleocr paddlepaddle'),
        ('PyQt5', 'PyQt5'),
        ('cv2', 'opencv-python'),
        ('PIL', 'Pillow'),
        ('pdf2image', 'pdf2image'),
        ('fuzzywuzzy', 'fuzzywuzzy python-Levenshtein'),
    ]

    missing = []
    for module, package in checks:
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if not missing:
        return True

    print("缺少以下依赖：")
    for package in missing:
        print(f"  pip install {package}")
    print(f"\n或者一次装齐：pip install -r {ROOT / 'requirements.txt'}")
    return False


def main():
    print("日文零件图纸术语自动翻译系统")

    if not check_dependencies():
        input("按 Enter 退出…")
        return 1

    config = load_config()

    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("日文零件图纸术语自动翻译系统")
    app.setFont(QFont("Microsoft YaHei", 9))

    from src.ui.main_window import MainWindow

    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
