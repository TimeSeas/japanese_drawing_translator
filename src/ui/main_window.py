"""主窗口：把各模块串起来。

处理链路是 解析文件 → 预处理 → OCR → 查词典翻译 → 叠加渲染，整条链
跑一张 A4 图纸要几秒，卡在 UI 线程里界面会假死，所以丢进 QThread。
"""

import json
import sys
import time
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSplitter, QStatusBar,
    QToolBar, QVBoxLayout, QWidget,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.data_store import DataStore
from src.file_parser import SUPPORTED_EXTENSIONS, get_file_info, parse_file
from src.image_processor import preprocess
from src.ocr_engine import OCREngine, is_term_text
from src.overlay_renderer import OverlayRenderer
from src.translator import Translator
from src.ui.dict_editor import DictEditorDialog
from src.ui.image_viewer import ImageViewer
from src.ui.result_panel import ResultPanel

TRANSLATED_METHODS = ('exact', 'fuzzy')


def _translate_one(src: dict, translator: Translator) -> dict:
    """查一次词典，拼成一条结果。

    字段是**白名单**式的：只从这里列出来的键进结果，所以 recognize() 挂在
    条目上的 `parts` 不会跟着流到界面和导出的 JSON 里。
    """
    translated, method, conf, suspect = translator.translate(src['text'])
    item = {
        'text': src['text'],
        'original': src['text'],
        'translated': translated,
        'match_method': method,
        'confidence': conf,
        # confidence 进来时是 OCR 的识别置信度，这里改名留住再写匹配得分。
        # 两个分数含义不同：一个说"这行日文认没认对"，一个说"词典匹配得
        # 准不准"。直接覆盖的话，就分不出某条未匹配到底是 OCR 认错了字，
        # 还是词典里压根没这个词。
        'ocr_confidence': src['confidence'],
        'bbox': dict(src['bbox']),
        # 横排 'h' / 竖排 'v' / 斜排 'rot'（见 ocr_engine._orientation）。
        # 只是标出来，不参与翻译 —— 竖排和斜排目前认不出，标出来是为了
        # 让它们别再悄无声息地混在横排里。
        'orientation': src.get('orientation', 'h'),
    }
    # 差一点没够阈值的候选（见 translator._SUSPECT_MARGIN）。
    # 只是个提示，不改变 match_method，也不参与统计。
    if suspect:
        item['suspect'] = suspect
    return item


def _translate_item(item: dict, translator: Translator) -> list:
    """一条识别结果 → 一条或多条翻译结果。

    多数情况就是一条。例外只有一种：这条是**接龙拼起来的**（OCR 把被表格线、
    引出线切开的一句话接回了整句，见 ocr_engine._merge_same_line），拼完整句
    反而查不到词典。这时候拼接不但没帮忙，还把本来能翻的碎片一起拖下水。

    实测就有一例：整句「4Eł牝加工部の寸法形状はđ×ｆデ-`-参照のこと」是四段
    拼的，整句查不到；但最后一段「参照のこと」单独拿出来是词典里的精确词条
    （→ 请参照）。不拆的话它跟着整句一起变成"未翻译"。

    拆开之后每条碎片带着**自己原来的 bbox** 画回原位，不是整句的并集框 ——
    所以这是"还原"，不是"在整句上补一条"。
    """
    result = _translate_one(item, translator)

    parts = item.get('parts') or []
    if result['match_method'] != 'unmatched' or len(parts) <= 1:
        return [result]

    # 拆的时候要再筛一遍"非术语"：`-`、`` `- `` 这种碎片本来是夹在整句里被
    # recognize() 的过滤放行的，单独成条只会多出几行 [未翻译] 的噪声。
    pieces = [_translate_one(p, translator) for p in parts
              if is_term_text(p['text'])]

    # 只有**精确**命中才值得拆，模糊命中不算。这条线是量出来的，不是随手
    # 保守：模糊碎片会两头亏 —— 整句那条连同它自己的"疑似"一起消失（而整句
    # 的疑似往往比碎片更准，因为它更接近词典里的整句词条），碎片自己还会
    # 掉进另一个坑：候选长度门槛只管"词条不能比文本短太多"，**不管词条比
    # 文本长多少**，于是 partial_ratio 认为"短串是长串子串"依旧给 100。
    #
    # 实测（拆「9公差はＪＩＳ / Ｂ0405 / 中級による」那条）：5 个字的
    # 「中級による」恰好是整句词条的子串，拿到 100 分，把整句的译文
    # 『⑨公差按JIS B 0405中级。』原样贴进了它自己那个小框里。拆之前这条是
    # "未翻译 + 疑似（而且疑似是对的）"，拆之后成了"模糊命中、100 分"，
    # 看着更确定，实际框错了位置 —— 比不拆更糟。
    if not any(p['match_method'] == 'exact' for p in pieces):
        return [result]

    return pieces

