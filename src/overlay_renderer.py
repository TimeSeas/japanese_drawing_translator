"""把中文译文叠回原图。

两种模式：
  遮挡   —— 白底盖住日文，译文画在原地。干净，适合直接拿去用。
  不遮挡 —— 原文留着，译文画在旁边，中日对照。适合核对翻译对不对。

不遮挡模式要防重叠：图纸上标注本来就密，译文行宽又和原文框不一样，
不排一下会糊成一团。做法是按 y 从上到下扫，每个标注试几个候选位置，
挑第一个不和已放置项相交的。
"""

import logging
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 找不到中文字体时的兜底候选。按优先级试，第一个能加载的用。
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/msgothic.ttc",   # MS Gothic
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/PingFang.ttc",
]

Rect = Tuple[float, float, float, float]


class OverlayRenderer:
    def __init__(self, text_color=(255, 0, 0), cover_bg_color=(255, 255, 255),
                 font_scale_ratio: float = 0.65, min_gap: int = 3):
        self.text_color = tuple(text_color)
        self.cover_bg_color = tuple(cover_bg_color)
        self.font_scale_ratio = font_scale_ratio
        self.min_gap = min_gap
        self._fonts: Dict[int, ImageFont.FreeTypeFont] = {}

    def render(self, image: Image.Image, annotations: List[Dict],
               cover_mode: bool = True) -> Image.Image:
        """annotations 是 OCR 结果（需带 translated 和 bbox）。"""
        img = image.convert('RGB') if image.mode != 'RGB' else image.copy()
        draw = ImageDraw.Draw(img)

        # 没译出来的不画 —— 画个"[未翻译]"上去比不画更碍事
        items = [
            self._layout(a, draw) for a in annotations
            if a.get('translated') and a['translated'] != '[未翻译]'
        ]
        if not items:
            return img

        if cover_mode:
            for it in items:
                self._draw_cover(draw, it)
        else:
            for it, (x, y) in _place(items, img.size, self.min_gap):
                for line in it['lines']:
                    draw.text((x, y), line,
                              fill=self.text_color, font=it['font'])
                    y += it['line_height']

        return img

    # ---- 布局计算 ----

    def _layout(self, ann: Dict, draw: ImageDraw.Draw) -> Dict:
        """量出译文要占多大地方：字号跟原文框高走，按原文框宽换行。"""
        box = ann['bbox']
        box_w = box['max_x'] - box['min_x']
        box_h = box['max_y'] - box['min_y']

        size = max(8, int(box_h * self.font_scale_ratio))
        font = self._font(size)
        lines = self._wrap(ann['translated'], font, box_w, draw)

        return {
            'bbox': box,
            'lines': lines,
            'font': font,
            'line_height': size + 2,
            'width': max((draw.textlength(l, font=font) for l in lines),
                         default=0),
            'height': len(lines) * (size + 2),
        }

    def _wrap(self, text: str, font, max_width: float,
              draw: ImageDraw.Draw) -> List[str]:
        """逐字试宽。中文可以在任意字之间断，不用管词边界。"""
        if max_width <= 0:
            return [text]

        lines, current = [], ""
        for ch in text:
            if current and draw.textlength(current + ch, font=font) > max_width:
                lines.append(current)
                current = ch
            else:
                current += ch
        if current:
            lines.append(current)
        return lines or [text]

    def _font(self, size: int):
        if size not in self._fonts:
            font = None
            for path in _FONT_CANDIDATES:
                try:
                    font = ImageFont.truetype(path, size=size)
                    break
                except (OSError, IOError):
                    continue
            if font is None:
                logger.warning("没找到中文字体，用 Pillow 默认字体（中文会显示成方块）")
                font = ImageFont.load_default()
            self._fonts[size] = font
        return self._fonts[size]

    # ---- 绘制 ----

    def _draw_cover(self, draw: ImageDraw.Draw, item: Dict):
        """盖白底 + 译文中。

        白底按原文框和译文实际占位的并集来铺，不能只铺原文框：译文换行之后
        可能比原文框高（标题栏那种窄格子尤其明显），只盖原文框的话，多出来的
        那几行译文会直接压在没盖住的日文上，糊成一团。
        """
        box = item['bbox']
        box_w = box['max_x'] - box['min_x']
        box_h = box['max_y'] - box['min_y']

        # 译文比原文框矮就居中，比它高就往上顶出去，两种都由并集兜住
        y = box['min_y'] + (box_h - item['height']) / 2
        left = min(box['min_x'], box['min_x'] + (box_w - item['width']) / 2)
        top = min(box['min_y'], y)

        draw.rectangle([(left, top),
                        (max(box['max_x'], left + item['width']),
                         max(box['max_y'], top + item['height']))],
                       fill=self.cover_bg_color)

        y = top + max(0, (max(box_h, item['height']) - item['height']) / 2)
        for line in item['lines']:
            tw = draw.textlength(line, font=item['font'])
            draw.text((box['min_x'] + max(0, (box_w - tw) / 2), y),
                      line, fill=self.text_color, font=item['font'])
            y += item['line_height']


def _place(items: List[Dict], size: Tuple[int, int],
           gap: int) -> List[Tuple[Dict, Tuple[float, float]]]:
    """给每个标注挑一个不撞的位置，返回 [(标注, (x, y)), ...]。

    候选顺序：正上方 → 正下方 → 左上/右上 → 左下/右下。都撞就选
    重叠面积最小的那个（宁可有点叠，也不能不画）。
    顺带把原文自己的框也算成障碍物，免得译文盖住原文。
    """
    img_w, img_h = size
    placed: List[Rect] = []
    result = []

    for item in sorted(items, key=lambda i: i['bbox']['min_y']):
        box, tw, th = item['bbox'], item['width'], item['height']
        y_above = box['min_y'] - th - gap
        y_below = box['max_y'] + gap

        xs = (box['min_x'] + (box['max_x'] - box['min_x'] - tw) / 2,
              box['min_x'],
              box['max_x'] - tw)

        candidates = []
        if y_above >= 0:
            candidates += [(x, y_above) for x in xs]
        if y_below + th <= img_h:
            candidates += [(x, y_below) for x in xs]
        candidates = [(x, y) for x, y in candidates
                      if 0 <= x and x + tw <= img_w]

        obstacles = placed + [(box['min_x'], box['min_y'],
                               box['max_x'], box['max_y'])]
        best, best_overlap = None, float('inf')

        for x, y in candidates:
            rect = (x, y, x + tw, y + th)
            overlap = sum(_overlap(rect, o) for o in obstacles)
            if overlap == 0:
                best, best_overlap = (x, y), 0
                break
            if overlap < best_overlap:
                best, best_overlap = (x, y), overlap

        if best is None:      # 上下都放不下（框贴边），挤在上方
            best = (box['min_x'], max(0, y_above))

        result.append((item, best))
        placed.append((best[0], best[1], best[0] + tw, best[1] + th))

    return result


def _overlap(a: Rect, b: Rect) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy
