# -*- coding: utf-8 -*-
"""实验：预处理参数对 OCR 质量的影响（两因素 2x2）

背景
----
应用的预处理流水线有两个可疑步骤：

  1. 下采样（image_processor.py:60-67）
     大图等比缩到 max_dimension 以内。当前值 3000 是**硬编码兜底**，
     因为 config.yaml 从未被传进任何模块（见下方"配置链路"）。
     源图纸 6350x4490 —— 等于跑 OCR 前先砍掉一半分辨率。

  2. 倾斜校正（image_processor.py:103-107）
     _detect_skew_angle() 用霍夫变换求所有直线倾角的**中位数**，
     只要 |角度| > 0.5° 就旋转整张图。

     实测：演示图纸_カバー.png 检测出 -6.000°，大图检测出 -5.000°。
     但**图纸根本没有倾斜**（边框/标题栏/表格肉眼确认严格水平）。
     角度是整数（-6.000 而非 -6.234），因为 HoughLines 的角度分辨率
     就是 1°（np.pi/180），中位数落在了量化格点上。
     7141 条线通过过滤，最多的角度只占 4.7% —— 是噪声分布，没有主峰。

本脚本**不改任何源文件**，只在调用 preprocess() 时覆盖这两个参数，
2x2 对比：max_dimension {3000, 6400} × deskew {True, False}。

为什么改 config.yaml 没用（配置链路）
-------------------------------------
    main.py:163          读了 config.yaml 存进局部变量，再没传给任何人
    main_window.py:569   创建 ProcessingWorker 时不传 preprocess_config
    main_window.py:69    → 兜底到 PP_DEFAULT
    image_processor.py:31  PP_DEFAULT 里**根本没有 max_dimension 键**
    image_processor.py:62  → config.get('max_dimension', 3000) 取兜底值
    main_window.py:83,194  get_engine() 不传参 → 用的是 paddleocr 而非
                           config.yaml 里写的 easyocr（且 easyocr 根本没装）

判读原则
--------
不看"平均准确率"，只看**已知答案的那几条**现在对不对。
对照组：小图（2000x1400）在 max_dimension 两档下应完全一致 ——
若不一致，说明实验不止动了一个变量，结论不可信。

已知答案（Ground Truth）
-----------------------
下面是我从 演示图纸_カバー.png 上**肉眼读出**的原文，用来判读对错。
⚠️ 我是从缩略图读的，你务必打开原图逐条核对一遍再当标准答案用。

    ①指示無き凸部はC0.5maxとする。
    ②ランジクランプ穴はバリ取リのこと。
    ③印加工部は、ピッチ寸法公差±0.005とする。
    ④EWC加工部の寸法形状はdxfデータ参照のこと。
    ⑤柱穴加工部にシンプを圧入のこと。
    ⑥全ての角部はR0.3以下とする。
    ⑦穴加工部は面取リのこと。
    ⑧バリ・カエリは完全に除去のこと。
    ⑨公差はJIS B 0405 中級による。
    表面処理：黒染め ／ 表面処理：アルマイト
    部品名 カバー ／ 材質 SUS304 ／ 尺度 1:2 ／ 作成 技術部
    正面図 ／ 側面図 ／ 注記 ／ シール面 ／ t=5

用法
----
    cd japanese_drawing_translator
    C:\\ProgramData\\Anaconda3\\python.exe tools\\exp_downsample.py
"""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Windows 控制台默认 cp936，而乱码样本里含有 cp936 编不出来的字符
# （如 'đ' U+0111）。不重设编码，print 那些文本会直接
# UnicodeEncodeError 崩掉 —— 而"打印乱码"恰恰是本实验的目的。
# ---------------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]      # tools/ 的上一级 = 项目根
sys.path.insert(0, str(ROOT))                   # 让 import src.xxx 能找到

from src.file_parser import parse_file
from src.image_processor import preprocess, DEFAULT_CONFIG
from src.ocr_engine import get_engine

# ===========================================================================
# 实验参数（要改就改这里）
# ===========================================================================