FILE_FILTER = (
    "支持的图纸文件 (*.pdf *.jpg *.jpeg *.png *.bmp);;"
    "PDF 文件 (*.pdf);;"
    "图像文件 (*.jpg *.jpeg *.png *.bmp);;"
    "所有文件 (*.*)"
)


class ProcessThread(QThread):
    """跑一次的识别+翻译任务。"""
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, file_path, cover_mode, cfg):
        super().__init__()
        self.file_path = file_path
        self.cover_mode = cover_mode
        self.cfg = cfg

    def run(self):
        try:
            self.done.emit(self._process())
        except Exception as e:
            self.failed.emit(str(e))

    def _process(self):
        started = time.time()
        images, _ = parse_file(self.file_path)

        ocr = OCREngine(**self.cfg['ocr'])
        translator = Translator(self.cfg['translator']['fuzzy_threshold'],
                                self.cfg['translator']['kanji_traps'])
        translator.load_dictionary(self.cfg['translator']['dictionary_path'])
        renderer = OverlayRenderer(**self.cfg['overlay'])

        pages = []
        for page_no, image in enumerate(images, start=1):
            results = ocr.recognize(preprocess(image, self.cfg['preprocessing']))

            texts = []
            for item in results:
                texts.extend(_translate_item(item, translator))

            # 拆碎片会让条目数变多，recognize() 里编好的号就对不上了，这里重编。
            # 编号是给"图上点框 ↔ 表格选中"双向联动用的，只在单页内要求唯一。
            for i, item in enumerate(texts, start=1):
                item['id'] = i

            pages.append({
                'page': page_no,
                'image_size': image.size,
                'texts': texts,
                'annotated': renderer.render(image, texts,
                                             cover_mode=self.cover_mode),
            })

        return {'file': self.file_path, 'elapsed': round(time.time() - started, 2),
                'pages': pages}


