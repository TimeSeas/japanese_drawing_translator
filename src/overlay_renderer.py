"""
标注叠加模块 | Overlay Renderer Module
====================================
在原图上绘制红色中文翻译标注。

功能模式:
  1. 遮挡模式 (cover): 用白色矩形填充日文区域 → 上方绘制红色中文
  2. 不遮挡模式 (no_cover): 在日文区域上方/下方绘制红色中文，
     带碰撞避免算法，防止多条译文互相覆盖。

原理说明:
  使用 Pillow 的 ImageDraw 在图像上绘制矩形和文字。
  字体大小根据原始文本区域的包围盒高度自动计算:
    font_size = bbox_height × font_scale_ratio

  不遮挡模式碰撞避免算法:
    1. 对所有标注按原文 y 坐标从上到下排序
    2. 为每个标注计算译文占用的矩形框 (text_rect)
    3. 优先尝试放在原文上方，检查与已放置标注的 text_rect 是否有重叠
    4. 若有重叠，尝试下方；若仍有重叠，尝试偏移到原文侧上方/侧下方
    5. 最终选择无重叠的位置；若无法完全避免，选择重叠最小的位置
    6. 同时确保不超出图像边界
"""

import logging
from typing import List, Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================
DEFAULT_CONFIG = {
    'text_color': (255, 0, 0),       # 红色字体 (RGB)
    'cover_bg_color': (255, 255, 255),  # 遮挡背景色 (白色)
    'font_scale_ratio': 0.8,         # 字体大小占文本框高度比例
    'default_cover_mode': True,      # 默认遮挡模式
    'min_gap': 3,                    # 标注与原文之间的最小间距 (px)
}