OUTER = ROOT.parent          # ...\日文零件图纸术语自动翻译功能\

IMAGES = [
    # (路径, 说明)
    (OUTER / "日文零件图纸术语自动翻译功能" / "20231113162902978.翻译参考_01(1).png",
     "大图 6350x4490（触发下采样）"),
    (OUTER / "演示图纸_カバー.png",
     "小图 2000x1400（不触发下采样 → 下采样轴的对照组）"),
]

MAX_DIMS = [3000, 6400]          # 3000 = 现状； 6400 = 等于关闭下采样
DESKEWS = [True, False]          # True = 现状（会转过 5~6°）

# 重点追踪的词：看它们在四种组合下分别被识别成什么
WATCH_WORDS = ["公差", "除去", "面取", "加工部", "参照", "部品", "材質", "硬度", "ダイセット"]

OUT_DIR = ROOT / "output" / "exp_downsample"

BAR = "=" * 62


# ===========================================================================
# 工具
# ===========================================================================
def _jsonable(value):
    """把 numpy 标量递归转成 Python 原生类型。

    为什么需要：json.dumps 不认识 numpy 类型，会直接抛
        TypeError: Object of type float32 is not JSON serializable

    实测 round(np.float32(0.9312), 4) 返回的仍然是 numpy.float32 ——
    round() 并不负责转换类型。而 ocr_engine 里 confidence 有显式
    float()（:175），bbox 坐标却是从 numpy 数组直接取的（:207-210），
    所以这里统一兜一道，省得写盘时才炸。
    """
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):          # numpy 标量都有 .item()
        return value.item()
    return value


def _short(text: str, limit: int = 40) -> str:
    """截断显示，太长的一行打不下。"""
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _tag(max_dim: int, deskew: bool) -> str:
    return f"md{max_dim}/{'转' if deskew else '不转'}"


# ===========================================================================
# 单次运行
# ===========================================================================
def run_once(engine, image, max_dim: int, deskew: bool) -> dict:
    """在指定参数组合下跑一遍 预处理 + OCR。

    关键点：config 用 {**DEFAULT_CONFIG, ...} 而不是只传要改的两个键。
    只传两个键的话，其余参数（灰度/降噪/二值化/…）会取不到值、
    各自走代码内部兜底 —— 那实验就不是单变量了。
    """
    config = {**DEFAULT_CONFIG, "max_dimension": max_dim, "deskew": deskew}

    # 下采样是否触发，要在预处理**之前**判断 —— 预处理会改尺寸，
    # 拿处理后的尺寸和原始尺寸比是错的（deskew 旋转也会改变尺寸）。
    will_downsample = max(image.size) > max_dim

    t0 = time.time()
    processed = preprocess(image, config)
    t1 = time.time()

    results = engine.recognize(processed)
    t2 = time.time()

    return {
        "max_dimension": max_dim,
        "deskew": deskew,
        "original_size": list(image.size),
        "processed_size": list(processed.size),
        "downsampled": will_downsample,     # ← 只看 max_dimension，与旋转无关
        "preprocess_sec": round(t1 - t0, 2),
        "ocr_sec": round(t2 - t1, 2),
        "count": len(results),
        "items": _jsonable(results),
        "texts": [r["text"] for r in results],
    }


