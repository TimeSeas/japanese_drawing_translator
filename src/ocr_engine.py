"""日文 OCR 封装：主力 PaddleOCR，装不上时退回 Windows 内置 OCR。

PaddleOCR 首次调用要下模型（几十 MB），所以引擎是懒加载的，建对象本身
很快，真正初始化推迟到第一次 recognize()。
"""

import logging
import re
from typing import Dict, List, Optional

import cv2
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

# 同一行的两个框，水平间隙小于这个倍数（×框高）就当成一句话被切开了。
# 实测：被切碎的片段之间间隙在 0.3 倍框高以内、甚至互相重叠；而真正独立的
# 两段标注，最近的一对也隔了 1.8 倍框高。1.0 卡在中间那道空档里。
_SAME_LINE_GAP_RATIO = 1.0

# 间隙里要有多高比例被一条竖线占满，才认定"这里是表格的一栏"，不接。
# 留 20% 余量是因为线宽不均、端点发虚。
_RULE_COVERAGE = 0.8

# 形态学开运算的结构元高度，按**文字行高**的倍数算，不是按图高。
#
# 这个尺度只能是"比一行字高多少"：判断的是"这够不够长、像不像一条表格线"，
# 参照物就是字。一开始按图高定（图高/30），在小图上会塌掉 —— 420px 高的
# 图上结构元只有 15px，比 32px 的字还矮，"バ"的竖笔画、"リ"的竖笔全都
# 留了下来，掩码里混进一堆字内的噪声。按字高算就跟分辨率无关了。
_RULE_MIN_LINE_HEIGHT = 2.0


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
        """返回 [{id, text, confidence, bbox, parts}, ...]，bbox 用 x/y 的 min/max。

        PaddleOCR 给的是四点多边形，这里压成轴对齐矩形——译文要按这个框
        叠回图上，矩形足够用。

        `parts` 是这条结果由哪几段拼起来的（没接过龙就只有一个元素）。它是
        给调用方在"整句查不到"时拆开重试用的中间产物，不是结果的一部分 ——
        写 JSON 之前要 pop 掉。见 translator / main_window 里的回退逻辑。
        """
        self._init()
        raw = (self._recognize_paddleocr(image)
               if self._backend == 'paddleocr'
               else self._recognize_windows(image))

        filtered = [r for r in raw if r['confidence'] >= self.min_confidence]
        overlapped = self._merge_overlapping(filtered)

        # 竖线掩码只在真的要接龙（两个框以上）时才算，单框的图省这一步。
        # 它是纯几何信息，只需要算一次，不用每对框都重算。
        rule_mask = (_vertical_rule_mask(np.array(image.convert('L')),
                                         _median_height(overlapped))
                     if len(overlapped) > 1 else None)
        merged = self._merge_same_line(overlapped, rule_mask)

        # 纯数字/符号的尺寸标注不是术语，滤掉。但要等接完行再滤 —— 先滤的话
        # 行中间会留个洞，把「9公差はJIS」和「中級による」这种同一句的两半
        # 拆成两个框，中间那 90px 的空档接龙接不上。
        #
        # 判据抽成 is_term_text() 是因为调用方拆碎片时要用同一个 —— 否则接龙
        # 拆开之后，"-"、"`-" 这些本来被这里滤掉的小碎片又会单独成条跑出来。
        results = [r for r in merged if is_term_text(r['text'])]

        for i, item in enumerate(results, start=1):
            item['id'] = i

        logger.info(f"OCR: {len(raw)} 原始 → {len(filtered)} 过阈 "
                    f"→ {len(merged)} 合并 → {len(results)} 去标注")
        return results

    def _recognize_paddleocr(self, image: Image.Image) -> List[Dict]:
        # PaddleOCR 返回 [[[[x,y]*4], (text, confidence)], ...]，外层还有一维
        results = []
        for item in self._ocr.ocr(np.array(image))[0] or []:
            if not item:
                continue
            box, (text, conf) = item[0], item[1]

            text = str(text)
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            results.append({
                'text': text,
                'confidence': round(float(conf), 4),
                'bbox': {
                    'min_x': round(min(xs), 1), 'min_y': round(min(ys), 1),
                    'max_x': round(max(xs), 1), 'max_y': round(max(ys), 1),
                },
                # 排向要在压成矩形**之前**判 —— 轴对齐的矩形已经把倾斜信息
                # 丢掉了，剩不下能判方向的量。
                'orientation': _orientation(box),
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
                # Windows OCR 只给轴对齐矩形，没有多边形，判不出排向。
                # 一律记横排 —— 是"判不了"，不是"判成横排"，只是这条
                # 退路上没法有更好的答案。
                'orientation': 'h',
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
                      'bbox': dict(a['bbox']),
                      'orientation': a.get('orientation', 'h')}
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

    def _merge_same_line(self, results: List[Dict],
                         rule_mask: Optional[np.ndarray] = None) -> List[Dict]:
        """把被切碎的一句话接回来。

        OCR 检测的单位是"文本行"，但图纸上同一行常被空格、表格线、引出线切成
        几段，检测器就吐几个框。每段单独拿去查词典基本查不到 —— 实测
        「②ランジクランプ穴はバリ取りのこと。」被切成「2ランジク」「ランプ穴はバ」
        「リ取リのこ」三段，三段都匹配失败，接回整句后才能对上词典里的整句词条。

        做法：先按行分组（同一行的框垂直方向必然重叠），行内按 x 排好，
        从左往右接龙，间隙小于一个框高就接上。日文词间不空格，所以直接拼。

        rule_mask 是竖线掩码（见 _vertical_rule_mask）。光看间隙大小分不出
        "一句话被切开"和"表格的两栏挨得近"—— 前者要接，后者不能接。有掩码
        时，间隙里横着一条竖线就不接。

        竖排和斜排的框不参与接龙，原样放回去。两个原因：
        (1) 接龙的尺度是框高（间隙 < 一个框高）。横排时框高约等于字号，
            这正是它想当的尺度；**竖排时框高是整列文字的长度**，尺度被撑大
            几十倍，半径大得能横跨半张图纸，会把不相干的东西串成一行。
        (2) 横排和竖排本来就不是"同一行文字"，接起来是硬凑。
        """
        if len(results) <= 1:
            return results

        # 判不出方向的（Windows OCR 那条退路）按横排处理，保持原行为
        horizontal = [r for r in results if r.get('orientation', 'h') == 'h']
        others = [r for r in results if r.get('orientation', 'h') != 'h']
        if len(horizontal) <= 1:
            return horizontal + others

        rows: List[List[Dict]] = []
        for item in sorted(horizontal, key=lambda r: _center_y(r['bbox'])):
            for row in rows:
                if any(_same_line(r['bbox'], item['bbox']) for r in row):
                    row.append(item)
                    break
            else:
                rows.append([item])

        merged = []
        for row in rows:
            row.sort(key=lambda r: r['bbox']['min_x'])
            current = _copy(row[0])
            for item in row[1:]:
                too_far = (_h_gap(current['bbox'], item['bbox'])
                           > _SAME_LINE_GAP_RATIO * _height(current['bbox']))
                if too_far or _split_by_rule(current['bbox'], item['bbox'],
                                             rule_mask):
                    merged.append(current)
                    current = _copy(item)
                else:
                    _join(current, item)
            merged.append(current)
        return merged + others


def _vertical_rule_mask(gray: np.ndarray, line_height: float) -> np.ndarray:
    """把长竖线单独抠出来，返回一张同尺寸掩码（是线的地方为 255）。

    经典形态学做法：拿到"笔画为白"的二值图，再用一个又高又窄的竖条结构元
    做开运算（erode→dilate）。结构元按字高定（line_height），比任何笔画都
    高，所以只有连续贯穿这个高度的竖线能活下来，文字、尺寸线、斜的引出线
    都被抹掉。

    注意预处理的输出**已经是二值图**（背景 255、笔画 0，见 image_processor
    的 THRESH_BINARY）。这时候再跑一次自适应二值化是没有意义的 —— 局部
    均值本来就是 255，"比均值暗"对全图成立，掩码会变成一整片白。实测踩过：
    91% 的像素被误判成竖线，于是每一次合并都被否决。所以先判断是不是已经
    二值化了。
    """
    if not np.all((gray == 0) | (gray == 255)):
        gray = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV, 15, 2)
    strokes = 255 - gray

    height = max(15, int(line_height * _RULE_MIN_LINE_HEIGHT))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, height))
    return cv2.morphologyEx(strokes, cv2.MORPH_OPEN, kernel)


