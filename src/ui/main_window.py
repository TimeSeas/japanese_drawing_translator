"""
主窗口 | Main Window
==================
应用主界面，整合所有子组件。

布局:
  ┌──────────────────────────────────────┐
  │  菜单栏: 文件 | 词典管理 | 帮助        │
  ├────────────┬─────────────────────────┤
  │  工具栏     │                         │
  │  (导入/处理/│     图纸预览区域          │
  │   导出)     │   (ImageViewer)          │
  ├────────────┴─────────────────────────┤
  │  翻译结果面板 (ResultPanel)            │
  ├──────────────────────────────────────┤
  │  状态栏: 就绪 | 文件信息               │
  └──────────────────────────────────────┘

处理流程:
  用户导入文件 → 文件解析 → 图像预处理 → OCR识别
  → 术语翻译 → 标注叠加 → 预览显示 → 结果存储
"""

import os
import time
from pathlib import Path
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QMenuBar, QMenu, QToolBar, QStatusBar,
    QFileDialog, QMessageBox, QLabel, QPushButton,
    QCheckBox, QProgressBar, QApplication, QFrame,
    QAction,
)
from PyQt5.QtGui import QKeySequence, QPixmap, QIcon
from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal

# 将 src 目录加入路径
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.file_parser import parse_file, get_file_info, SUPPORTED_EXTENSIONS
from src.image_processor import preprocess, DEFAULT_CONFIG as PP_DEFAULT
from src.ocr_engine import get_engine
from src.translator import get_translator
from src.overlay_renderer import get_renderer
from src.data_store import get_store
from src.ui.image_viewer import ImageViewer
from src.ui.result_panel import ResultPanel
from src.ui.dict_editor import DictEditorDialog


class ProcessingWorker:
    """
    处理任务封装（在独立线程中执行）。

    包含:
      文件解析 → 预处理 → OCR → 翻译 → 叠加渲染
    """
    def __init__(
        self,
        file_path: str,
        cover_mode: bool,
        preprocess_config: Optional[dict] = None,
    ):
        self.file_path = file_path
        self.cover_mode = cover_mode
        self.preprocess_config = preprocess_config or PP_DEFAULT
        self.results = {}
        self.error = None

    def run(self):
        """执行处理（在子线程中调用）"""
        try:
            start_time = time.time()

            # Step 1: 文件解析
            images, filename = parse_file(self.file_path)
            source_filename = Path(self.file_path).name

            # 获取引擎单例
            ocr = get_engine()
            translator = get_translator()
            renderer = get_renderer()
            store = get_store()

            all_texts = []
            page_results = []
            annotated_images = []

            # Step 2-6: 逐页处理
            for page_idx, image in enumerate(images, start=1):
                # 预处理
                processed = preprocess(image, self.preprocess_config)

                # OCR 识别
                ocr_results = ocr.recognize(processed)

                # 翻译
                for item in ocr_results:
                    original_text = item['text']
                    translated, method, conf = translator.translate(original_text)
                    item['original'] = original_text
                    item['translated'] = translated
                    item['match_method'] = method
                    item['confidence'] = conf

                all_texts.extend(ocr_results)

                # 生成标注图像
                annotated = renderer.render(
                    image, ocr_results, cover_mode=self.cover_mode
                )
                annotated_images.append(annotated)

                page_results.append({
                    'page': page_idx,
                    'image_size': (image.width, image.height),
                    'texts': ocr_results,
                })

            # 统计
            elapsed = round(time.time() - start_time, 2)
            total = len(all_texts)
            translated_count = sum(
                1 for t in all_texts
                if t.get('translated') and t['translated'] != '[未翻译]'
            )
            unmatched = total - translated_count
            avg_conf = (
                sum(t.get('confidence', 0) for t in all_texts) / total
                if total > 0 else 0
            )

            self.results = {
                'source_file': source_filename,
                'filename': filename,
                'images': images,
                'ocr_results': all_texts,
                'annotated_images': annotated_images,
                'page_results': page_results,
                'stats': {
                    'total': total,
                    'translated': translated_count,
                    'unmatched': unmatched,
                    'avg_confidence': round(avg_conf, 4),
                    'elapsed_sec': elapsed,
                },
            }

        except Exception as e:
            self.error = str(e)