# ===========================================================================
# 对比
# ===========================================================================
def report(image_label: str, runs: list[dict]) -> None:
    print()
    print(BAR)
    print(f"图片：{image_label}")
    print(BAR)

    for run in runs:
        size = "x".join(str(v) for v in run["processed_size"])
        flag = "已下采样" if run["downsampled"] else "未下采样"
        print(f"  [{_tag(run['max_dimension'], run['deskew']):<12}]"
              f" 处理后 {size:<12} {flag:<7}"
              f" 预处理 {run['preprocess_sec']:>5.2f}s"
              f" OCR {run['ocr_sec']:>6.2f}s"
              f"  识别 {run['count']:>3} 条")

    # ---- 下采样轴的对照组检验 ----
    # 小图不该触发下采样，所以 md3000 和 md6400 应当**逐字一致**。
    # 若不一致 → 有别的变量在动 → 全部结论不可信。
    for deskew in DESKEWS:
        pair = [r for r in runs if r["deskew"] is deskew]
        if len(pair) == 2 and not any(r["downsampled"] for r in pair):
            same = set(pair[0]["texts"]) == set(pair[1]["texts"])
            print(f"\n  [对照检验] deskew={deskew} 下 max_dimension 两档"
                  f" → {'一致 ✓（单变量成立）' if same else '不一致 ✗（有别的变量在动）'}")

    # ---- 两两差异 ----
    print()
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a, b = runs[i], runs[j]
            only_a = set(a["texts"]) - set(b["texts"])
            only_b = set(b["texts"]) - set(a["texts"])
            tag_a, tag_b = _tag(a["max_dimension"], a["deskew"]), _tag(b["max_dimension"], b["deskew"])
            print("-" * 62)
            print(f"{tag_a}  vs  {tag_b}    （{len(a['texts'])} 条 vs {len(b['texts'])} 条）")
            print("-" * 62)
            print(f"  只在 [{tag_a}] : {[_short(t, 24) for t in sorted(only_a)][:6] or '（无）'}")
            print(f"  只在 [{tag_b}] : {[_short(t, 24) for t in sorted(only_b)][:6] or '（无）'}")

    # ---- 关键词追踪：这才是本实验的重点 ----
    print()
    print("-" * 62)
    print("关键词追踪（同一个词在四种组合下分别识别成什么）")
    print("-" * 62)
    for word in WATCH_WORDS:
        if not any(word in t for r in runs for t in r["texts"]):
            continue
        print(f"\n  「{word}」")
        for run in runs:
            found = [t for t in run["texts"] if word in t]
            tag = _tag(run["max_dimension"], run["deskew"])
            if found:
                print(f"      [{tag:<12}] {_short(found[0], 52)}")
            else:
                print(f"      [{tag:<12}] （未识别到）")


# ===========================================================================
# 主流程
# ===========================================================================
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 引擎只建一次：get_engine() 是单例，且首次调用会懒加载 PaddleOCR 模型。
    engine = get_engine()
    print("OCR 引擎就绪，开始实验。")
    print(f"组合：max_dimension {MAX_DIMS} × deskew {DESKEWS} = "
          f"{len(MAX_DIMS) * len(DESKEWS)} 次/图")

    for image_path, label in IMAGES:
        if not image_path.exists():
            print(f"\n[跳过] 找不到图片：{image_path}")
            continue

        print()
        print(BAR)
        print(f"处理：{image_path.name}")
        print(BAR)

        images, _ = parse_file(str(image_path))
        base = images[0]
        actual = "x".join(str(v) for v in base.size)
        print(f"原始尺寸 {actual}  |  {label}")

        runs = []
        for max_dim in MAX_DIMS:
            for deskew in DESKEWS:
                tag = _tag(max_dim, deskew)
                print(f"  → 跑 {tag} …", flush=True)
                runs.append(run_once(engine, base, max_dim, deskew))

        report(f"{image_path.name}  ({label})", runs)

        # ---- 落盘 ----
        stem = image_path.stem.replace(" ", "_")
        for run in runs:
            tag = _tag(run["max_dimension"], run["deskew"]).replace("/", "_")
            out = OUT_DIR / f"{stem}__{tag}.json"
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(_jsonable(run), handle, ensure_ascii=False, indent=2)
        print(f"\n  结果已存 → {OUT_DIR}")

        # 6350x4490 的 RGBA 图约 114MB，转 numpy 后翻倍；及时释放
        del base, images, runs
        gc.collect()

    print()
    print(BAR)
    print("判读方法：")
    print("  1. 先看[对照检验]：小图两档若不一致，本次结论作废")
    print("  2. 大图看「硬奥HRC / 硬度HRC」「上ダイセッノ / 上ダイセット」")
    print("  3. 小图对照上面的 Ground Truth 逐条判对错，别只看条数多少")
    print(BAR)


if __name__ == "__main__":
    main()
