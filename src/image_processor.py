"""
图像预处理模块 | Image Preprocessor Module
========================================
对图纸图像进行预处理，提升 OCR 识别准确率。

预处理流水线（可配置各步骤开关）:
  1. 灰度化 (Grayscale)      — 将彩色图像转为灰度，减少色彩干扰
  2. 降噪 (Denoise)          — 使用 Non-Local Means 降噪，去除图纸噪点
  3. 自适应二值化 (Adaptive Threshold) — 处理不均匀光照，分离文字与背景
  4. 旋转校正 (Deskew)       — 检测文本倾角并仿射变换水平对齐

原理说明:
  - 自适应二值化: 将图像分块，对每块分别计算阈值。相比全局二值化，
    对光照不均匀的扫描件效果更好。计算公式 (均值-C 方法):
    dst(x,y) = src(x,y) > mean(block) - C ? 255 : 0
  - NL-Means 降噪: 对每个像素，在邻域内搜索相似区域，加权平均后替换。
    相比高斯滤波，去噪同时更好地保留文字边缘。
  - 旋转校正: 通过霍夫线变换检测图像中的主要直线方向，
    计算其倾斜角度后使用仿射变换将图像旋转到水平位置。
"""

from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image


# ============================================================
# 预处理参数默认值
# ============================================================
DEFAULT_CONFIG = {
    'grayscale': True,             # 灰度化
    'denoise': True,               # 降噪
    'denoise_strength': 10,        # 降噪强度 (1-30, 越大越强但越慢)
    'adaptive_threshold': True,    # 自适应二值化
    'binary_block_size': 11,       # 二值化块大小 (奇数, 越大块越大)
    'binary_c': 2,                 # 二值化常数 C
    'deskew': True,                # 旋转校正
}


def preprocess(
    image: Image.Image,
    config: Optional[dict] = None,
) -> Image.Image:
    """
    对输入图像执行预处理流水线。

    Args:
        image: 输入 PIL Image
        config: 配置字典

    Returns:
        处理后的 PIL Image
    """
    if config is None:
        config = DEFAULT_CONFIG

    # ---- Step 0: 大图下采样（性能优化） ----
    # 若图像任一边超过 MAX_DIM，等比缩放到 MAX_DIM
    MAX_DIM = config.get('max_dimension', 3000)
    w, h = image.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        new_w, new_h = int(w * scale), int(h * scale)
        image = image.resize((new_w, new_h), Image.LANCZOS)

    # ---- 转为 OpenCV 格式处理 (BGR) ----
    img = pil_to_cv2(image)

    # Step 1: 灰度化
    if config.get('grayscale', True) and len(img.shape) == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Step 2: 降噪
    if config.get('denoise', True):
        strength = config.get('denoise_strength', 10)
        # 灰度图和彩色图使用不同的 NL-Means 函数
        if len(img.shape) == 2:
            # 灰度图降噪
            img = cv2.fastNlMeansDenoising(img, None, h=strength)
        else:
            # 彩色图降噪
            img = cv2.fastNlMeansDenoisingColored(img, None, h=strength, hColor=strength)

    # Step 3: 自适应二值化 (仅对灰度图)
    if config.get('adaptive_threshold', True) and len(img.shape) == 2:
        block_size = config.get('binary_block_size', 11)
        # 确保块大小为奇数
        if block_size % 2 == 0:
            block_size += 1
        c_value = config.get('binary_c', 2)

        img = cv2.adaptiveThreshold(
            img, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            c_value,
        )

    # Step 4: 旋转校正
    if config.get('deskew', True):
        angle = _detect_skew_angle(img)
        if abs(angle) > 0.5:  # 倾斜 > 0.5° 才校正
            img = _rotate_image(img, angle)

    # ---- 转回 PIL Image ----
    return cv2_to_pil(img)


