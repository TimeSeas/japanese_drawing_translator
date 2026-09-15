"""识别结果表格：ID / 原文 / 译文 / 匹配方式 / 置信度 / 坐标。

匹配方式这一列是有意留的 —— 客户复核时只关心 fuzzy 那些（机器猜的），
exact 的可以跳过。筛选下拉框就是为这个场景加的。
"""

from typing import Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

COLUMNS = ['ID', '日文原文', '中文翻译', '匹配方式', '置信度', '坐标范围']

UNTRANSLATED = '[未翻译]'
METHOD_LABELS = {'exact': '精确', 'fuzzy': '模糊', 'unmatched': '-'}

RED = QColor(200, 50, 50)
YELLOW = QColor(180, 150, 0)
GREEN = QColor(0, 140, 0)


class ResultPanel(QWidget):
    text_selected = pyqtSignal(int)
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: List[Dict] = []
        self._shown: List[Dict] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        top = QHBoxLayout()
        self._stats = QLabel("就绪")
        self._stats.setStyleSheet("color: #555; font-size: 12px;")
        top.addWidget(self._stats)
        top.addStretch()

        top.addWidget(QLabel("筛选:"))
        self._filter = QComboBox()
        self._filter.addItems(['全部', '已翻译', '未翻译', '精确匹配', '模糊匹配'])
        self._filter.currentTextChanged.connect(self._apply_filter)
        top.addWidget(self._filter)

        export = QPushButton("导出 JSON")
        export.setFixedWidth(100)
        export.clicked.connect(self.export_requested.emit)
        top.addWidget(export)
        layout.addLayout(top)

        self._table = QTableWidget()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        for col in (0, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in (1, 2, 5):
            header.setSectionResizeMode(col, QHeaderView.Stretch)

        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table)

    # ---- 数据 ----

    def set_results(self, results: List[Dict]):
        self._all = list(results)
        self._apply_filter()

    def select_text_id(self, text_id: int):
        """在表格里选中指定 ID 的行并滚过去。图纸上点了标注框时联动用。"""
        for row, item in enumerate(self._shown):
            if item.get('id') == text_id:
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                return

    # ---- 表格刷新 ----

    def _apply_filter(self):
        mode = self._filter.currentText()
        tests = {
            '已翻译': lambda r: r.get('translated') not in ('', UNTRANSLATED, None),
            '未翻译': lambda r: r.get('translated') in ('', UNTRANSLATED, None),
            '精确匹配': lambda r: r.get('match_method') == 'exact',
            '模糊匹配': lambda r: r.get('match_method') == 'fuzzy',
        }
        test = tests.get(mode)
        self._shown = [r for r in self._all if test(r)] if test else list(self._all)

        self._refresh_table()
        self._update_stats()

    def _refresh_table(self):
        self._table.setRowCount(len(self._shown))

        for row, item in enumerate(self._shown):
            self._cell(row, 0, item.get('id', row + 1))

            self._cell(row, 1, item.get('original', ''))

            translated = item.get('translated', '')
            cell = self._cell(row, 2, translated)
            if translated == UNTRANSLATED:
                cell.setForeground(QBrush(RED))
                cell.setFont(QFont(cell.font().family(), -1, italic=True))

            method = item.get('match_method', '')
            cell = self._cell(row, 3, METHOD_LABELS.get(method, method))
            if method == 'unmatched':
                cell.setForeground(QBrush(YELLOW))

            confidence = item.get('confidence', 0)
            cell = self._cell(row, 4, f"{confidence:.1%}"
                              if isinstance(confidence, float) else str(confidence))
            if isinstance(confidence, (int, float)):
                cell.setForeground(QBrush(
                    GREEN if confidence >= 0.9 else
                    YELLOW if confidence >= 0.7 else RED
                ))

            box = item.get('bbox', {})
            self._cell(row, 5, "({min_x}, {min_y}) - ({max_x}, {max_y})".format(
                **{k: box.get(k, '?') for k in
                   ('min_x', 'min_y', 'max_x', 'max_y')}
            ))

    def _cell(self, row: int, col: int, text) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.setItem(row, col, item)
        return item

    def _update_stats(self):
        total = len(self._all)
        translated = sum(
            1 for r in self._all
            if r.get('translated') not in ('', UNTRANSLATED, None)
        )
        avg = (sum(r.get('confidence', 0) for r in self._all) / total * 100
               if total else 0)

        text = (f"共 {total} 条 | 已翻译 {translated} | "
                f"未匹配 {total - translated} | 平均置信度 {avg:.1f}%")
        if len(self._shown) != total:
            text += f" | 筛选后 {len(self._shown)} 条"
        self._stats.setText(text)

    def _on_cell_clicked(self, row: int, col: int):
        if 0 <= row < len(self._shown):
            text_id = self._shown[row].get('id')
            if text_id:
                self.text_selected.emit(int(text_id))
