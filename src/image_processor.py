"""图像预处理：灰度化 → 降噪 → 自适应二值化 →(可选) 旋转校正。

图纸扫描件的特点：背景白、线条黑、光照可能有渐变。自适应二值化按块算
局部阈值，比全局阈值更能扛光照不均；NL-Means 降噪比高斯滤波更能保住
文字边缘（高斯会把细笔画一起糊掉）。
"""

import cv2
import numpy as np
from PIL import Image


def preprocess(image: Image.Image, config: dict) -> Image.Image:
    """按 config 里的开关跑一遍预处理流水线。

    识别的大图先缩到 max_dimension 以内再往下走；config 里没写
    max_dimension 就不缩。
    """
    max_dim = config.get('max_dimension')
    if max_dim:
        w, h = image.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            image = image.resize(
                (int(w * scale), int(h * scale)), Image.LANCZOS
            )

    img = pil_to_cv2(image)

    if config.get('grayscale', True) and len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    if config.get('denoise', True):
        strength = config.get('denoise_strength', 10)
        if len(img.shape) == 2:
            img = cv2.fastNlMeansDenoising(img, None, h=strength)
        else:
            img = cv2.fastNlMeansDenoisingColored(
                img, None, h=strength, hColor=strength
            )

    if config.get('adaptive_threshold', True) and len(img.shape) == 2:
        block_size = config.get('binary_block_size', 11)
        if block_size % 2 == 0:      # adaptiveThreshold 要求奇数
            block_size += 1
        img = cv2.adaptiveThreshold(
            img, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            config.get('binary_c', 2),
        )

    # 旋转校正默认关闭。_detect_skew_angle() 用霍夫变换求全体直线倾角的
    # 中位数，但机械图纸本身横竖斜线都有，没有主峰 —— 实测一张完全没有
    # 倾斜的图纸被它判成 -6°，转完之后 OCR 反而变差。
    # 图纸基本都是正着扫描/导出的，这一步行当上是在帮倒忙。
    if config.get('deskew', False):
        angle = _detect_skew_angle(img)
        if abs(angle) > 0.5:
            img = _rotate_image(img, angle)

    return cv2_to_pil(img)


def _detect_skew_angle(image: np.ndarray) -> float:
    """霍夫变换求直线倾角中位数。保留实现，但默认不启用（原因见上）。"""
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if np.max(gray) <= 1 or set(np.unique(gray)).issubset({0, 255}):
        edges = gray
    else:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=100)
    if lines is None:
        return 0.0

    # theta 是法线方向，(theta - 90) 才是直线相对水平的角度
    angles = []
    for line in lines:
        angle_deg = np.degrees(line[0][1]) - 90
        if -45 < angle_deg < 45:
            angles.append(angle_deg)

    return float(np.median(angles)) if angles else 0.0


def _rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    """绕中心旋转，画布放大到能装下整张图，空白填白。"""
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)

    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    new_w, new_h = int(h * sin + w * cos), int(h * cos + w * sin)

    m[0, 2] += (new_w / 2) - center[0]
    m[1, 2] += (new_h / 2) - center[1]

    return cv2.warpAffine(
        image, m, (new_w, new_h),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )


def pil_to_cv2(image: Image.Image) -> np.ndarray:
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    arr = np.array(image)
    if len(arr.shape) == 3 and arr.shape[2] == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return arr


def cv2_to_pil(image: np.ndarray) -> Image.Image:
    if len(image.shape) == 2:
        return Image.fromarray(image, mode='L')
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
