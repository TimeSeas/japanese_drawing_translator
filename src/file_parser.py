"""
文件解析模块 | File Parser Module
=============================
负责将各种输入格式统一转换为 PIL Image 列表。
支持格式: PDF（含扫描件/图像型）、JPG、PNG、BMP

原理说明：
- PDF 使用 pdf2image 库逐页渲染为 PIL Image，保持原始分辨率和色彩空间
- 图像文件使用 Pillow 直接加载，不进行任何压缩或色彩空间转换
- 所有文件读取均包含异常捕获，损坏文件弹出友好提示而非崩溃
"""

import os
from pathlib import Path
from typing import List, Tuple, Optional

from PIL import Image
import pypdf


# ============================================================
# 支持的图像文件扩展名
# ============================================================
SUPPORTED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp'}
SUPPORTED_PDF_EXTENSIONS = {'.pdf'}

# 支持的文件扩展名（全部）
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_PDF_EXTENSIONS


def parse_file(file_path: str, dpi: int = 300) -> Tuple[List[Image.Image], str]:
    """
    解析文件，返回 PIL Image 列表和文件名。

    根据文件扩展名自动选择解析方式：
      - .pdf → 调用 parse_pdf() 逐页渲染
      - .jpg/.png/.bmp → 调用 parse_image() 直接加载

    Args:
        file_path: 文件路径
        dpi: PDF 渲染分辨率（仅对 PDF 有效），默认 300 DPI

    Returns:
        (images, filename): images 为 PIL Image 列表, filename 为不含扩展名的文件名

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 不支持的或损坏的文件格式
    """
    path = Path(file_path)

    # ---- 检查文件是否存在 ----
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # ---- 检查是否为空文件 ----
    if path.stat().st_size == 0:
        raise ValueError(f"文件为空 (0 字节): {file_path}")

    ext = path.suffix.lower()
    filename = path.stem

    # ---- 验证文件格式 ----
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的文件格式: {ext}\n"
            f"支持的格式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # ---- 根据格式分发处理 ----
    try:
        if ext in SUPPORTED_PDF_EXTENSIONS:
            return parse_pdf(file_path, dpi=dpi), filename
        else:
            return [parse_image(file_path)], filename

    except Exception as e:
        # 将所有异常包装为友好消息
        raise ValueError(f"无法解析文件，文件可能已损坏。\n文件: {file_path}\n详情: {str(e)}")