class OverlayRenderer:
    """
    图纸翻译标注叠加渲染器。

    使用方式:
        renderer = OverlayRenderer(config)
        result_image = renderer.render(original_image, ocr_results)
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._font_cache: Dict[int, ImageFont.FreeTypeFont] = {}

    def render(
        self,
        image: Image.Image,
        annotations: List[Dict],
        cover_mode: Optional[bool] = None,
    ) -> Image.Image:
        """
        在图像上绘制翻译标注。

        Args:
            image: 原始图像 (PIL Image, RGB 模式)
            annotations: 标注数据列表
            cover_mode: 是否遮挡原文

        Returns:
            标注后的 PIL Image
        """
        if cover_mode is None:
            cover_mode = self.config['default_cover_mode']

        # 创建副本
        if image.mode != 'RGB':
            img = image.convert('RGB')
        else:
            img = image.copy()

        draw = ImageDraw.Draw(img)
        img_width, img_height = img.size

        # 过滤有效标注并预计算文本布局
        valid_anns = []
        for ann in annotations:
            translated = ann.get('translated', '')
            if not translated or translated == '[未翻译]':
                continue
            valid_anns.append(self._prepare_annotation(ann, draw))

        if not valid_anns:
            return img

        if cover_mode:
            for ann_data in valid_anns:
                self._draw_cover_mode(draw, ann_data)
        else:
            # 不遮挡模式：使用碰撞避免算法
            placements = self._compute_placements(
                valid_anns, img_width, img_height
            )
            for ann_data, (x, y) in placements:
                self._draw_text_at(draw, ann_data, x, y)

        logger.info(
            f"标注完成: {len(valid_anns)} 条标注 "
            f"({'遮挡' if cover_mode else '不遮挡+防碰撞'}模式)"
        )

        return img

    def _prepare_annotation(self, ann: Dict, draw: ImageDraw.Draw) -> Dict:
        """
        预计算标注的文本布局参数。

        Returns:
            {
                'bbox': {min_x, min_y, max_x, max_y},  # 原文区域
                'text_lines': [str, ...],               # 译文行
                'font': ImageFont,
                'line_height': int,
                'text_width': float,                    # 最宽行的宽度
                'text_height': int,                     # 总文本高度
            }
        """
        bbox = ann['bbox']
        bbox_height = bbox['max_y'] - bbox['min_y']
        bbox_width = bbox['max_x'] - bbox['min_x']

        font_size = max(8, int(bbox_height * self.config['font_scale_ratio']))
        font = self._get_font(font_size)

        text_lines = self._wrap_text(ann.get('translated', ''), font, bbox_width, draw)
        line_height = font_size + 2
        text_height = len(text_lines) * line_height

        # 计算实际文本宽度
        text_width = max((draw.textlength(line, font=font) for line in text_lines), default=0)

        return {
            'bbox': bbox,
            'text_lines': text_lines,
            'font': font,
            'line_height': line_height,
            'text_width': text_width,
            'text_height': text_height,
        }

    # ============================================================
    # 碰撞避免算法
    # ============================================================

    def _compute_placements(
        self,
        annotations: List[Dict],
        img_width: int,
        img_height: int,
    ) -> List[Tuple[Dict, Tuple[int, int]]]:
        """
        为所有标注计算无碰撞的放置位置。

        算法步骤:
          1. 按原文 y 坐标从上到下排序
          2. 对每个标注，按优先级生成候选位置:
             a. 原文正上方 (首选)
             b. 原文正下方
             c. 原文左上偏移 (shifted above-left)
             d. 原文右下偏移 (shifted below-right)
          3. 选择第一个不与已放置标注重叠且不越界的位置
          4. 若所有候选都冲突，选择重叠面积最小的位置
        """
        # 按 y 坐标排序
        sorted_anns = sorted(annotations, key=lambda a: a['bbox']['min_y'])

        placed = []  # [(text_rect, original_bbox), ...]
        result = []

        for ann_data in sorted_anns:
            candidates = self._generate_candidates(ann_data, img_width, img_height)

            best_placement = None
            best_overlap = float('inf')

            for x, y in candidates:
                # 计算该候选位置占用的矩形
                text_rect = self._compute_text_rect(ann_data, x, y)

                # 计算与已放置标注的重叠面积
                total_overlap = 0
                for placed_rect, placed_orig_rect in placed:
                    overlap = self._rect_overlap_area(text_rect, placed_rect)
                    # 额外惩罚: 如果覆盖了原文区域本身
                    overlap += self._rect_overlap_area(text_rect, placed_orig_rect) * 2
                    total_overlap += overlap

                if total_overlap == 0:
                    best_placement = (x, y)
                    best_overlap = 0
                    break  # 完美位置，直接使用
                elif total_overlap < best_overlap:
                    best_overlap = total_overlap
                    best_placement = (x, y)

            if best_placement is None:
                # 极端情况: 使用默认上方位置
                best_placement = (ann_data['bbox']['min_x'], max(0, ann_data['bbox']['min_y'] - ann_data['text_height'] - self.config['min_gap']))

            text_rect = self._compute_text_rect(ann_data, best_placement[0], best_placement[1])
            placed.append((text_rect, self._bbox_to_rect(ann_data['bbox'])))
            result.append((ann_data, best_placement))

            if best_overlap > 0:
                logger.debug(
                    f"文本 '{ann_data['text_lines'][0][:10]}...' 放置时有 "
                    f"{best_overlap:.0f}px² 的重叠 (不可避免)"
                )

        return result

    def _generate_candidates(
        self, ann_data: Dict, img_width: int, img_height: int,
    ) -> List[Tuple[int, int]]:
        """
        生成候选放置位置列表，按优先级排序。

        优先级:
          1. 原文正上方
          2. 原文正下方
          3-6. 上方/下方 + 左/右/居中偏移 (处理水平空间不足的情况)
        """

        bbox = ann_data['bbox']
        text_w = ann_data['text_width']
        text_h = ann_data['text_height']
        bbox_w = bbox['max_x'] - bbox['min_x']
        gap = self.config['min_gap']

        candidates = []

        # 水平位置选项: 居中 / 左对齐 / 右对齐
        def x_center(): return bbox['min_x'] + max(0, (bbox_w - text_w) / 2)
        def x_left(): return bbox['min_x']
        def x_right(): return bbox['max_x'] - text_w

        # 首选: 上方居中
        y_above = bbox['min_y'] - text_h - gap
        if y_above >= 0:
            candidates.append((x_center(), y_above))

        # 次选: 下方居中
        y_below = bbox['max_y'] + gap
        if y_below + text_h <= img_height:
            candidates.append((x_center(), y_below))

        # 上方左/右对齐
        for x_fn in [x_left, x_right]:
            x = x_fn()
            if y_above >= 0 and x >= 0 and x + text_w <= img_width:
                candidates.append((x, y_above))

        # 下方左/右对齐
        for x_fn in [x_left, x_right]:
            x = x_fn()
            if y_below + text_h <= img_height and x >= 0 and x + text_w <= img_width:
                candidates.append((x, y_below))

        # 上方但更靠上 (给拥挤区域多留空间)
        y_above_far = bbox['min_y'] - text_h - gap * 3
        if y_above_far >= 0:
            candidates.append((x_center(), y_above_far))

        # 下方但更靠下
        y_below_far = bbox['max_y'] + gap * 3
        if y_below_far + text_h <= img_height:
            candidates.append((x_center(), y_below_far))

        # 若原文区域够宽，也可以把译文放在原文侧边 (右方)
        x_right_side = bbox['max_x'] + gap
        if x_right_side + text_w <= img_width:
            y_mid = bbox['min_y'] + (bbox['max_y'] - bbox['min_y'] - text_h) / 2
            candidates.append((x_right_side, max(0, y_mid)))

        return candidates

    @staticmethod
    def _bbox_to_rect(bbox: Dict) -> Tuple[float, float, float, float]:
        """将 bbox dict 转为矩形 tuple (min_x, min_y, max_x, max_y)。"""
        return (bbox['min_x'], bbox['min_y'], bbox['max_x'], bbox['max_y'])

    def _compute_text_rect(
        self, ann_data: Dict, x: float, y: float,
    ) -> Tuple[float, float, float, float]:
        """计算译文占用的矩形区域 (min_x, min_y, max_x, max_y)。"""
        text_w = ann_data['text_width']
        text_h = ann_data['text_height']
        return (x, y, x + text_w, y + text_h)

    @staticmethod
    def _rect_overlap_area(
        rect_a: Tuple[float, float, float, float],
        rect_b: Tuple[float, float, float, float],
    ) -> float:
        """计算两个矩形的重叠面积。"""
        ax1, ay1, ax2, ay2 = rect_a
        bx1, by1, bx2, by2 = rect_b

        ox1 = max(ax1, bx1)
        oy1 = max(ay1, by1)
        ox2 = min(ax2, bx2)
        oy2 = min(ay2, by2)

        if ox1 >= ox2 or oy1 >= oy2:
            return 0.0

        return (ox2 - ox1) * (oy2 - oy1)

    # ============================================================
    # 绘制方法
    # ============================================================

    def _draw_cover_mode(self, draw: ImageDraw.Draw, ann_data: Dict):
        """遮挡模式: 覆盖原文 + 居中绘制翻译。"""
        bbox = ann_data['bbox']
        text_lines = ann_data['text_lines']
        font = ann_data['font']
        line_height = ann_data['line_height']
        text_height = ann_data['text_height']
        bbox_height = bbox['max_y'] - bbox['min_y']
        bbox_width = bbox['max_x'] - bbox['min_x']

        bg_color = self.config['cover_bg_color']
        text_color = self.config['text_color']

        # 覆盖矩形
        draw.rectangle(
            [(bbox['min_x'], bbox['min_y']), (bbox['max_x'], bbox['max_y'])],
            fill=bg_color,
        )

        # 居中文字
        y = bbox['min_y'] + max(0, (bbox_height - text_height) / 2)
        for line in text_lines:
            tw = draw.textlength(line, font=font)
            x = bbox['min_x'] + max(0, (bbox_width - tw) / 2)
            draw.text((x, y), line, fill=text_color, font=font)
            y += line_height

    def _draw_text_at(
        self, draw: ImageDraw.Draw, ann_data: Dict,
        x: float, y: float,
    ):
        """在指定位置绘制翻译文本。"""
        text_color = self.config['text_color']
        font = ann_data['font']
        line_height = ann_data['line_height']

        for line in ann_data['text_lines']:
            draw.text((x, y), line, fill=text_color, font=font)
            y += line_height

    def _wrap_text(
        self, text: str, font: ImageFont.FreeTypeFont,
        max_width: float, draw: ImageDraw.Draw,
    ) -> List[str]:
        """
        根据最大宽度自动换行文本。
        CJK 字符可在任意两个字符间断行，拉丁字符按单词断行。
        """
        if max_width <= 0:
            return [text]
        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            if draw.textlength(test_line, font=font) > max_width and current_line:
                lines.append(current_line)
                current_line = char
            else:
                current_line = test_line
        if current_line:
            lines.append(current_line)
        return lines or [text]

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """
        获取指定大小的字体。

        字体查找优先级:
          1. Windows: Microsoft YaHei (微软雅黑)
          2. Windows: SimHei (黑体)
          3. Windows: MS Gothic (日文字体)
          4. Pillow 默认字体 (回退)

        使用字体缓存避免重复加载。
        """
        if size in self._font_cache:
            return self._font_cache[size]

        # 尝试系统字体（按优先级）
        font_paths = [
            # Windows 中文字体
            "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
            "C:/Windows/Fonts/msyhbd.ttc",     # 微软雅黑粗体
            "C:/Windows/Fonts/simhei.ttf",     # 黑体
            "C:/Windows/Fonts/simsun.ttc",     # 宋体
            "C:/Windows/Fonts/msgothic.ttc",   # MS Gothic (日文)
            "C:/Windows/Fonts/msmincho.ttc",   # MS Mincho (日文)
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
            "/System/Library/Fonts/PingFang.ttc",  # macOS
        ]

        for font_path in font_paths:
            try:
                font = ImageFont.truetype(font_path, size=size)
                self._font_cache[size] = font
                return font
            except (OSError, IOError):
                continue

        # 回退到 Pillow 默认字体
        logger.warning(f"未找到系统中文字体，使用默认字体 (size={size})")
        font = ImageFont.load_default()
        self._font_cache[size] = font
        return font

    def render_comparison(
        self,
        image: Image.Image,
        annotations: List[Dict],
    ) -> Image.Image:
        """
        生成"遮挡"与"不遮挡"的并排对比图。

        Args:
            image: 原始图像
            annotations: 标注数据

        Returns:
            左右并排对比图 (原图宽度 × 2)
        """
        cover_img = self.render(image, annotations, cover_mode=True)
        no_cover_img = self.render(image, annotations, cover_mode=False)

        # 并排拼接
        total_width = cover_img.width + no_cover_img.width
        max_height = max(cover_img.height, no_cover_img.height)
        result = Image.new('RGB', (total_width, max_height), (255, 255, 255))

        result.paste(cover_img, (0, 0))
        result.paste(no_cover_img, (cover_img.width, 0))

        # 添加分隔标签
        draw = ImageDraw.Draw(result)
        label_font = self._get_font(16)
        draw.text((10, 5), "[遮挡模式]", fill=(0, 0, 255), font=label_font)
        draw.text((cover_img.width + 10, 5), "[不遮挡模式]", fill=(0, 0, 255), font=label_font)

        return result


# ============================================================
# 全局渲染器单例
# ============================================================
_renderer_instance: Optional[OverlayRenderer] = None


def get_renderer(config: Optional[dict] = None) -> OverlayRenderer:
    """获取渲染器单例"""
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = OverlayRenderer(config)
    return _renderer_instance
