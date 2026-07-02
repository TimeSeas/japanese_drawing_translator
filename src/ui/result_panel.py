"""
结果面板 | Result Panel
=====================
翻译结果表格面板，展示 OCR 识别和翻译结果。

功能:
  - QTableWidget 展示: ID | 日文原文 | 中文翻译 | 匹配方式 | 置信度 | 坐标
  - 点击行时发出信号，联动图纸预览高亮
  - 支持按置信度/匹配方式筛选
  - 显示统计摘要
"""

from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QComboBox,
    QPushButton, QAbstractItemView, QSplitter,
)
from PyQt5.QtGui import QColor, QBrush, QFont
from PyQt5.QtCore import Qt, pyqtSignal


class ResultPanel(QWidget):
    """
    翻译结果面板。

    信号:
        text_selected(int): 用户点击表格行时发出，传递文本 ID
        export_requested(): 请求导出 JSON 数据
    """

    text_selected = pyqtSignal(int)
    export_requested = pyqtSignal()

    # 表格列定义
    COLUMNS = ['ID', '日文原文', '中文翻译', '匹配方式', '置信度', '坐标范围']

    def __init__(self, parent=None):
        super().__init__(parent)

        self._all_results: List[Dict] = []
        self._filtered_results: List[Dict] = []

        self._setup_ui()

    def _setup_ui(self):
        """构建 UI 布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ---- 顶部统计栏 ----
        top_layout = QHBoxLayout()

        self._stats_label = QLabel("就绪")
        self._stats_label.setStyleSheet("color: #555; font-size: 12px;")
        top_layout.addWidget(self._stats_label)

        top_layout.addStretch()

        # 筛选下拉框
        top_layout.addWidget(QLabel("筛选:"))
        self._filter_combo = QComboBox()
        self._filter_combo.addItems(['全部', '已翻译', '未翻译', '精确匹配', '模糊匹配'])
        self._filter_combo.currentTextChanged.connect(self._apply_filter)
        top_layout.addWidget(self._filter_combo)

        # 导出按钮
        export_btn = QPushButton("导出 JSON")
        export_btn.setFixedWidth(100)
        export_btn.clicked.connect(self.export_requested.emit)
        top_layout.addWidget(export_btn)

        layout.addLayout(top_layout)

        # ---- 结果表格 ----
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)

        # 表格设置
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SingleSelection
        )
        self._table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        # 列宽设置
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)          # 日文
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)          # 中文
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # 匹配方式
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # 置信度
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # 坐标

        # 点击事件
        self._table.cellClicked.connect(self._on_cell_clicked)

        layout.addWidget(self._table)

    # ============================================================
    # 数据加载
    # ============================================================

    def set_results(self, results: List[Dict]):
        """
        设置翻译结果数据并刷新表格。

        Args:
            results: 翻译结果列表
        """
        self._all_results = results
        self._apply_filter()

    def clear(self):
        """清空表格"""
        self._all_results = []
        self._filtered_results = []
        self._table.setRowCount(0)
        self._stats_label.setText("就绪")

    # ============================================================
    # 筛选
    # ============================================================

    def _apply_filter(self):
        """根据筛选条件过滤结果"""
        filter_text = self._filter_combo.currentText()

        if filter_text == '全部':
            self._filtered_results = list(self._all_results)
        elif filter_text == '已翻译':
            self._filtered_results = [
                r for r in self._all_results
                if r.get('translated') and r['translated'] != '[未翻译]'
            ]
        elif filter_text == '未翻译':
            self._filtered_results = [
                r for r in self._all_results
                if not r.get('translated') or r['translated'] == '[未翻译]'
            ]
        elif filter_text == '精确匹配':
            self._filtered_results = [
                r for r in self._all_results
                if r.get('match_method') == 'exact'
            ]
        elif filter_text == '模糊匹配':
            self._filtered_results = [
                r for r in self._all_results
                if r.get('match_method') == 'fuzzy'
            ]

        self._refresh_table()
        self._update_stats()

    def _refresh_table(self):
        """刷新表格内容"""
        self._table.setRowCount(len(self._filtered_results))

        for row, item in enumerate(self._filtered_results):
            # ID
            self._set_cell(row, 0, str(item.get('id', row + 1)))

            # 日文原文
            self._set_cell(row, 1, item.get('original', ''))

            # 中文翻译
            translated = item.get('translated', '')
            cell = self._set_cell(row, 2, translated)
            if translated == '[未翻译]':
                cell.setForeground(QBrush(QColor(200, 50, 50)))  # 红色标记未翻译
                cell.setFont(QFont(cell.font().family(), -1, italic=True))

            # 匹配方式
            method = item.get('match_method', '')
            method_display = {
                'exact': '精确',
                'fuzzy': '模糊',
                'unmatched': '-',
            }.get(method, method)
            cell = self._set_cell(row, 3, method_display)
            if method == 'unmatched':
                cell.setForeground(QBrush(QColor(180, 150, 0)))

            # 置信度
            confidence = item.get('confidence', 0)
            conf_text = f"{confidence:.1%}" if isinstance(confidence, float) else str(confidence)
            cell = self._set_cell(row, 4, conf_text)
            # 颜色: 高→绿, 中→黄, 低→红
            if isinstance(confidence, (int, float)):
                if confidence >= 0.9:
                    cell.setForeground(QBrush(QColor(0, 140, 0)))
                elif confidence >= 0.7:
                    cell.setForeground(QBrush(QColor(180, 150, 0)))
                else:
                    cell.setForeground(QBrush(QColor(200, 50, 50)))

            # 坐标范围
            bbox = item.get('bbox', {})
            coord_text = (
                f"({bbox.get('min_x', '?')}, {bbox.get('min_y', '?')}) - "
                f"({bbox.get('max_x', '?')}, {bbox.get('max_y', '?')})"
            )
            self._set_cell(row, 5, coord_text)

    def _set_cell(self, row: int, col: int, text: str) -> QTableWidgetItem:
        """设置单元格内容，返回 QTableWidgetItem"""
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.setItem(row, col, item)
        return item

    def _update_stats(self):
        """更新统计摘要"""
        total = len(self._all_results)
        translated = sum(
            1 for r in self._all_results
            if r.get('translated') and r['translated'] != '[未翻译]'
        )
        unmatched = total - translated
        avg_conf = (
            sum(r.get('confidence', 0) for r in self._all_results) / total * 100
            if total > 0 else 0
        )

        self._stats_label.setText(
            f"共 {total} 条 | 已翻译 {translated} | 未匹配 {unmatched}"
            f" | 平均置信度 {avg_conf:.1f}%"
            + (f" | 筛选后 {len(self._filtered_results)} 条"
               if len(self._filtered_results) != total else "")
        )

    def _on_cell_clicked(self, row: int, col: int):
        """表格行点击处理"""
        if 0 <= row < len(self._filtered_results):
            text_id = self._filtered_results[row].get('id', row + 1)
            if text_id:
                self.text_selected.emit(int(text_id))

    # ============================================================
    # 数据访问
    # ============================================================

    def get_all_results(self) -> List[Dict]:
        """返回全部结果"""
        return list(self._all_results)

    def get_selected_result(self) -> Optional[Dict]:
        """返回当前选中的行对应的结果"""
        current_row = self._table.currentRow()
        if 0 <= current_row < len(self._filtered_results):
            return self._filtered_results[current_row]
        return None