def parse_pdf(file_path: str, dpi: int = 300) -> List[Image.Image]:
    """
    将 PDF 文件逐页转换为 PIL Image 列表。

    处理流程:
      1. 使用 pypdf 读取 PDF，检测是否为加密/损坏文件
      2. 逐页使用 pdf2image 渲染为 PIL Image
      3. 保持原始色彩空间（RGB/CMYK/Grayscale）
      4. 若 pdf2image 不可用，回退到 pypdf 内置提取（仅限文本型 PDF）

    原理说明 (pdf2image):
      pdf2image 封装了 poppler 的 pdftoppm 工具，通过子进程调用将 PDF
      每页渲染为 PPM/PPM 格式的像素图，然后由 Pillow 加载为 PIL Image。
      渲染 DPI 控制了输出图像的分辨率: pixel_size = page_size_inch × dpi。

    Args:
        file_path: PDF 文件路径
        dpi: 渲染分辨率，默认 300

    Returns:
        PIL Image 列表，每个元素对应一页 PDF

    Raises:
        ValueError: PDF 损坏或加密无法读取
    """
    # ---- 预检: 使用 pypdf 验证 PDF 完整性 ----
    try:
        pypdf_reader = pypdf.PdfReader(file_path)
        total_pages = len(pypdf_reader.pages)

        if total_pages == 0:
            raise ValueError("PDF 不包含任何页面")

    except pypdf.errors.PdfReadError as e:
        raise ValueError(f"PDF 文件解析失败（可能已损坏或加密）: {str(e)}")

    # ---- 使用 pdf2image 渲染 ----
    try:
        from pdf2image import convert_from_path

        images = convert_from_path(
            file_path,
            dpi=dpi,
            # 保持原始色彩空间
            grayscale=False,
            # 不使用裁剪
            use_cropbox=False,
            # 单线程渲染（避免并发问题）
            thread_count=1,
        )

        if not images:
            raise ValueError("PDF 渲染结果为空")

        return images

    except ImportError:
        # pdf2image 不可用时的回退方案
        # 仅能处理包含嵌入图像的 PDF（如扫描件），不能处理文本型 PDF
        images = []
        for page in pypdf_reader.pages:
            page_images = []
            if '/XObject' in page['/Resources']:
                xObject = page['/Resources']['/XObject'].get_object()
                for obj_name in xObject:
                    obj = xObject[obj_name].get_object()
                    if obj['/Subtype'] == '/Image':
                        # 从 PDF 流中提取原始图像数据
                        try:
                            from PIL import Image as PILImage
                            import io
                            size = (obj['/Width'], obj['/Height'])
                            data = obj.get_data()
                            if '/Filter' in obj and '/DCTDecode' in str(obj['/Filter']):
                                img = PILImage.open(io.BytesIO(data))
                            else:
                                if '/ColorSpace' in obj and '/DeviceRGB' in str(obj['/ColorSpace']):
                                    mode = "RGB"
                                else:
                                    mode = "L"
                                img = PILImage.frombytes(mode, size, data)
                            page_images.append(img)
                        except Exception:
                            continue

            if page_images:
                # 若有嵌入图像，使用最大的那张
                images.append(max(page_images, key=lambda x: x.width * x.height))

        if not images:
            raise ValueError(
                "PDF 渲染失败: pdf2image 未安装，且 PDF 中无可提取的嵌入图像。\n"
                "请安装 pdf2image: pip install pdf2image\n"
                "并确保 poppler 已安装并添加到 PATH。"
            )

        return images


def parse_image(file_path: str) -> Image.Image:
    """
    加载单个图像文件。

    保持原始分辨率、色彩空间和视觉比例不变，无压缩失真或拉伸变形。

    原理说明:
      使用 Pillow 直接打开图像文件，不进行任何 resize、convert 或 compress
      操作。Pillow 会保留 EXIF 方向信息并自动纠正图像旋转。

    Args:
        file_path: 图像文件路径

    Returns:
        PIL Image 对象

    Raises:
        ValueError: 图像损坏或格式不匹配
    """
    try:
        img = Image.open(file_path)

        # ---- 处理 EXIF 旋转 ----
        # 某些相机/软件保存的图像需要根据 EXIF 方向标签旋转
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)

        # ---- 验证图像有效性 ----
        img.verify()

        # verify() 后需要重新打开
        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img)

        # ---- 确保图像在内存中加载 ----
        img.load()

        return img

    except (IOError, OSError) as e:
        raise ValueError(
            f"图像文件无法打开，可能已损坏或格式不匹配。\n"
            f"文件: {file_path}\n"
            f"详情: {str(e)}"
        )


def get_file_info(file_path: str) -> dict:
    """
    获取文件的基本信息，用于 UI 展示。

    Returns:
        dict: {
            'filename': 文件名,
            'format': 格式,
            'size_kb': 文件大小(KB),
            'page_count': 页数(PDF) 或 1(图像),
            'width': 宽度(像素),
            'height': 高度(像素),
        }
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    size_kb = path.stat().st_size / 1024

    info = {
        'filename': path.name,
        'format': ext[1:].upper(),
        'size_kb': round(size_kb, 1),
        'page_count': 1,
        'width': 0,
        'height': 0,
    }

    try:
        if ext in SUPPORTED_PDF_EXTENSIONS:
            reader = pypdf.PdfReader(file_path)
            info['page_count'] = len(reader.pages)
            # PDF 页面尺寸从第一页获取
            if len(reader.pages) > 0:
                page = reader.pages[0]
                mediabox = page.mediabox
                info['width'] = round(float(mediabox.width), 1)
                info['height'] = round(float(mediabox.height), 1)
        else:
            img = Image.open(file_path)
            info['width'] = img.width
            info['height'] = img.height
            img.close()
    except Exception:
        pass  # 信息获取失败时不抛出异常，仅返回基本信息

    return info