class ProcessingThread(QThread):
    """
    处理线程，避免阻塞 UI。

    信号:
        progress(int): 处理进度 (0-100)
        finished(dict): 处理完成，传递结果字典
        error(str): 处理失败，传递错误消息
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, worker: ProcessingWorker):
        super().__init__()
        self._worker = worker

    def run(self):
        """线程入口"""
        self.progress.emit(10)
        self._worker.run()
        self.progress.emit(100)

        if self._worker.error:
            self.error_occurred.emit(self._worker.error)
        else:
            self.finished.emit(self._worker.results)


class MainWindow(QMainWindow):
    """
    应用主窗口。
    """

    def __init__(self):
        super().__init__()

        # ---- 引擎初始化 ----
        self._ocr = get_engine()
        self._translator = get_translator()
        self._renderer = get_renderer()
        self._store = get_store()

        # 加载词典
        try:
            dict_path = Path(__file__).resolve().parents[2] / 'dictionary.json'
            self._translator.load_dictionary(str(dict_path))
        except FileNotFoundError:
            pass  # UI 启动后再提示

        # ---- 状态变量 ----
        self._current_file: Optional[str] = None
        self._current_results: List[Dict] = []     # 全部页的合并结果
        self._page_results: List[Dict] = []         # 每页独立结果 [{page, image_size, texts}, ...]
        self._annotated_images: List = []
        self._original_images: List = []
        self._current_page: int = 0
        self._cover_mode: bool = True
        self._processing: bool = False

        # ---- 构建 UI ----
        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_connections()

        # ---- 窗口设置 ----
        self.setWindowTitle("日文零件图纸术语自动翻译系统")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

    # ============================================================
    # UI 构建
    # ============================================================

    def _setup_ui(self):
        """构建主界面布局"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)

        # ---- 控制栏 ----
        control_layout = QHBoxLayout()

        self._file_label = QLabel("未打开文件")
        self._file_label.setStyleSheet("font-weight: bold; color: #333;")
        control_layout.addWidget(self._file_label)

        control_layout.addStretch()

        # 遮挡模式开关
        self._cover_checkbox = QCheckBox("遮挡原文")
        self._cover_checkbox.setChecked(self._cover_mode)
        self._cover_checkbox.setToolTip(
            "勾选: 用白色背景覆盖日文原文后标注翻译\n"
            "不勾选: 在原文上方/下方标注翻译"
        )
        control_layout.addWidget(self._cover_checkbox)

        # 处理按钮
        self._process_btn = QPushButton("▶  执行识别与翻译")
        self._process_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #1976D2; color: white;"
            "  padding: 8px 20px; border-radius: 4px;"
            "  font-weight: bold; font-size: 13px;"
            "}"
            "QPushButton:hover { background-color: #1565C0; }"
            "QPushButton:disabled { background-color: #BDBDBD; }"
        )
        self._process_btn.setToolTip("对当前打开的图纸执行 OCR 识别和翻译")
        control_layout.addWidget(self._process_btn)

        main_layout.addLayout(control_layout)

        # ---- 主分割区 ----
        splitter = QSplitter(Qt.Vertical)

        # 图纸预览
        self._image_viewer = ImageViewer()
        self._image_viewer.setMinimumHeight(300)
        splitter.addWidget(self._image_viewer)

        # 结果面板
        self._result_panel = ResultPanel()
        self._result_panel.setMinimumHeight(150)
        splitter.addWidget(self._result_panel)

        # 默认比例为 60:40
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)

        main_layout.addWidget(splitter)

        # ---- 进度条 (默认隐藏) ----
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setMaximumHeight(4)
        main_layout.addWidget(self._progress_bar)

        # ---- 翻页控件 (默认隐藏) ----
        self._page_controls = QWidget()
        page_layout = QHBoxLayout(self._page_controls)
        page_layout.setContentsMargins(0, 0, 0, 0)

        self._prev_page_btn = QPushButton("◀ 上一页")
        self._prev_page_btn.clicked.connect(self._prev_page)
        page_layout.addWidget(self._prev_page_btn)

        self._page_label = QLabel("第 1/1 页")
        self._page_label.setAlignment(Qt.AlignCenter)
        page_layout.addWidget(self._page_label)

        self._next_page_btn = QPushButton("下一页 ▶")
        self._next_page_btn.clicked.connect(self._next_page)
        page_layout.addWidget(self._next_page_btn)

        self._page_controls.setVisible(False)
        main_layout.addWidget(self._page_controls)

    def _setup_menu(self):
        """设置菜单栏"""
        menu_bar = self.menuBar()

        # ---- 文件菜单 ----
        file_menu = menu_bar.addMenu("文件(&F)")

        open_action = QAction("打开文件(&O)...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self._on_open_file)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        export_image_action = QAction("导出标注图纸(&I)...", self)
        export_image_action.setShortcut(QKeySequence("Ctrl+E"))
        export_image_action.triggered.connect(self._on_export_image)
        file_menu.addAction(export_image_action)

        export_json_action = QAction("导出翻译数据 JSON(&J)...", self)
        export_json_action.triggered.connect(self._on_export_json)
        file_menu.addAction(export_json_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ---- 词典菜单 ----
        dict_menu = menu_bar.addMenu("词典(&D)")

        edit_dict_action = QAction("编辑词典(&E)...", self)
        edit_dict_action.setShortcut(QKeySequence("Ctrl+D"))
        edit_dict_action.triggered.connect(self._on_edit_dictionary)
        dict_menu.addAction(edit_dict_action)

        # ---- 视图菜单 ----
        view_menu = menu_bar.addMenu("视图(&V)")

        zoom_in_action = QAction("放大(&I)", self)
        zoom_in_action.setShortcut(QKeySequence.ZoomIn)
        zoom_in_action.triggered.connect(self._image_viewer.zoom_in)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("缩小(&O)", self)
        zoom_out_action.setShortcut(QKeySequence.ZoomOut)
        zoom_out_action.triggered.connect(self._image_viewer.zoom_out)
        view_menu.addAction(zoom_out_action)

        zoom_fit_action = QAction("适配窗口(&F)", self)
        zoom_fit_action.setShortcut(QKeySequence("Ctrl+0"))
        zoom_fit_action.triggered.connect(self._image_viewer.zoom_fit)
        view_menu.addAction(zoom_fit_action)

        # ---- 帮助菜单 ----
        help_menu = menu_bar.addMenu("帮助(&H)")

        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        """设置工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # 打开文件
        open_action = QAction("📂 打开文件", self)
        open_action.triggered.connect(self._on_open_file)
        toolbar.addAction(open_action)

        toolbar.addSeparator()

        # 缩放控制
        zoom_in_action = QAction("🔍+", self)
        zoom_in_action.triggered.connect(self._image_viewer.zoom_in)
        toolbar.addAction(zoom_in_action)

        zoom_out_action = QAction("🔍-", self)
        zoom_out_action.triggered.connect(self._image_viewer.zoom_out)
        toolbar.addAction(zoom_out_action)

        zoom_fit_action = QAction("⊞ 适配", self)
        zoom_fit_action.triggered.connect(self._image_viewer.zoom_fit)
        toolbar.addAction(zoom_fit_action)

        toolbar.addSeparator()

        # 导出
        export_img_action = QAction("💾 导出图纸", self)
        export_img_action.triggered.connect(self._on_export_image)
        toolbar.addAction(export_img_action)

        export_json_action = QAction("📋 导出JSON", self)
        export_json_action.triggered.connect(self._on_export_json)
        toolbar.addAction(export_json_action)

    def _setup_statusbar(self):
        """设置状态栏"""
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_label = QLabel("就绪")
        self._statusbar.addWidget(self._status_label, 1)

        self._file_info_label = QLabel("")
        self._statusbar.addPermanentWidget(self._file_info_label)

    def _setup_connections(self):
        """连接信号和槽"""
        self._cover_checkbox.stateChanged.connect(self._on_cover_mode_changed)
        self._process_btn.clicked.connect(self._on_process)

        # ImageViewer 信号
        self._image_viewer.text_selected.connect(self._on_text_selected)

        # ResultPanel 信号
        self._result_panel.text_selected.connect(self._on_text_selected)
        self._result_panel.export_requested.connect(self._on_export_json)

    # ============================================================
    # 文件操作
    # ============================================================

    def _on_open_file(self):
        """打开文件对话框"""
        # 构建文件过滤器
        filters = (
            "支持的图纸文件 (*.pdf *.jpg *.jpeg *.png *.bmp);;"
            "PDF 文件 (*.pdf);;"
            "图像文件 (*.jpg *.jpeg *.png *.bmp);;"
            "所有文件 (*.*)"
        )

        file_path, _ = QFileDialog.getOpenFileName(
            self, "打开图纸文件", "", filters
        )

        if not file_path:
            return

        self._load_file(file_path)

    def _load_file(self, file_path: str):
        """
        加载文件并显示预览。

        流程: 文件解析 → 显示首页预览
        """
        try:
            # 获取文件信息
            info = get_file_info(file_path)

            # 解析文件
            images, filename = parse_file(file_path)
            self._original_images = images
            self._current_file = file_path
            self._current_page = 0
            self._current_results = []
            self._annotated_images = []

            # 显示预览
            self._show_page(0)

            # 更新 UI
            self._file_label.setText(info['filename'])
            self._file_info_label.setText(
                f"{info['format']} | "
                f"{info['width']}×{info['height']} | "
                f"{info['size_kb']} KB"
                + (f" | {info['page_count']} 页" if info['page_count'] > 1 else "")
            )

            # 翻页控件
            if len(images) > 1:
                self._page_controls.setVisible(True)
                self._page_label.setText(f"第 1/{len(images)} 页")
            else:
                self._page_controls.setVisible(False)

            self._status_label.setText(f"已加载: {info['filename']}")
            self._set_status(f"文件已加载, 共 {len(images)} 页。点击「执行识别与翻译」开始处理。")

        except Exception as e:
            QMessageBox.critical(self, "文件打开失败", str(e))
            self._status_label.setText("文件打开失败")
            self._set_status(f"错误: {e}")

    def _show_page(self, page_idx: int):
        """显示指定页，仅展示当前页的标注结果"""
        if 0 <= page_idx < len(self._original_images):
            if self._annotated_images and page_idx < len(self._annotated_images):
                # 显示标注后的图
                self._image_viewer.load_image(self._annotated_images[page_idx])
            else:
                # 显示原图
                self._image_viewer.load_image(self._original_images[page_idx])

            self._current_page = page_idx

            # 更新翻页标签
            if len(self._original_images) > 1:
                self._page_label.setText(
                    f"第 {page_idx + 1}/{len(self._original_images)} 页"
                )

            # 更新标注框：只显示当前页的结果
            page_texts = self._get_page_texts(page_idx)
            if page_texts and self._annotated_images:
                self._image_viewer.add_annotations(page_texts)
            else:
                self._image_viewer.clear_annotations()

    def _get_page_texts(self, page_idx: int) -> List[Dict]:
        """获取指定页的翻译结果"""
        for pg in self._page_results:
            if pg.get('page') == page_idx + 1:  # page_results 中 page 从 1 开始
                return pg.get('texts', [])
        # 回退：单页情况
        if len(self._page_results) == 0 and page_idx == 0:
            return self._current_results
        return []

    # ============================================================
    # 处理流程
    # ============================================================

    def _on_process(self):
        """执行 OCR 识别和翻译"""
        if self._processing:
            return

        if not self._current_file:
            QMessageBox.information(self, "提示", "请先打开图纸文件。")
            return

        self._processing = True
        self._process_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._status_label.setText("正在处理中...")
        self._set_status("OCR 识别与翻译进行中...")

        # 创建处理工作器
        worker = ProcessingWorker(
            file_path=self._current_file,
            cover_mode=self._cover_mode,
        )

        # 创建线程
        self._thread = ProcessingThread(worker)
        self._thread.progress.connect(self._on_progress)
        self._thread.finished.connect(self._on_process_finished)
        self._thread.error_occurred.connect(self._on_process_error)
        self._thread.start()

    def _on_progress(self, value: int):
        """处理进度更新"""
        self._progress_bar.setValue(value)

    def _on_process_finished(self, results: dict):
        """处理完成"""
        self._processing = False
        self._process_btn.setEnabled(True)
        self._progress_bar.setVisible(False)

        stats = results['stats']

        # 保存结果
        self._current_results = results['ocr_results']
        self._page_results = results['page_results']
        self._annotated_images = results['annotated_images']
        self._original_images = results['images']

        # 更新预览（标注后，显示第一页）
        self._show_page(0)

        # 更新结果面板（显示第一页的结果）
        first_page_texts = self._get_page_texts(0)
        self._result_panel.set_results(first_page_texts)

        # 存储 JSON 结果
        try:
            for page_data in results['page_results']:
                pg_texts = page_data['texts']
                pg_stats = {
                    'total_detected': len(pg_texts),
                    'translated': sum(1 for t in pg_texts if t.get('translated') and t['translated'] != '[未翻译]'),
                    'unmatched': sum(1 for t in pg_texts if not t.get('translated') or t['translated'] == '[未翻译]'),
                    'avg_confidence': sum(t.get('confidence', 0) for t in pg_texts) / max(len(pg_texts), 1),
                    'processing_time_sec': stats['elapsed_sec'],
                }
                self._store.save_result(
                    source_file=results['source_file'],
                    image_size=page_data['image_size'],
                    texts=page_data['texts'],
                    stats=pg_stats,
                    page_num=page_data['page'],
                )
        except Exception as e:
            self._set_status(f"保存 JSON 结果时出错: {e}")

        # 更新状态
        elapsed = stats['elapsed_sec']
        within_limit = elapsed <= 5.0
        perf_mark = "✓" if within_limit else "⚠"

        self._status_label.setText(
            f"处理完成 | "
            f"共 {stats['total']} 条, "
            f"已翻译 {stats['translated']}, "
            f"未匹配 {stats['unmatched']}, "
            f"耗时 {elapsed}秒 {perf_mark}"
        )
        self._set_status(
            f"处理完成 | 检测 {stats['total']} 条文本 | "
            f"翻译成功 {stats['translated']} 条 | "
            f"处理耗时 {elapsed} 秒 {'(符合 ≤5s 要求)' if within_limit else '(超出 5s 限制)'}"
        )

    def _on_process_error(self, error_msg: str):
        """处理出错"""
        self._processing = False
        self._process_btn.setEnabled(True)
        self._progress_bar.setVisible(False)

        self._status_label.setText("处理失败")
        self._set_status(f"错误: {error_msg}")

        QMessageBox.critical(
            self, "处理失败",
            f"OCR 识别或翻译过程中发生错误:\n\n{error_msg}"
        )

    # ============================================================
    # 交互处理
    # ============================================================

    def _on_cover_mode_changed(self, state: int):
        """遮挡模式切换 — 重新渲染当前页"""
        self._cover_mode = bool(state)

        if self._original_images and self._page_results:
            page_idx = self._current_page
            if page_idx < len(self._original_images):
                page_texts = self._get_page_texts(page_idx)
                if page_texts:
                    # 重新渲染当前页
                    annotated = self._renderer.render(
                        self._original_images[page_idx],
                        page_texts,
                        cover_mode=self._cover_mode,
                    )
                    if page_idx < len(self._annotated_images):
                        self._annotated_images[page_idx] = annotated
                    self._image_viewer.load_image(annotated)
                    self._image_viewer.add_annotations(page_texts)

    def _on_text_selected(self, text_id: int):
        """文本选中处理"""
        # 在图纸预览中高亮
        self._image_viewer.highlight_text(text_id)

        # 在表格中查找并滚动到对应行
        for row in range(self._result_panel._table.rowCount()):
            id_item = self._result_panel._table.item(row, 0)
            if id_item and id_item.text() == str(text_id):
                self._result_panel._table.selectRow(row)
                self._result_panel._table.scrollToItem(id_item)
                break

        # 查找对应文本信息
        for r in self._current_results:
            if r.get('id') == text_id:
                translated = r.get('translated', '')
                original = r.get('original', '')
                self._set_status(f"选中: {original} → {translated}")
                break

    def _prev_page(self):
        """上一页"""
        if self._current_page > 0:
            self._show_page(self._current_page - 1)
            self._update_result_panel_for_current_page()

    def _next_page(self):
        """下一页"""
        if self._current_page < len(self._original_images) - 1:
            self._show_page(self._current_page + 1)
            self._update_result_panel_for_current_page()

    def _update_result_panel_for_current_page(self):
        """根据当前页更新结果面板"""
        page_texts = self._get_page_texts(self._current_page)
        if page_texts:
            self._result_panel.set_results(page_texts)

    # ============================================================
    # 导出操作
    # ============================================================

    def _on_export_image(self):
        """导出标注后的图纸"""
        if not self._annotated_images:
            QMessageBox.information(self, "提示", "请先执行识别与翻译处理。")
            return

        image = self._annotated_images[self._current_page]

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出标注图纸",
            f"translated_{Path(self._current_file).stem}.png",
            "PNG 图像 (*.png);;JPEG 图像 (*.jpg);;PDF 文件 (*.pdf)"
        )

        if not file_path:
            return

        try:
            if file_path.lower().endswith('.pdf'):
                image.save(file_path, 'PDF', resolution=300)
            else:
                image.save(file_path)
            self._set_status(f"图纸已导出: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"保存文件时发生错误:\n{str(e)}")

    def _on_export_json(self):
        """导出翻译数据 JSON"""
        if not self._current_results:
            QMessageBox.information(self, "提示", "请先执行识别与翻译处理。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出翻译数据 JSON",
            f"translation_{Path(self._current_file).stem}.json",
            "JSON 文件 (*.json)"
        )

        if not file_path:
            return

        try:
            import json
            output = {
                'source_file': Path(self._current_file).name if self._current_file else '',
                'page': self._current_page + 1,
                'texts': self._current_results,
            }
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
            self._set_status(f"JSON 已导出: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"保存 JSON 时发生错误:\n{str(e)}")

    def _on_edit_dictionary(self):
        """打开词典编辑器"""
        dialog = DictEditorDialog(self._translator, self)
        dialog.dictionary_changed.connect(self._on_dictionary_changed)
        dialog.exec()

    def _on_dictionary_changed(self):
        """词典被修改后的处理"""
        self._set_status("术语词典已更新。若已有处理结果，建议重新执行翻译。")

    def _on_about(self):
        """关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "日文零件图纸术语自动翻译系统\n"
            "Japanese Mechanical Drawing Terminology Auto-Translator\n\n"
            "版本: 1.0.0\n"
            "基于 PaddleOCR + PyQt6 构建\n\n"
            "功能:\n"
            "• 识别机械图纸中的日文术语\n"
            "• 自动翻译为中文（离线术语词典匹配）\n"
            "• 红色中文标注叠加（支持遮挡/不遮挡）\n"
            "• 结构化 JSON 数据存储\n\n"
            "所有处理完全本地执行，无需网络连接。"
        )

    # ============================================================
    # 辅助方法
    # ============================================================

    def _set_status(self, message: str):
        """在状态栏显示临时消息"""
        self._statusbar.showMessage(message, 10000)  # 10秒后自动消失

    def closeEvent(self, event):
        """窗口关闭事件"""
        # 确保处理线程已结束
        if self._processing:
            reply = QMessageBox.question(
                self, "确认退出",
                "处理仍在进行中，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return

        event.accept()
