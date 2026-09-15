"""把 PDF / JPG / PNG / BMP 统一读成 PIL Image 列表。

PDF 走 pdf2image（底层是 poppler 的 pdftoppm），按 300 DPI 渲染成像素图。
300 DPI 是权衡：图纸上的注记字号很小，低于这个识别率掉得厉害；再高则
一张图几百 MB，PaddleOCR 跑不动。
"""

import io
from pathlib import Path
from typing import List, Optional, Tuple

import pypdf
from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp'}
PDF_EXTENSIONS = {'.pdf'}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS


def parse_file(file_path: str, dpi: int = 300) -> Tuple[List[Image.Image], str]:
    """返回 (每页图像, 不含扩展名的文件名)。"""
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"文件是空的: {file_path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"不支持的格式 {ext}，只认 {'/'.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    try:
        images = (parse_pdf(file_path, dpi) if ext in PDF_EXTENSIONS
                  else [parse_image(file_path)])
    except Exception as e:
        raise ValueError(f"解析失败，文件可能损坏: {file_path}\n{e}")

    return images, path.stem


def parse_pdf(file_path: str, dpi: int = 300) -> List[Image.Image]:
    _check_pdf_readable(file_path)

    try:
        from pdf2image import convert_from_path
    except ImportError:
        return _extract_embedded_images(file_path)

    images = convert_from_path(file_path, dpi=dpi, grayscale=False,
                               use_cropbox=False, thread_count=1)
    if not images:
        raise ValueError("PDF 渲染出来是空的")
    return images


def _check_pdf_readable(file_path: str):
    """先让 pypdf 读一遍头部。加密或损坏的 PDF 在这里就报错，
    不用等 poppler 起子进程之后再失败。"""
    try:
        if len(pypdf.PdfReader(file_path).pages) == 0:
            raise ValueError("PDF 里没有页面")
    except pypdf.errors.PdfReadError as e:
        raise ValueError(f"PDF 损坏或加密: {e}")


def _extract_embedded_images(file_path: str) -> List[Image.Image]:
    """没装 pdf2image（或 poppler 不在 PATH）时的退路。

    直接从 PDF 里抠嵌入的位图，跳过页面渲染这一步。扫描件能用——
    整页本来就是一张图；纯文本型 PDF 抠不出东西，会抛错说明情况。
    """
    images = []
    for page in pypdf.PdfReader(file_path).pages:
        resources = page.get('/Resources')
        if not resources or '/XObject' not in resources:
            continue

        xobjects = resources['/XObject'].get_object()
        candidates = []
        for name in xobjects:
            obj = xobjects[name].get_object()
            if obj.get('/Subtype') != '/Image':
                continue
            try:
                data = obj.get_data()
                if '/DCTDecode' in str(obj.get('/Filter', '')):
                    candidates.append(Image.open(io.BytesIO(data)))
                else:   # 没压缩的原始位图，按色彩空间猜模式
                    mode = ('RGB' if '/DeviceRGB' in str(obj.get('/ColorSpace'))
                            else 'L')
                    candidates.append(Image.frombytes(
                        mode, (obj['/Width'], obj['/Height']), data))
            except Exception:
                continue

        if candidates:      # 一页上可能有好几张图，取最大的那张当整页
            images.append(max(candidates, key=lambda im: im.width * im.height))

    if not images:
        raise ValueError(
            "抠不出嵌入图像。请安装 pdf2image 并确保 poppler 在 PATH 里，"
            "或换一张扫描件试试。"
        )
    return images


def parse_image(file_path: str) -> Image.Image:
    """读单张图。不做任何缩放或压缩，原样交给后续环节。"""
    try:
        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img)   # 手机拍的图方向信息在 EXIF 里

        img.verify()                          # verify() 之后对象就废了，得重开
        img = ImageOps.exif_transpose(Image.open(file_path))
        img.load()
        return img
    except (IOError, OSError) as e:
        raise ValueError(f"打不开这张图: {file_path}\n{e}")


def get_file_info(file_path: str) -> dict:
    """给状态栏显示用：文件名、格式、大小、页数、尺寸。"""
    path = Path(file_path)
    info = {
        'filename': path.name,
        'format': path.suffix.lstrip('.').upper(),
        'size_kb': round(path.stat().st_size / 1024, 1),
        'page_count': 1,
        'width': 0,
        'height': 0,
    }

    try:
        if path.suffix.lower() in PDF_EXTENSIONS:
            reader = pypdf.PdfReader(file_path)
            info['page_count'] = len(reader.pages)
            if reader.pages:      # PDF 页面尺寸是 pt，不是像素
                box = reader.pages[0].mediabox
                info['width'] = round(float(box.width), 1)
                info['height'] = round(float(box.height), 1)
        else:
            with Image.open(file_path) as img:
                info['width'], info['height'] = img.size
    except Exception:
        pass    # 状态栏信息拿不到就算了，不该拦着主流程

    return info