def _median_height(boxes: List[Dict]) -> float:
    """同一批框里最典型的那个高度，当作"一行字有多高"。

    取中位数而不是平均：图纸上总有几个特别高或特别矮的框（竖排的一列、
    被切碎的半行），平均会被它们拽偏。
    """
    heights = sorted(_height(b['bbox']) for b in boxes)
    return heights[len(heights) // 2] if heights else 0.0


def _split_by_rule(a: Dict, b: Dict, mask: Optional[np.ndarray]) -> bool:
    """a、b 之间的空隙里，有没有一条竖线贯穿它们的高度。

    只看**竖**线，这是关键。碎片是被"空格、表格线、引出线"切开的（见
    _merge_same_line），其中引出线多是斜的 —— 开运算之后斜线剩不下几个
    像素，所以这条判据不会误砍"被引出线切开的一句话"，而那恰恰是要接上的。
    实测：demo 图上「②ランジクランプ穴はバリ取りのこと。」的四段碎片，
    间隙里的竖线覆盖率都是 0%。

    只做否决、不促成合并 —— 拿不准时退化成原来的纯算术行为，最坏也就是
    今天的样子。没有掩码时一律放行。
    """
    if mask is None:
        return False

    x0 = int(min(a['max_x'], b['max_x']))
    x1 = int(max(a['min_x'], b['min_x']))
    if x1 <= x0:
        return False                # 两框已经挨上或重叠，中间没有间隙

    y0 = int(min(a['min_y'], b['min_y']))
    y1 = int(max(a['max_y'], b['max_y']))
    if y1 <= y0:
        return False

    h, w = mask.shape
    band = mask[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    if band.size == 0:
        return False

    # 逐列数亮像素：空隙里只要有一列被竖线占掉大部分高度，就算隔着一条线
    return bool(band.sum(axis=0).max() >= _RULE_COVERAGE * (y1 - y0))


def _orientation(quad) -> str:
    """这条文字是横排('h')、竖排('v')还是斜的('rot')。

    用紧贴多边形的最小外接矩形和轴对齐矩形**面积之比**来判，不碰
    cv2.minAreaRect 的 angle —— 那个角度的正负号和取值范围在 OpenCV 4.5
    前后改过（以前是 [-90,0)，之后是 (0,90]），照着写很容易在换版本时
    悄悄判反，而且反了不报错。

    判据分两步，都不依赖角度约定：

      fill = 紧贴矩形面积 / 轴对齐矩形面积
      横平竖直时两者几乎相等（fill≈1）；斜着写时轴对齐矩形会胀大一圈，
      fill 明显小于 1。实测 100×30 的框，偏 4° 时 fill≈0.80。

      紧贴矩形接近正方形时判不出来（单个字就长这样），按横排算。

    再比一下紧贴矩形的长边落在哪个轴上，落到 h 还是 v。
    """
    pts = np.array(quad, dtype=np.float32)
    (_, _), (w, h), _ = cv2.minAreaRect(pts)

    long_side, short_side = max(w, h), min(w, h)
    if short_side <= 0 or long_side < 1.3 * short_side:
        return 'h'              # 接近正方形，多半是单个字，判不出方向

    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    bbox_w = max(xs) - min(xs)
    bbox_h = max(ys) - min(ys)
    if bbox_w <= 0 or bbox_h <= 0:
        return 'h'

    # 斜着写的框，轴对齐矩形会比紧贴矩形大一圈，靠这个把"斜"分出来
    if (w * h) / (bbox_w * bbox_h) < 0.8:
        return 'rot'

    return 'h' if bbox_w >= bbox_h else 'v'


def is_term_text(text: str) -> bool:
    """这行够不够当一条术语 —— 去掉数字/符号/单位之后还剩至少两个字。

    纯数字符号的是尺寸标注（"80.5-"、"R0.3"、"±0.005"），不是术语。
    """
    return len(_NON_TERM_CHARS.sub('', text)) >= 2


def _height(box: Dict) -> float:
    return box['max_y'] - box['min_y']


def _center_y(box: Dict) -> float:
    return (box['min_y'] + box['max_y']) / 2


def _h_gap(a: Dict, b: Dict) -> float:
    """两个框的水平间隙，负数代表已经重叠。"""
    return max(a['min_x'], b['min_x']) - min(a['max_x'], b['max_x'])


def _same_line(a: Dict, b: Dict) -> bool:
    """垂直方向重叠超过较矮那个的一半，就算同一行。"""
    overlap = min(a['max_y'], b['max_y']) - max(a['min_y'], b['min_y'])
    return overlap > 0.5 * min(_height(a), _height(b))


def _copy(r: Dict) -> Dict:
    """复制一份，同时记下它自己就是第一个碎片。

    `parts` 是给"接龙失败"留的退路：接成整句之后如果词典里查不到，调用方
    可以拆回这几个碎片各自再查一次，每片带自己的 bbox 画回原位。没有它的话，
    接龙就是单向的 —— 接完查不到，那三段就一起变成未翻译，连原本能翻的那段
    也跟着丢了。
    """
    return {'text': r['text'], 'confidence': r['confidence'],
            'bbox': dict(r['bbox']),
            'orientation': r.get('orientation', 'h'),
            'parts': [{'text': r['text'], 'confidence': r['confidence'],
                       'bbox': dict(r['bbox'])}]}


def _join(a: Dict, b: Dict):
    """把右边的 b 接到 a 后面。置信度取两者中低的 —— 整句准不准，取决于
    认得最差的那一段。

    b 连文本带自己的 bbox 一起记进 a['parts']（见 _copy 的说明）。注意这里
    必须存**原始的** bbox，不能用 a 那个不断长大的并集框 —— 拆回去的时候
    每片要画在自己原来的位置上。
    """
    a['text'] += b['text']
    a['confidence'] = min(a['confidence'], b['confidence'])
    a['bbox']['min_x'] = min(a['bbox']['min_x'], b['bbox']['min_x'])
    a['bbox']['min_y'] = min(a['bbox']['min_y'], b['bbox']['min_y'])
    a['bbox']['max_x'] = max(a['bbox']['max_x'], b['bbox']['max_x'])
    a['bbox']['max_y'] = max(a['bbox']['max_y'], b['bbox']['max_y'])
    a['parts'].append({'text': b['text'], 'confidence': b['confidence'],
                       'bbox': dict(b['bbox'])})


def _iou(a: Dict, b: Dict) -> float:
    ix = max(0, min(a['max_x'], b['max_x']) - max(a['min_x'], b['min_x']))
    iy = max(0, min(a['max_y'], b['max_y']) - max(a['min_y'], b['min_y']))
    inter = ix * iy
    if inter == 0:
        return 0.0
    area = ((a['max_x'] - a['min_x']) * (a['max_y'] - a['min_y'])
            + (b['max_x'] - b['min_x']) * (b['max_y'] - b['min_y']) - inter)
    return inter / area
