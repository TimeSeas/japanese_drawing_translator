"""术语词典编辑器。

词典是翻译质量的瓶颈 —— 图纸上认出来的生词、以及模糊匹配猜错的词，
都指望在这里补。所以搜索是日汉双向的：知道中文意思也能反查到日文条目。
"""

from typing import Dict

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)


class DictEditorDialog(QDialog):
    dictionary_changed = pyqtSignal()

    def __init__(self, translator, parent=None):
        super().__init__(parent)
        self.translator = translator
        self._build_ui()
        self._reload()

    def _build_ui(self):
        self.setWindowTitle("术语词典编辑器")
        self.setMinimumSize(700, 500)
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入日文或中文关键词…")
        self.search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_input)

        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self.search_input.clear)
        search_row.addWidget(clear_btn)
        layout.addLayout(search_row)

        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(['日文术语', '中文翻译'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellDoubleClicked.connect(self._on_edit)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        for text, slot in [("添加词条", self._on_add),
                           ("编辑词条", lambda: self._on_edit(-1, -1)),
                           ("删除词条", self._on_delete)]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)

        buttons.addStretch()

        save_btn = QPushButton("保存词典")
        save_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white;"
            "  padding: 6px 20px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        save_btn.clicked.connect(self._on_save)
        buttons.addWidget(save_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    # ---- 表格 ----

    def _reload(self):
        self._fill(self.translator.dictionary)

    def _fill(self, entries: Dict[str, str]):
        self.table.setRowCount(len(entries))
        for row, (jp, cn) in enumerate(sorted(entries.items())):
            self.table.setItem(row, 0, QTableWidgetItem(jp))
            self.table.setItem(row, 1, QTableWidgetItem(cn))
        self.count_label.setText(f"共 {len(entries)} 条术语")

    def _on_search(self, keyword: str):
        keyword = keyword.strip()
        if not keyword:
            self._reload()
            return

        found = self.translator.search_terms(keyword)
        self._fill(found)
        self.count_label.setText(f"搜索「{keyword}」: {len(found)} 条")

    def _selected_term(self) -> str:
        """返回选中行的日文词，没选中返回空串。"""
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.text() if item else ""

    def _changed(self):
        self._reload()
        self.dictionary_changed.emit()

    # ---- 增删改 ----

    def _on_add(self):
        jp, ok = QInputDialog.getText(self, "添加词条", "日文术语:")
        if not ok or not jp.strip():
            return

        cn, ok = QInputDialog.getText(
            self, "添加词条", f"日文: {jp.strip()}\n中文翻译:"
        )
        if not ok or not cn.strip():
            return

        self.translator.add_term(jp.strip(), cn.strip())
        self._changed()

    def _on_edit(self, row: int, col: int):
        term = self._selected_term()
        if not term:
            QMessageBox.information(self, "提示", "请先选择要编辑的词条。")
            return

        # 只让改译文：日文词是词典的键，改了等于删一条再加一条，
        # 想改日文就删掉重加，免得把已有译文弄丢。
        old_cn = self.table.item(self.table.currentRow(), 1).text()
        new_cn, ok = QInputDialog.getText(
            self, "编辑词条", f"日文: {term}\n中文翻译:",
            text=old_cn,
        )
        if ok and new_cn.strip():
            self.translator.add_term(term, new_cn.strip())
            self._changed()

    def _on_delete(self):
        term = self._selected_term()
        if not term:
            QMessageBox.information(self, "提示", "请先选择要删除的词条。")
            return

        reply = QMessageBox.question(
            self, "确认删除", f"确定删除术语「{term}」吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.translator.remove_term(term)
            self._changed()

    def _on_save(self):
        try:
            self.translator.save_dictionary()
            QMessageBox.information(
                self, "保存成功",
                f"已保存 {self.translator.term_count} 条术语。"
            )
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))