class MainWindow(QMainWindow):
    def __init__(self, config: dict):
        super().__init__()
        self.cfg = config

        self.store = DataStore(**config['logging'])
        self.translator = Translator(config['translator']['fuzzy_threshold'],
                                     config['translator']['kanji_traps'])
        self.renderer = OverlayRenderer(**config['overlay'])
        self.store.setup_logging()

        try:
            self.translator.load_dictionary(config['translator']['dictionary_path'])
        except FileNotFoundError:
            pass    # 启动后再由状态栏提示，不要拦着界面出来

        self.file_path = None
        self.original_images = []
        self.pages = []          # [{page, image_size, texts, annotated}, ...]
        self.page_index = 0
        # 标注模式固定成中日对照（保留原文，译文画在旁边）。
        # 界面上那个「遮挡原文」开关撤掉了：这是个**核对**工具，把日文盖住等于
        # 把要核对的东西销毁了 —— 用户没法再回头确认翻译对不对。
        # OverlayRenderer 里的遮挡模式没删，只是界面不再暴露它。
        self.cover_mode = False
        self.thread = None

        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_statusbar()

        self.setWindowTitle("日文零件图纸术语自动翻译系统")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

    # ---- 界面搭建 ----

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        self.file_label = QLabel("未打开文件")
        self.file_label.setStyleSheet("font-weight: bold; color: #333;")
        bar.addWidget(self.file_label)
        bar.addStretch()

        self.process_btn = QPushButton("▶  执行识别与翻译")
        self.process_btn.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: white;"
            "  padding: 8px 20px; border-radius: 4px;"
            "  font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1565C0; }"
            "QPushButton:disabled { background-color: #BDBDBD; }"
        )
        self.process_btn.clicked.connect(self._on_process)
        bar.addWidget(self.process_btn)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Vertical)
        self.viewer = ImageViewer()
        self.viewer.setMinimumHeight(300)
        self.viewer.text_selected.connect(self._on_text_selected)
        splitter.addWidget(self.viewer)

        self.results = ResultPanel()
        self.results.setMinimumHeight(150)
        self.results.text_selected.connect(self._on_text_selected)
        self.results.export_requested.connect(self._on_export_json)
        splitter.addWidget(self.results)

        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(4)
        layout.addWidget(self.progress)

        self.page_controls = QWidget()
        page_bar = QHBoxLayout(self.page_controls)
        page_bar.setContentsMargins(0, 0, 0, 0)
        prev_btn = QPushButton("◀ 上一页")
        prev_btn.clicked.connect(lambda: self._show_page(self.page_index - 1))
        self.page_label = QLabel("第 1/1 页")
        self.page_label.setAlignment(Qt.AlignCenter)
        next_btn = QPushButton("下一页 ▶")
        next_btn.clicked.connect(lambda: self._show_page(self.page_index + 1))
        page_bar.addWidget(prev_btn)
        page_bar.addWidget(self.page_label)
        page_bar.addWidget(next_btn)
        self.page_controls.setVisible(False)
        layout.addWidget(self.page_controls)

    def _build_menu(self):
        bar = self.menuBar()

        file_menu = bar.addMenu("文件(&F)")
        self._add_action(file_menu, "打开文件(&O)...", self._on_open_file,
                         QKeySequence.Open)
        file_menu.addSeparator()
        self._add_action(file_menu, "导出标注图纸(&I)...",
                         self._on_export_image, "Ctrl+E")
        self._add_action(file_menu, "导出翻译数据 JSON(&J)...",
                         self._on_export_json)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出(&X)", self.close, QKeySequence.Quit)

        dict_menu = bar.addMenu("词典(&D)")
        self._add_action(dict_menu, "编辑词典(&E)...", self._on_edit_dictionary,
                         "Ctrl+D")

        view_menu = bar.addMenu("视图(&V)")
        self._add_action(view_menu, "放大(&I)", self.viewer.zoom_in,
                         QKeySequence.ZoomIn)
        self._add_action(view_menu, "缩小(&O)", self.viewer.zoom_out,
                         QKeySequence.ZoomOut)
        self._add_action(view_menu, "适配窗口(&F)", self.viewer.zoom_fit, "Ctrl+0")

        help_menu = bar.addMenu("帮助(&H)")
        self._add_action(help_menu, "关于(&A)", self._on_about)

    def _add_action(self, menu, text, slot, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_toolbar(self):
        toolbar = QToolBar("主工具栏")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        for text, slot in [
            ("📂 打开文件", self._on_open_file),
            (None, None),                       # 分隔线
            ("🔍+", self.viewer.zoom_in),
            ("🔍-", self.viewer.zoom_out),
            ("⊞ 适配", self.viewer.zoom_fit),
            (None, None),
            ("💾 导出图纸", self._on_export_image),
            ("📋 导出JSON", self._on_export_json),
        ]:
            if text is None:
                toolbar.addSeparator()
                continue
            action = QAction(text, self)
            action.triggered.connect(slot)
            toolbar.addAction(action)

    def _build_statusbar(self):
        self.setStatusBar(QStatusBar())
        self.status_label = QLabel("就绪")
        self.statusBar().addWidget(self.status_label, 1)
        self.file_info_label = QLabel("")
        self.statusBar().addPermanentWidget(self.file_info_label)

    def _status(self, message: str):
        """状态栏右侧的临时提示，10 秒后自动消失。"""
        self.statusBar().showMessage(message, 10000)

    # ---- 打开与预览 ----

    def _on_open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开图纸文件", "", FILE_FILTER)
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        try:
            info = get_file_info(path)
            images, _ = parse_file(path)
        except Exception as e:
            QMessageBox.critical(self, "文件打开失败", str(e))
            self.status_label.setText("文件打开失败")
            return

        self.file_path = path
        self.original_images = images
        self.pages = []

        self.file_label.setText(info['filename'])
        pages = f" | {info['page_count']} 页" if info['page_count'] > 1 else ""
        self.file_info_label.setText(
            f"{info['format']} | {info['width']}×{info['height']} | "
            f"{info['size_kb']} KB{pages}"
        )

        self.page_controls.setVisible(len(images) > 1)
        self._show_page(0)
        self.status_label.setText(f"已加载: {info['filename']}")
        self._status(f"共 {len(images)} 页。点「执行识别与翻译」开始。")

    def _show_page(self, index: int):
        if not (0 <= index < len(self.original_images)):
            return

        self.page_index = index
        page = self.pages[index] if index < len(self.pages) else None

        self.viewer.load_image(page['annotated'] if page
                               else self.original_images[index])
        texts = page['texts'] if page else []
        self.viewer.add_annotations(texts)
        self.results.set_results(texts)

        if len(self.original_images) > 1:
            self.page_label.setText(f"第 {index + 1}/{len(self.original_images)} 页")

    # ---- 处理 ----

    def _on_process(self):
        if self.thread and self.thread.isRunning():
            return
        if not self.file_path:
            QMessageBox.information(self, "提示", "请先打开图纸文件。")
            return

        self.process_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)          # 不确定进度，转圈
        self.status_label.setText("正在处理中…")

        self.thread = ProcessThread(self.file_path, self.cover_mode, self.cfg)
        self.thread.done.connect(self._on_done)
        self.thread.failed.connect(self._on_failed)
        self.thread.start()

    def _on_done(self, result: dict):
        self.process_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.pages = result['pages']

        texts = [t for p in self.pages for t in p['texts']]
        translated = sum(1 for t in texts if t['match_method'] in ('exact', 'fuzzy'))
        skipped = sum(1 for t in texts if t['match_method'] == 'skipped')
        elapsed = result['elapsed']

        self._save_json(result)
        self._show_page(0)

        self.status_label.setText(
            f"处理完成 | 共 {len(texts)} 条, 已翻译 {translated}, "
            f"无需翻译 {skipped}, 未匹配 {len(texts) - translated - skipped}, "
            f"耗时 {elapsed} 秒"
            + (" ✓" if elapsed <= 5.0 else " ⚠ 超过 5 秒")
        )

    def _on_failed(self, message: str):
        self.process_btn.setEnabled(True)
        self.progress.setVisible(False)
        self.status_label.setText("处理失败")
        QMessageBox.critical(self, "处理失败", message)

    def _save_json(self, result: dict):
        for page in self.pages:
            try:
                self.store.save_result(
                    source_file=Path(result['file']).name,
                    image_size=page['image_size'],
                    texts=page['texts'],
                    output_dir=self.cfg['output']['default_dir'],
                    page_num=page['page'],
                )
            except Exception as e:
                self._status(f"保存 JSON 时出错: {e}")

    # ---- 交互 ----

    def _on_text_selected(self, text_id: int):
        self.viewer.highlight_text(text_id)
        self.results.select_text_id(text_id)

        for page in self.pages:
            for item in page['texts']:
                if item.get('id') == text_id:
                    self._status(f"选中: {item['text']} → {item['translated']}")
                    return

    def _on_edit_dictionary(self):
        dialog = DictEditorDialog(self.translator, self)
        dialog.dictionary_changed.connect(
            lambda: self._status("词典已更新。已有结果需要重新执行翻译才会生效。")
        )
        dialog.exec()

    def _on_about(self):
        QMessageBox.about(
            self, "关于",
            "日文零件图纸术语自动翻译系统\n\n"
            "版本 1.0.0 · PaddleOCR + PyQt5\n\n"
            "• 识别机械图纸中的日文术语\n"
            "• 离线术语词典翻译（精确 + 模糊匹配）\n"
            "• 中文红色标注叠加，支持遮挡 / 中日对照两种模式\n"
            "• 结果导出为 JSON，含原文、译文、坐标、匹配方式\n\n"
            "全部处理在本机完成，图纸不外传。"
        )

    # ---- 导出 ----

    def _on_export_image(self):
        if not self.pages:
            QMessageBox.information(self, "提示", "请先执行识别与翻译。")
            return

        default = f"translated_{Path(self.file_path).stem}.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出标注图纸", default,
            "PNG 图像 (*.png);;JPEG 图像 (*.jpg);;PDF 文件 (*.pdf)"
        )
        if not path:
            return

        image = self.pages[self.page_index]['annotated']
        try:
            if path.lower().endswith('.pdf'):
                image.save(path, 'PDF', resolution=self.cfg['output']['export_dpi'])
            else:
                image.save(path)
            self._status(f"图纸已导出: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_json(self):
        if not self.pages:
            QMessageBox.information(self, "提示", "请先执行识别与翻译。")
            return

        default = f"translation_{Path(self.file_path).stem}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出翻译数据 JSON", default, "JSON 文件 (*.json)"
        )
        if not path:
            return

        page = self.pages[self.page_index]
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'source_file': Path(self.file_path).name,
                    'page': page['page'],
                    'texts': page['texts'],
                }, f, ensure_ascii=False, indent=2)
            self._status(f"JSON 已导出: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            reply = QMessageBox.question(
                self, "确认退出", "识别还在进行中，确定要退出吗？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            self.thread.wait(3000)
        event.accept()
