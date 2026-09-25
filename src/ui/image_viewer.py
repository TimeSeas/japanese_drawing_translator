"""图纸预览控件：滚轮缩放、拖拽平移、标注框叠加与点击命中。

QGraphicsView 自带的滚轮是滚动而不是缩放，所以这里覆盖 wheelEvent 自己
算缩放比例；拖拽平移直接借它的 ScrollHandDrag 模式，不用手写。
"""

from typing import Dict, List, Optional

import numpy as np
from PIL import Image
from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QWheelEvent
from PyQt5.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

ZOOM_STEP = 1.15
MIN_ZOOM, MAX_ZOOM = 0.1, 10.0

# 普通标注框只描一条虚线边，不填充。填了之后每一条识别结果都是一块半透明
# 蓝底，几十条叠上去等于给图纸蒙了层纱，原文和线条全被压暗了 —— 而预览的
# 重点是看图，框只是提示"这里有字、可以点"。选中那条才实心强调。
PEN_NORMAL = QPen(QColor(70, 120, 220, 150), 1, Qt.DashLine)
PEN_NORMAL.setCosmetic(True)          # 线宽不随缩放变，永远是屏幕上 1px
BRUSH_NORMAL = QBrush(Qt.NoBrush)

PEN_ACTIVE = QPen(QColor(255, 50, 50), 2)
PEN_ACTIVE.setCosmetic(True)
BRUSH_ACTIVE = QBrush(QColor(255, 50, 50, 55))


class ImageViewer(QGraphicsView):
    text_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._rects: List = []
        self._zoom = 1.0

        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(240, 240, 240)))
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def load_image(self, pil_image: Image.Image):
        """换一张图并适配到窗口大小。"""
        img = pil_image.convert('RGB') if pil_image.mode == 'RGBA' else pil_image
        arr = np.array(img)
        h, w = arr.shape[:2]

        # 灰度图要从 2D 数组构造，RGB 图每行 w*3 字节
        qimage = (QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
                  if arr.ndim == 2 else
                  QImage(arr.data, w, h, w * 3, QImage.Format_RGB888))

        pixmap = QPixmap.fromImage(qimage)   # fromImage 会拷一份，arr 之后可以释放

        self._scene.clear()                  # 清掉上一张图和它的标注框
        self._rects.clear()
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._zoom = 1.0

    # ---- 标注框 ----

    def add_annotations(self, annotations: List[Dict], highlight_id: Optional[int] = None):
        """给每个识别结果画一个框，框上挂 id 供点击命中。"""
        self.clear_annotations()

        for ann in annotations:
            box = ann.get('bbox')
            if not box:
                continue

            text_id = ann.get('id', 0)
            active = text_id == highlight_id
            rect = QRectF(box['min_x'], box['min_y'],
                          box['max_x'] - box['min_x'],
                          box['max_y'] - box['min_y'])

            item = self._scene.addRect(
                rect, PEN_ACTIVE if active else PEN_NORMAL,
                BRUSH_ACTIVE if active else BRUSH_NORMAL,
            )
            item.setData(0, text_id)
            self._rects.append(item)

    def clear_annotations(self):
        for item in self._rects:
            self._scene.removeItem(item)
        self._rects.clear()

    def highlight_text(self, text_id: int):
        """把指定 id 的框标红并移入视野，其它的恢复成蓝色。"""
        for item in self._rects:
            if item.data(0) == text_id:
                item.setPen(PEN_ACTIVE)
                item.setBrush(BRUSH_ACTIVE)
                self.centerOn(item.rect().center())
            else:
                item.setPen(PEN_NORMAL)
                item.setBrush(BRUSH_NORMAL)

    # ---- 缩放 ----

    def wheelEvent(self, event: QWheelEvent):
        self._zoom_by(ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP)

    def zoom_in(self):
        self._zoom_by(1.25)

    def zoom_out(self):
        self._zoom_by(1 / 1.25)

    def _zoom_by(self, factor: float):
        if MIN_ZOOM <= self._zoom * factor <= MAX_ZOOM:
            self._zoom *= factor
            self.scale(factor, factor)

    def zoom_fit(self):
        if self._scene.sceneRect().width() > 0:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()

    # ---- 点击 ----

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.pos())
            for item in self._rects:
                if item.contains(pos):
                    text_id = item.data(0)
                    if text_id:
                        self.highlight_text(text_id)
                        self.text_selected.emit(text_id)
                        return
        super().mousePressEvent(event)
