"""日文 OCR 封装：主力 PaddleOCR，装不上时退回 Windows 内置 OCR。

PaddleOCR 首次调用要下模型（几十 MB），所以引擎是懒加载的，建对象本身
很快，真正初始化推迟到第一次 recognize()。
"""

import logging
import re
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 只剩数字/符号/单位的文本块，都是尺寸标注而不是术语。
# 例："80.5-"、"R0.3"、"-"、"±0.005"、"t=5" 去掉这些字符后就空了。
_NON_TERM_CHARS = re.compile(
    r'[\d\.\,\-\+\±\°\s'
    r' -/:-@[-`'   # 空格~斜杠、冒号~@、[~`
    r']+'
)


class OCREngine:
    def __init__(self, lang: str = 'japan', min_confidence: float = 0.5,
                 merge_iou_threshold: float = 0.8):
        self.lang = lang
        self.min_confidence = min_confidence
        self.merge_iou_threshold = merge_iou_threshold
        self._ocr = None
        self._backend = None

    def _init(self):
        if self._backend:
            return

        try:
            from paddleocr import PaddleOCR
            logger.info("初始化 PaddleOCR 日文模型…")
            # use_angle_cls=False：方向分类器是给自然场景照片用的，
            # 图纸不会倒着扫，开着只是白搭一倍时间。
            self._ocr = PaddleOCR(
                lang=self.lang, use_angle_cls=False, ocr_version='PP-OCRv4'
            )
            self._backend = 'paddleocr'
            logger.info("PaddleOCR 就绪")
            return
        except Exception as e:
            logger.warning(f"PaddleOCR 不可用: {e}")

        try:
            from winsdk.windows.globalization import Language
            from winsdk.windows.media.ocr import OcrEngine as WinOcr

            ja = Language('ja')
            if not WinOcr.is_language_supported(ja):
                raise RuntimeError("系统未安装日文 OCR 语言包")

            self._ocr = WinOcr.try_create_from_language(ja)
            if not self._ocr:
                raise RuntimeError("创建日文 OCR 引擎失败")

            self._backend = 'windows'
            logger.info("Windows OCR 日文引擎就绪")
            return
        except Exception as e:
            logger.warning(f"Windows OCR 不可用: {e}")

        raise RuntimeError(
            "没有可用的 OCR 引擎。装 PaddleOCR（pip install paddleocr "
            "paddlepaddle），或在 设置→语言 里加日文语言包后 pip install winsdk。"
        )

    def recognize(self, image: Image.Image) -> List[Dict]:
        """返回 [{id, text, confidence, bbox}, ...]，bbox 用 x/y 的 min/max。

        PaddleOCR 给的是四点多边形，这里压成轴对齐矩形——译文要按这个框
        叠回图上，矩形足够用。
        """
        self._init()
        raw = (self._recognize_paddleocr(image)
               if self._backend == 'paddleocr'
               else self._recognize_windows(image))

        filtered = [r for r in raw if r['confidence'] >= self.min_confidence]
        merged = self._merge_overlapping(filtered)

        for i, item in enumerate(merged, start=1):
            item['id'] = i

        logger.info(f"OCR: {len(raw)} 原始 → {len(filtered)} 过阈 "
                    f"→ {len(merged)} 合并")
        return merged

    def _recognize_paddleocr(self, image: Image.Image) -> List[Dict]:
        # PaddleOCR 返回 [[[[x,y]*4], (text, confidence)], ...]，外层还有一维
        results = []
        for item in self._ocr.ocr(np.array(image))[0] or []:
            if not item:
                continue
            box, (text, conf) = item[0], item[1]

            text = str(text)
            # 纯数字/符号块是尺寸标注；单个假名是标注符号，都不是术语
            if len(_NON_TERM_CHARS.sub('', text)) < 2:
                continue

            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            results.append({
                'text': text,
                'confidence': round(float(conf), 4),
                'bbox': {
                    'min_x': round(min(xs), 1), 'min_y': round(min(ys), 1),
                    'max_x': round(max(xs), 1), 'max_y': round(max(ys), 1),
                },
            })
        return results

    def _recognize_windows(self, image: Image.Image) -> List[Dict]:
        """退回方案。Windows OCR 不吐置信度，这里统一记 1.0 —— 它给的
        是整行结果，没有可比的分数字段。"""
        import asyncio

        from winsdk.windows.graphics.imaging import (
            BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap,
        )

        w, h = image.size
        bitmap = SoftwareBitmap.create_with_alpha_mode(
            BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED
        )
        bitmap.copy_from_buffer(image.convert('RGBA').tobytes())

        result = asyncio.run(self._ocr.recognize_async(bitmap))

        lines = []
        for line in result.lines:
            if not line.text.strip() or not line.words:
                continue
            rects = [wd.bounding_rect for wd in line.words]
            lines.append({
                'text': line.text.strip(),
                'confidence': 1.0,
                'bbox': {
                    'min_x': round(min(r.x for r in rects), 1),
                    'min_y': round(min(r.y for r in rects), 1),
                    'max_x': round(max(r.x + r.width for r in rects), 1),
                    'max_y': round(max(r.y + r.height for r in rects), 1),
                },
            })
        return lines

    def _merge_overlapping(self, results: List[Dict]) -> List[Dict]:
        """把几乎重合的框并成一个。

        二值化之后笔画会粘连，检测器有时对同一处文字吐出两个框。按置信度
        从高到低扫，IoU 超过阈值就并进去，并的时候取两者外接矩形——宁可框
        大一点，也不要让译文只盖住半行字。
        """
        if len(results) <= 1:
            return results

        ordered = sorted(results, key=lambda r: r['confidence'], reverse=True)
        kept, dropped = [], set()

        for i, a in enumerate(ordered):
            if i in dropped:
                continue
            merged = {'text': a['text'], 'confidence': a['confidence'],
                      'bbox': dict(a['bbox'])}
            for j in range(i + 1, len(ordered)):
                if j in dropped:
                    continue
                b = ordered[j]
                if _iou(merged['bbox'], b['bbox']) < self.merge_iou_threshold:
                    continue
                for k in ('min_x', 'min_y'):
                    merged['bbox'][k] = min(merged['bbox'][k], b['bbox'][k])
                for k in ('max_x', 'max_y'):
                    merged['bbox'][k] = max(merged['bbox'][k], b['bbox'][k])
                if b['confidence'] > merged['confidence']:
                    merged['text'] = b['text']
                    merged['confidence'] = b['confidence']
                dropped.add(j)
            kept.append(merged)
        return kept


def _iou(a: Dict, b: Dict) -> float:
    ix = max(0, min(a['max_x'], b['max_x']) - max(a['min_x'], b['min_x']))
    iy = max(0, min(a['max_y'], b['max_y']) - max(a['min_y'], b['min_y']))
    inter = ix * iy
    if inter == 0:
        return 0.0
    area = ((a['max_x'] - a['min_x']) * (a['max_y'] - a['min_y'])
            + (b['max_x'] - b['min_x']) * (b['max_y'] - b['min_y']) - inter)
    return inter / area
