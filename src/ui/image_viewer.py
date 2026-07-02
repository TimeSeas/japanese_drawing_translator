"""
图纸预览控件 | Image Viewer Widget
===============================
基于 QGraphicsView 的图纸预览组件，支持:
  - 鼠标滚轮缩放
  - 拖拽平移
  - 翻译标注叠加渲染
  - 点击高亮指定文本区域

原理说明:
  QGraphicsView 是 Qt 的 2D 图形视图框架，使用场景 (QGraphicsScene) /
  视图 (QGraphicsView) 分离架构。视图负责用户交互（缩放/平移），
  场景负责管理显示对象（图像、标注框等）。
"""

from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QFrame,
)
from PyQt5.QtGui import (
    QPixmap, QImage, QPen, QColor, QBrush,
    QWheelEvent, QMouseEvent, QPainter,
)
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal

from PIL import Image
import numpy as np


class ImageViewer(QGraphicsView):
    """
    图纸预览控件。

    信号:
        text_selected(int): 当用户点击标注区域时发出，传递文本 ID
    """

    text_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 场景初始化 ----
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # ---- 当前显示的图像 ----
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._annotation_rects: List[QGraphicsRectItem] = []

        # ---- 视图设置 ----
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.AnchorUnderMouse
        )
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

        # ---- 视图外观 ----
        self.setBackgroundBrush(QBrush(QColor(240, 240, 240)))
        self.setFrameShape(QFrame.NoFrame)

        # ---- 交互设置 ----
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # 缩放范围
        self._zoom_level = 1.0
        self._min_zoom = 0.1
        self._max_zoom = 10.0

    # ============================================================
    # 图像加载
    # ============================================================

    def load_image(self, pil_image: Image.Image):
        """
        加载 PIL Image 并显示在视图中。

        自动适配视图大小（Fit to View），将图像缩放到适合窗口的尺寸。

        Args:
            pil_image: PIL Image 对象
        """
        self._clear_annotations()

        # ---- PIL Image → QPixmap ----
        if pil_image.mode == 'RGBA':
            pil_image = pil_image.convert('RGB')

        img_array = np.array(pil_image)
        height, width = img_array.shape[:2]

        if len(img_array.shape) == 2:
            # 灰度图
            qimage = QImage(
                img_array.data, width, height, width,
                QImage.Format_Grayscale8,
            )
        else:
            # RGB 图
            qimage = QImage(
                img_array.data, width, height, width * 3,
                QImage.Format_RGB888,
            )

        pixmap = QPixmap.fromImage(qimage)

        # ---- 设置场景 ----
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

        # ---- 适配视图 ----
        self.fitInView(
            self._scene.sceneRect(),
            Qt.KeepAspectRatio,
        )

        self._zoom_level = 1.0

    def load_qpixmap(self, pixmap: QPixmap):
        """
        直接加载 QPixmap。

        Args:
            pixmap: QPixmap 对象
        """
        self._clear_annotations()
        self._scene.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(
            self._scene.sceneRect(),
            Qt.KeepAspectRatio,
        )
        self._zoom_level = 1.0

    def get_current_pixmap(self) -> Optional[QPixmap]:
        """返回当前显示的 QPixmap"""
        if self._pixmap_item:
            return self._pixmap_item.pixmap()
        return None

    # ============================================================
    # 标注叠加
    # ============================================================

    def clear_annotations(self):
        """清除所有标注"""
        self._clear_annotations()

    def add_annotations(
        self,
        annotations: List[Dict],
        highlight_id: Optional[int] = None,
    ):
        """
        在图像上叠加矩形标注框。

        每个标注用蓝色半透明矩形框标记文本区域。
        若指定 highlight_id，则该标注用红色高亮显示。

        Args:
            annotations: 标注数据列表 [{'id': 1, 'bbox': {...}, ...}]
            highlight_id: 高亮显示的文本 ID
        """
        self._clear_annotations()

        for ann in annotations:
            bbox = ann.get('bbox', {})
            text_id = ann.get('id', 0)

            rect = QRectF(
                bbox['min_x'], bbox['min_y'],
                bbox['max_x'] - bbox['min_x'],
                bbox['max_y'] - bbox['min_y'],
            )

            # 颜色: 高亮为红色，普通为蓝色
            if text_id == highlight_id:
                pen = QPen(QColor(255, 50, 50), 2)
                brush = QBrush(QColor(255, 50, 50, 40))
            else:
                pen = QPen(QColor(50, 100, 255), 1.5)
                brush = QBrush(QColor(50, 100, 255, 25))

            rect_item = self._scene.addRect(rect, pen, brush)
            rect_item.setData(0, text_id)  # 存储文本 ID
            self._annotation_rects.append(rect_item)

    def highlight_text(self, text_id: int):
        """
        高亮显示指定 ID 的文本区域。

        Args:
            text_id: 文本 ID
        """
        for rect_item in self._annotation_rects:
            if rect_item.data(0) == text_id:
                rect_item.setPen(QPen(QColor(255, 50, 50), 3))
                rect_item.setBrush(QBrush(QColor(255, 50, 50, 60)))
                # 滚动到可见区域
                self.centerOn(rect_item.rect().center())
            else:
                rect_item.setPen(QPen(QColor(50, 100, 255), 1.5))
                rect_item.setBrush(QBrush(QColor(50, 100, 255, 25)))

    def _clear_annotations(self):
        """清除所有标注矩形"""
        for rect_item in self._annotation_rects:
            self._scene.removeItem(rect_item)
        self._annotation_rects.clear()

    # ============================================================
    # 缩放与平移
    # ============================================================

    def wheelEvent(self, event: QWheelEvent):
        """
        鼠标滚轮缩放事件。

        向前滚动放大，向后滚动缩小。缩放比例 1.15 倍/档。
        """
        zoom_in = event.angleDelta().y() > 0
        factor = 1.15 if zoom_in else 1 / 1.15

        new_zoom = self._zoom_level * factor

        if self._min_zoom <= new_zoom <= self._max_zoom:
            self._zoom_level = new_zoom
            self.scale(factor, factor)

    def zoom_in(self):
        """放大"""
        if self._zoom_level * 1.25 <= self._max_zoom:
            self._zoom_level *= 1.25
            self.scale(1.25, 1.25)

    def zoom_out(self):
        """缩小"""
        if self._zoom_level / 1.25 >= self._min_zoom:
            self._zoom_level /= 1.25
            self.scale(1 / 1.25, 1 / 1.25)

    def zoom_fit(self):
        """适配窗口"""
        if self._scene.sceneRect().width() > 0:
            self.fitInView(
                self._scene.sceneRect(),
                Qt.KeepAspectRatio,
            )
            # 重新计算缩放级别
            tr = self.transform()
            self._zoom_level = tr.m11()

    def reset_view(self):
        """重置视图到原始大小"""
        self.resetTransform()
        self._zoom_level = 1.0

    def mousePressEvent(self, event: QMouseEvent):
        """
        鼠标点击事件。

        左键点击检测是否点中了标注矩形，若点中则发出 text_selected 信号。
        """
        if event.button() == Qt.LeftButton:
            # 检测是否点击了标注矩形
            scene_pos = self.mapToScene(event.pos())
            for rect_item in self._annotation_rects:
                if rect_item.contains(scene_pos):
                    text_id = rect_item.data(0)
                    if text_id:
                        self.highlight_text(text_id)
                        self.text_selected.emit(text_id)
                        return

        super().mousePressEvent(event)