def _detect_skew_angle(image: np.ndarray) -> float:
    """
    检测图像的倾斜角度。

    原理说明（霍夫线变换法）:
      1. 若图像非二值图，使用 Canny 边缘检测获取边缘图
      2. 使用霍夫线变换 (HoughLines) 检测所有直线段
      3. 统计所有直线的倾角中位数（排除极端角度）
      4. 返回中位数倾角作为校正角度

    Args:
        image: OpenCV 图像 (灰度或二值图)

    Returns:
        倾斜角度（度数），正值表示逆时针
    """
    # ---- 确保是二值图 ----
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # 若是二值图直接用，否则做 Canny 边缘检测
    if np.max(gray) <= 1 or set(np.unique(gray)).issubset({0, 255}):
        edges = gray
    else:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)

    # ---- 霍夫线变换检测直线 ----
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=100)

    if lines is None:
        return 0.0

    # ---- 统计倾角 ----
    angles = []
    for line in lines:
        rho, theta = line[0]
        # 将角度转为度数，并以水平为基准
        angle_deg = np.degrees(theta) - 90
        # 限制在 [-45, 45] 范围，排除垂直/水平极值
        if -45 < angle_deg < 45:
            angles.append(angle_deg)

    if not angles:
        return 0.0

    # ---- 使用中位数避免极端值干扰 ----
    median_angle = float(np.median(angles))

    return median_angle


def _rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    """
    按指定角度旋转图像，使用仿射变换保持图像内容完整。

    原理说明:
      仿射变换矩阵:
        M = [[cosθ, -sinθ, (1-cosθ)*cx + sinθ*cy],
             [sinθ,  cosθ, -sinθ*cx + (1-cosθ)*cy]]
      其中 (cx, cy) 为旋转中心。使用 cv2.warpAffine 执行。
      旋转后自动计算新的包围矩形尺寸，确保内容不丢失。

    Args:
        image: OpenCV 图像
        angle: 旋转角度（度数）, 正值为逆时针

    Returns:
        旋转后的图像
    """
    h, w = image.shape[:2]
    center = (w / 2, h / 2)

    # 获取旋转矩阵
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

    # 计算旋转后的新尺寸，确保内容完整
    cos = abs(rotation_matrix[0, 0])
    sin = abs(rotation_matrix[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)

    # 调整旋转矩阵的平移分量
    rotation_matrix[0, 2] += (new_w / 2) - center[0]
    rotation_matrix[1, 2] += (new_h / 2) - center[1]

    # 执行仿射变换
    rotated = cv2.warpAffine(
        image, rotation_matrix, (new_w, new_h),
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,  # 白色填充
    )

    return rotated


def pil_to_cv2(image: Image.Image) -> np.ndarray:
    """
    PIL Image → OpenCV 格式 (BGR)
    """
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    img_array = np.array(image)
    if len(img_array.shape) == 3 and img_array.shape[2] == 3:
        # RGB → BGR
        return cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    return img_array


def cv2_to_pil(image: np.ndarray) -> Image.Image:
    """
    OpenCV 格式 → PIL Image
    """
    if len(image.shape) == 2:
        # 灰度图
        return Image.fromarray(image, mode='L')
    else:
        # BGR → RGB
        return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def apply_preprocessing_for_bg(
    image: Image.Image,
    config: Optional[dict] = None,
) -> tuple:
    """
    对图纸进行双重预处理，返回两个版本:
      - 轻处理版: 仅灰度化+降噪，用于最终叠加标注
      - 重处理版: 完整预处理流水线，用于 OCR 识别

    这是为了在 OCR 时最大化识别率，同时在最终输出时保持视觉质量。

    Args:
        image: 原始 PIL Image
        config: 预处理配置

    Returns:
        (light_image, heavy_image): 两个 PIL Image 元组
    """
    if config is None:
        config = DEFAULT_CONFIG

    # 轻处理: 仅灰度化+降噪
    light_config = {
        **config,
        'adaptive_threshold': False,
        'deskew': False,
    }
    light = preprocess(image, light_config)

    # 重处理: 完整流水线
    heavy = preprocess(image, config)

    return light, heavy
