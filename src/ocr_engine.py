"""
OCR 引擎模块 | OCR Engine Module
=============================
日文文本检测与识别封装。

支持的引擎（按优先级排序）:
  - PaddleOCR (默认): 基于 PaddlePaddle 的日文 OCR，识别精度高
  - Windows OCR: Windows 10/11 内置 OCR，无需额外下载模型

使用方式:
    engine = OCREngine()
    results = engine.recognize(image)

返回结果格式:
    [
        {
            'id': 1,
            'text': '面取り',
            'confidence': 0.95,
            'bbox': {'min_x': 120, 'min_y': 340, 'max_x': 200, 'max_y': 370}
        },
        ...
    ]
"""

import logging
import numpy as np
from typing import List, Dict, Optional, Any
from PIL import Image

logger = logging.getLogger(__name__)


DEFAULT_CONFIG = {
    'lang': 'japan',           # PaddleOCR 日文模型
    'min_confidence': 0.5,     # 最低置信度阈值
    'merge_iou_threshold': 0.8,  # 重叠框合并 IOU 阈值
    'use_gpu': False,          # 是否使用 GPU
    'engine': 'paddleocr',     # OCR 引擎: 'paddleocr' | 'windows'
}


class OCREngine:
    """
    OCR 识别引擎封装。

    默认使用 PaddleOCR（日文专用模型），回退到 Windows OCR。
    模型使用懒加载，首次调用时初始化。
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._ocr = None
        self._engine = self.config.get('engine', 'paddleocr')
        self._initialized = False

    def _init_engine(self):
        """按优先级初始化 OCR 引擎。"""
        if self._initialized:
            return

        engine_type = self._engine

        # ---- 尝试 PaddleOCR ----
        if engine_type == 'paddleocr':
            try:
                from paddleocr import PaddleOCR
                logger.info("正在初始化 PaddleOCR 日文模型...")
                # use_angle_cls=False 跳过方向分类器，提升速度
                # ocr_version='PP-OCRv4' 使用最新的识别模型
                self._ocr = PaddleOCR(
                    lang='japan',
                    use_angle_cls=False,
                    ocr_version='PP-OCRv4',
                )
                logger.info("PaddleOCR 初始化完成")
                self._initialized = True
                return
            except Exception as e:
                logger.warning(f"PaddleOCR 初始化失败: {e}")

        # ---- 回退到 Windows OCR ----
        if engine_type == 'windows' or self._ocr is None:
            try:
                from winsdk.windows.media.ocr import OcrEngine as WinOcr
                from winsdk.windows.globalization import Language
                ja = Language('ja')
                if WinOcr.is_language_supported(ja):
                    self._ocr = WinOcr.try_create_from_language(ja)
                    if self._ocr:
                        logger.info("Windows OCR 日文引擎初始化完成")
                        self._engine = 'windows'
                        self._initialized = True
                        return
                logger.warning("Windows OCR 日文语言包未安装")
            except ImportError:
                logger.warning("winsdk 未安装，Windows OCR 不可用")
            except Exception as e:
                logger.warning(f"Windows OCR 初始化失败: {e}")

        if self._ocr is None:
            raise RuntimeError(
                "无可用的 OCR 引擎。请安装以下任一方案:\n"
                "  PaddleOCR: pip install paddleocr paddlepaddle\n"
                "  Windows OCR: 设置 → 语言 → 添加日本語 + 安装 winsdk\n"
            )

        self._initialized = True

    def recognize(self, image: Image.Image) -> List[Dict]:
        """
        对输入图像执行日文 OCR 识别。

        Args:
            image: PIL Image

        Returns:
            识别结果列表 [{id, text, confidence, bbox}, ...]
        """
        self._init_engine()

        if self._engine == 'paddleocr':
            return self._recognize_paddleocr(image)
        elif self._engine == 'windows':
            return self._recognize_windows(image)
        else:
            return self._recognize_paddleocr(image)

    # ============================================================
    # PaddleOCR 识别
    # ============================================================

    def _recognize_paddleocr(self, image: Image.Image) -> List[Dict]:
        """
        使用 PaddleOCR 进行识别。

        PaddleOCR 返回格式:
          [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ('text', confidence)]
        """
        img_np = np.array(image)
        raw_results = self._ocr.ocr(img_np)

        parsed = self._parse_paddleocr_results(raw_results)

        # 置信度过滤
        filtered = self._filter_by_confidence(parsed)

        # IOU 去重合并
        merged = self._merge_overlapping_boxes(filtered)

        # 重新编号
        for i, item in enumerate(merged, start=1):
            item['id'] = i

        logger.info(
            f"PaddleOCR: {len(parsed)} raw → {len(filtered)} filtered → {len(merged)} merged"
        )
        return merged

    def _parse_paddleocr_results(self, raw_results) -> List[Dict]:
        """解析 PaddleOCR 原始输出，过滤非术语文本。"""
        results = []
        if not raw_results or not raw_results[0]:
            return results

        for item in raw_results[0]:
            if item is None:
                continue
            bbox = item[0]
            text_info = item[1]
            if isinstance(text_info, (list, tuple)) and len(text_info) >= 2:
                text = str(text_info[0])
                conf = float(text_info[1])
            else:
                continue

            # ---- 过滤规则 ----
            # 1. 跳过纯数字/符号（如 "1", "001", "80.5-", "-"）
            import re
            # 移除数字、小数点、符号后检查是否还有内容
            stripped = re.sub(r'[\d\.\,\-\+\±\°\s -/:-@[-`]+', '', text)
            if len(stripped) < 2:
                continue

            # 2. 跳过单一日文假名作为独立"词"的情况
            # (机械图纸中单假名通常是标注符号，非术语)
            if len(text.strip()) == 1:
                continue

            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]

            results.append({
                'text': text,
                'confidence': round(conf, 4),
                'bbox': {
                    'min_x': round(min(xs), 1),
                    'min_y': round(min(ys), 1),
                    'max_x': round(max(xs), 1),
                    'max_y': round(max(ys), 1),
                },
            })
        return results

    # ============================================================
    # Windows OCR 识别
    # ============================================================

    def _recognize_windows(self, image: Image.Image) -> List[Dict]:
        """使用 Windows OCR 进行识别。"""
        import asyncio
        from winsdk.windows.graphics.imaging import (
            SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode,
        )

        if image.mode == 'RGBA':
            rgba = image.tobytes()
        elif image.mode == 'L':
            rgba = image.convert('RGBA').tobytes()
        else:
            rgba = image.convert('RGBA').tobytes()

        w, h = image.size
        bitmap = SoftwareBitmap.create_with_alpha_mode(
            BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED,
        )
        bitmap.copy_from_buffer(bytes(rgba))

        async def recognize():
            return await self._ocr.recognize_async(bitmap)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(recognize(), loop)
                result = future.result(timeout=30)
            else:
                result = loop.run_until_complete(recognize())
        except RuntimeError:
            result = asyncio.run(recognize())

        parsed = []
        for line in result.lines:
            line_text = line.text.strip()
            if not line_text or not line.words:
                continue

            r = line.words[0].bounding_rect
            min_x, min_y = r.x, r.y
            max_x, max_y = r.x, r.y

            for word in line.words:
                wr = word.bounding_rect
                min_x = min(min_x, wr.x)
                min_y = min(min_y, wr.y)
                max_x = max(max_x, wr.x + wr.width)
                max_y = max(max_y, wr.y + wr.height)

            parsed.append({
                'text': line_text,
                'confidence': 0.9,
                'bbox': {
                    'min_x': round(float(min_x), 1),
                    'min_y': round(float(min_y), 1),
                    'max_x': round(float(max_x), 1),
                    'max_y': round(float(max_y), 1),
                },
            })

        filtered = self._filter_by_confidence(parsed)
        merged = self._merge_overlapping_boxes(filtered)
        for i, item in enumerate(merged, start=1):
            item['id'] = i

        return merged

    # ============================================================
    # 通用后处理
    # ============================================================

    def _filter_by_confidence(self, results: List[Dict]) -> List[Dict]:
        threshold = self.config['min_confidence']
        return [r for r in results if r['confidence'] >= threshold]

    def _merge_overlapping_boxes(self, results: List[Dict]) -> List[Dict]:
        """基于 IOU 贪心合并重叠检测框。"""
        if len(results) <= 1:
            return results

        threshold = self.config['merge_iou_threshold']
        sorted_r = sorted(results, key=lambda x: x['confidence'], reverse=True)
        merged, skip = [], set()

        for i, a in enumerate(sorted_r):
            if i in skip:
                continue
            cur = {'text': a['text'], 'confidence': a['confidence'],
                   'bbox': dict(a['bbox'])}
            for j, b in enumerate(sorted_r):
                if j <= i or j in skip:
                    continue
                iou = self._calc_iou(cur['bbox'], b['bbox'])
                if iou >= threshold:
                    cur['bbox']['min_x'] = min(cur['bbox']['min_x'], b['bbox']['min_x'])
                    cur['bbox']['min_y'] = min(cur['bbox']['min_y'], b['bbox']['min_y'])
                    cur['bbox']['max_x'] = max(cur['bbox']['max_x'], b['bbox']['max_x'])
                    cur['bbox']['max_y'] = max(cur['bbox']['max_y'], b['bbox']['max_y'])
                    if b['confidence'] > cur['confidence']:
                        cur['text'] = b['text']
                        cur['confidence'] = b['confidence']
                    skip.add(j)
            merged.append(cur)
        return merged

    @staticmethod
    def _calc_iou(a: dict, b: dict) -> float:
        ix = max(0, min(a['max_x'], b['max_x']) - max(a['min_x'], b['min_x']))
        iy = max(0, min(a['max_y'], b['max_y']) - max(a['min_y'], b['min_y']))
        inter = ix * iy
        if inter == 0:
            return 0.0
        area_a = (a['max_x'] - a['min_x']) * (a['max_y'] - a['min_y'])
        area_b = (b['max_x'] - b['min_x']) * (b['max_y'] - b['min_y'])
        return inter / (area_a + area_b - inter)


# ============================================================
# 单例
# ============================================================
_engine_instance: Optional[OCREngine] = None


def get_engine(config: Optional[dict] = None) -> OCREngine:
    """获取 OCR 引擎单例。"""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = OCREngine(config)
    return _engine_instance
