"""
词典编辑器 | Dictionary Editor
===========================
术语词典的增删改查对话框。

功能:
  - 表格展示所有术语条目
  - 搜索过滤（日文/中文双向搜索）
  - 添加/编辑/删除词条
  - 保存到 JSON 文件
"""

from typing import Dict, Optional

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QHeaderView, QPushButton, QLineEdit,
    QLabel, QMessageBox, QAbstractItemView, QDialogButtonBox,
    QFormLayout, QInputDialog, QWidget,
)
from PyQt5.QtCore import Qt, pyqtSignal


class DictEditorDialog(QDialog):
    """
    词典编辑器对话框。

    信号:
        dictionary_changed(): 词典被修改后发出
    """

    dictionary_changed = pyqtSignal()

    def __init__(self, translator, parent=None):
        """
        Args:
            translator: Translator 实例
        """
        super().__init__(parent)
        self._translator = translator
        self._setup_ui()
        self._load_dictionary()

    def _setup_ui(self):
        """构建 UI"""
        self.setWindowTitle("术语词典编辑器")
        self.setMinimumSize(700, 500)

        layout = QVBoxLayout(self)

        # ---- 搜索栏 ----
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("输入日文或中文关键词搜索...")
        self._search_input.textChanged.connect(self._on_search)
        search_layout.addWidget(self._search_input)

        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self._clear_search)
        search_layout.addWidget(clear_btn)

        layout.addLayout(search_layout)

        # ---- 统计标签 ----
        self._count_label = QLabel()
        layout.addWidget(self._count_label)

        # ---- 词典表格 ----
        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(['日文术语', '中文翻译'])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self._table.cellDoubleClicked.connect(self._on_edit)
        layout.addWidget(self._table)

        # ---- 操作按钮 ----
        btn_layout = QHBoxLayout()

        add_btn = QPushButton("添加词条")
        add_btn.clicked.connect(self._on_add)
        btn_layout.addWidget(add_btn)

        edit_btn = QPushButton("编辑词条")
        edit_btn.clicked.connect(lambda: self._on_edit(-1, -1))
        btn_layout.addWidget(edit_btn)

        delete_btn = QPushButton("删除词条")
        delete_btn.clicked.connect(self._on_delete)
        btn_layout.addWidget(delete_btn)

        btn_layout.addStretch()

        # 保存按钮
        save_btn = QPushButton("保存词典")
        save_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "padding: 6px 20px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #45a049; }"
        )
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _load_dictionary(self):
        """从 Translator 加载词典到表格"""
        dictionary = self._translator.dictionary
        self._populate_table(dictionary)

    def _populate_table(self, dictionary: Dict[str, str]):
        """填充表格"""
        self._table.setRowCount(0)
        self._table.setRowCount(len(dictionary))

        for row, (jp, cn) in enumerate(sorted(dictionary.items())):
            self._table.setItem(row, 0, QTableWidgetItem(jp))
            self._table.setItem(row, 1, QTableWidgetItem(cn))

        self._count_label.setText(f"共 {len(dictionary)} 条术语")

    def _on_search(self, keyword: str):
        """搜索过滤"""
        if not keyword.strip():
            self._load_dictionary()
            return

        results = self._translator.search_terms(keyword)
        self._populate_table(results)
        self._count_label.setText(
            f"搜索 \"{keyword}\": {len(results)} 条结果"
        )

    def _clear_search(self):
        """清除搜索"""
        self._search_input.clear()
        self._load_dictionary()

    def _on_add(self):
        """添加新词条"""
        # 使用两个输入对话框
        jp, ok1 = QInputDialog.getText(
            self, "添加词条", "日文术语:"
        )
        if not ok1 or not jp.strip():
            return

        cn, ok2 = QInputDialog.getText(
            self, "添加词条", f"日文: {jp}\n中文翻译:"
        )
        if not ok2 or not cn.strip():
            return

        self._translator.add_term(jp.strip(), cn.strip())
        self._load_dictionary()
        self.dictionary_changed.emit()

    def _on_edit(self, row: int, col: int):
        """编辑选中的词条"""
        if row < 0:
            current_row = self._table.currentRow()
        else:
            current_row = row

        if current_row < 0:
            QMessageBox.information(self, "提示", "请先选择要编辑的词条。")
            return

        jp_item = self._table.item(current_row, 0)
        cn_item = self._table.item(current_row, 1)

        if not jp_item:
            return

        old_jp = jp_item.text()
        old_cn = cn_item.text() if cn_item else ""

        # 编辑中文翻译
        new_cn, ok = QInputDialog.getText(
            self, "编辑词条",
            f"日文: {old_jp}\n中文翻译:",
            text=old_cn,
        )

        if ok and new_cn.strip():
            self._translator.add_term(old_jp, new_cn.strip())
            self._load_dictionary()
            self.dictionary_changed.emit()

    def _on_delete(self):
        """删除选中的词条"""
        current_row = self._table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的词条。")
            return

        jp_item = self._table.item(current_row, 0)
        if not jp_item:
            return

        jp = jp_item.text()

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除术语 \"{jp}\" 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._translator.remove_term(jp)
            self._load_dictionary()
            self.dictionary_changed.emit()

    def _on_save(self):
        """保存词典到文件"""
        try:
            self._translator.save_dictionary()
            QMessageBox.information(
                self, "保存成功",
                f"词典已保存，共 {self._translator.term_count} 条术语。"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "保存失败",
                f"保存词典时发生错误:\n{str(e)}"
            )
