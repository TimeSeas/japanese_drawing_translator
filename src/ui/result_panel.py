"""识别结果表格：ID / 原文 / 译文 / 匹配方式 / 置信度 / 坐标。

匹配方式这一列是有意留的，它把每条结果分成四类，复核时的处理方式完全不同：

  exact     词典里查到的，可以跳过不看
  fuzzy     机器猜的，重点看
  skipped   中文读者自己看得懂，本来就不用翻
  unmatched 该翻但词典里没这个词，要往词典里补

筛选下拉框就是为这个场景加的。
"""

from typing import Dict, List

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

COLUMNS = ['ID', '日文原文', '中文翻译', '匹配方式', '置信度', '方向', '坐标范围']

# 「方向」列的列号，隐藏/显示要用
COL_ORIENTATION = 5

UNTRANSLATED = '[未翻译]'
METHOD_LABELS = {'exact': '精确', 'fuzzy': '模糊',
                 'skipped': '无需翻译', 'unmatched': '-'}
# 竖排 'v' 和斜排 'rot' 目前识别不了（模型是按横排训练的），标出来是为了
# 让它们别再悄无声息地混在横排里当垃圾 —— "没支持"和"看出来但没支持"，
# 对复核的人来说是两件事。
ORIENTATION_LABELS = {'h': '横', 'v': '竖', 'rot': '斜'}
TRANSLATED_METHODS = ('exact', 'fuzzy')

RED = QColor(200, 50, 50)
YELLOW = QColor(180, 150, 0)
GREEN = QColor(0, 140, 0)


def _is_translated(item: Dict) -> bool:
    return item.get('match_method') in TRANSLATED_METHODS


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
        self._filter.addItems(['全部', '已翻译', '无需翻译', '未翻译',
                               '精确匹配', '模糊匹配'])
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
        for col in (0, 3, 4, COL_ORIENTATION):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in (1, 2, 6):
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
            '已翻译': _is_translated,
            '无需翻译': lambda r: r.get('match_method') == 'skipped',
            '未翻译': lambda r: r.get('match_method') == 'unmatched',
            '精确匹配': lambda r: r.get('match_method') == 'exact',
            '模糊匹配': lambda r: r.get('match_method') == 'fuzzy',
        }
        test = tests.get(mode)
        self._shown = [r for r in self._all if test(r)] if test else list(self._all)

        self._refresh_table()
        self._update_stats()

    def _refresh_table(self):
        self._table.setRowCount(len(self._shown))

        # 「方向」列只在真有竖排/斜排时才出现。整张图都是横排的话，一列
        # 全写着"横"只是占地方。一旦有一条竖排，这一列就出来把它标上。
        has_non_h = any(item.get('orientation', 'h') != 'h'
                        for item in self._shown)
        self._table.setColumnHidden(COL_ORIENTATION, not has_non_h)

        for row, item in enumerate(self._shown):
            self._cell(row, 0, item.get('id', row + 1))

            self._cell(row, 1, item.get('original', ''))

            method = item.get('match_method', '')
            translated = '—' if method == 'skipped' else item.get('translated', '')
            suspect = item.get('suspect')

            # 差一点没够阈值的候选。写成"[未翻译]（疑似 X 81分……）"，保留
            # [未翻译] 前缀是为了让下面的红色斜体判断和筛选统计都不受影响 ——
            # 这条仍然是 unmatched，不是翻译结果。
            shown = translated
            if translated == UNTRANSLATED and suspect:
                shown = (f"{UNTRANSLATED}（疑似 {suspect['translation']}，"
                         f"{suspect['score']} 分）")

            cell = self._cell(row, 2, shown)
            if translated == UNTRANSLATED:
                cell.setForeground(QBrush(RED))
                cell.setFont(QFont(cell.font().family(), -1, italic=True))
                if suspect:
                    cell.setToolTip(
                        f"最接近的词条：{suspect['term']}\n"
                        f"译文：{suspect['translation']}\n"
                        f"相似度：{suspect['score']} 分，差一点没够阈值\n\n"
                        f"这只是提示，不是翻译结果，没有算进「已翻译」里。\n"
                        f"可能是 OCR 认错了个别字符，也可能是图纸本身就写错了 ——"
                        f"这两者在这里长得一模一样，分不出来。"
                    )

            cell = self._cell(row, 3, METHOD_LABELS.get(method, method))
            if method == 'unmatched':
                cell.setForeground(QBrush(YELLOW))

            if method == 'skipped':
                # 没参与翻译的行，置信度是个占位的 0，画成红色 0.0% 只会让人
                # 以为出错了
                self._cell(row, 4, '—')
            else:
                confidence = item.get('confidence', 0)
                cell = self._cell(row, 4, f"{confidence:.1%}"
                                  if isinstance(confidence, float)
                                  else str(confidence))
                if isinstance(confidence, (int, float)):
                    cell.setForeground(QBrush(
                        GREEN if confidence >= 0.9 else
                        YELLOW if confidence >= 0.7 else RED
                    ))

            orientation = item.get('orientation', 'h')
            cell = self._cell(row, COL_ORIENTATION,
                              ORIENTATION_LABELS.get(orientation, orientation))
            if orientation != 'h':
                # 竖排/斜排的译文是不可信的 —— 模型按横排训练，这一条要
                # 让人一眼看出来，别当成正常结果
                cell.setForeground(QBrush(RED))

            box = item.get('bbox', {})
            self._cell(row, 6, "({min_x}, {min_y}) - ({max_x}, {max_y})".format(
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
        translated = [r for r in self._all if _is_translated(r)]
        skipped = sum(1 for r in self._all if r.get('match_method') == 'skipped')

        # 平均置信度只算真翻了的那些：未匹配和无需翻译的 0 是占位值，
        # 一起平均只会把这个数白白拉低，看不出翻译本身的质量
        avg = (sum(r.get('confidence', 0) for r in translated)
               / len(translated) * 100 if translated else 0)

        text = (f"共 {total} 条 | 已翻译 {len(translated)} | "
                f"无需翻译 {skipped} | 未匹配 {total - len(translated) - skipped} | "
                f"平均置信度 {avg:.1f}%")
        if len(self._shown) != total:
            text += f" | 筛选后 {len(self._shown)} 条"
        self._stats.setText(text)

    def _on_cell_clicked(self, row: int, col: int):
        if 0 <= row < len(self._shown):
            text_id = self._shown[row].get('id')
            if text_id:
                self.text_selected.emit(int(text_id))
