# -*- coding: utf-8 -*-
"""实验：预处理两个参数对 OCR 结果的影响（2×2 对照）

背景
----
`preprocess()` 里有两个步骤当初是拍脑袋定的，一直没验证过：

  1. 下采样 —— 大图等比缩到 max_dimension 以内。
     源图纸 6350×4490，如果 max_dimension 设成 3000，等于跑 OCR 之前
     先把分辨率砍掉一半。注记字号本来就小，这一步很可能是负收益。

  2. 倾斜校正 —— 霍夫变换求所有直线倾角的中位数，|角度| > 0.5° 就转图。
     但机械图纸横竖斜线都有，这个"中位数"没有意义。实测一张完全水平的
     图纸被判成 -6°（整数是因为 HoughLines 角度分辨率就是 1°），转完
     OCR 反而变差。所以 config.yaml 里 deskew 默认是关的 —— 本实验
     就是为了确认这个判断。

本脚本不改任何源文件，只在调用 preprocess() 时覆盖这两个参数。

判读原则
--------
不看"平均准确率"，只看已知答案的那几条现在对不对。
对照组：小图（2000×1400）在 max_dimension 两档下应当逐字一致 ——
若不一致，说明不止动了一个变量，结论作废。

Ground Truth
------------
下面是从 演示图纸_カバー.png 上读出的原文，用来判对错。
这是照缩略图抄的，正式引用前请打开原图核对一遍。

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
    python tools/exp_downsample.py
"""
from __future__ import annotations

import gc
import json
import sys
import time
from pathlib import Path

# Windows 控制台默认 cp936，而乱码样本里含有 cp936 编不出来的字符
# （如 'đ' U+0111）。不重设编码，print 那些文本会直接 UnicodeEncodeError
# 崩掉 —— 而"打印乱码"恰恰是本实验的目的之一。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as app
from src.file_parser import parse_file
from src.image_processor import preprocess
from src.ocr_engine import OCREngine

# ===========================================================================
# 实验参数
# ===========================================================================

BASE_CONFIG = app.load_config()
OUTER = ROOT.parent

IMAGES = [
    (OUTER / "日文零件图纸术语自动翻译功能" / "20231113162902978.翻译参考_01(1).png",
     "大图 6350×4490（会触发下采样）"),
    (OUTER / "演示图纸_カバー.png",
     "小图 2000×1400（不触发下采样 → 对照组）"),
]

MAX_DIMS = [3000, 99999]      # 3000 = 缩小；99999 = 不缩（放大到超过原图边长）
DESKEWS = [False, True]       # False = 现状（config.yaml 默认）

WATCH_WORDS = ["公差", "除去", "面取", "加工部", "参照", "部品", "材質",
               "硬度", "ダイセット"]

OUT_DIR = ROOT / "output" / "exp_downsample"
BAR = "=" * 62


def _jsonable(value):
    """把 numpy 标量递归转成 Python 原生类型，否则 json.dump 会抛
    TypeError: Object of type float32 is not JSON serializable。
    round(np.float32(x), 4) 返回的仍然是 numpy.float32 —— round() 不管类型转换。"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):      # numpy 标量都有 .item()
        return value.item()
    return value


def _short(text: str, limit: int = 40) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _tag(max_dim: int, deskew: bool) -> str:
    dim = "不缩" if max_dim > 10000 else str(max_dim)
    return f"{dim}/{'转' if deskew else '不转'}"


def run_once(engine, image, max_dim: int, deskew: bool) -> dict:
    """跑一遍 预处理 + OCR。

    config 用 {**BASE, ...} 而不是只传要改的两个键 —— 只传两个键的话，
    其余参数取不到值会各自走代码内部兜底，实验就不是单变量了。
    """
    config = {**BASE_CONFIG['preprocessing'],
              "max_dimension": max_dim, "deskew": deskew}

    # 下采样是否触发要在预处理**之前**判断：预处理会改尺寸，拿处理后的
    # 尺寸和原始尺寸比是错的（旋转也会改变尺寸）。
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
        "downsampled": will_downsample,
        "preprocess_sec": round(t1 - t0, 2),
        "ocr_sec": round(t2 - t1, 2),
        "count": len(results),
        "items": _jsonable(results),
        "texts": [r["text"] for r in results],
    }


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

    # 小图不该触发下采样，所以两档 max_dimension 应当逐字一致。
    # 不一致 → 有别的变量在动 → 全部结论不可信。
    for deskew in DESKEWS:
        pair = [r for r in runs if r["deskew"] is deskew]
        if len(pair) == 2 and not any(r["downsampled"] for r in pair):
            same = set(pair[0]["texts"]) == set(pair[1]["texts"])
            print(f"\n  [对照检验] deskew={deskew} 下 max_dimension 两档"
                  f" → {'一致 ✓ 单变量成立' if same else '不一致 ✗ 有别的变量在动'}")

    print()
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a, b = runs[i], runs[j]
            tag_a = _tag(a["max_dimension"], a["deskew"])
            tag_b = _tag(b["max_dimension"], b["deskew"])
            print("-" * 62)
            print(f"{tag_a}  vs  {tag_b}    （{len(a['texts'])} 条 vs {len(b['texts'])} 条）")
            print("-" * 62)
            only_a = sorted(set(a["texts"]) - set(b["texts"]))
            only_b = sorted(set(b["texts"]) - set(a["texts"]))
            print(f"  只在 [{tag_a}] : {[_short(t, 24) for t in only_a][:6] or '（无）'}")
            print(f"  只在 [{tag_b}] : {[_short(t, 24) for t in only_b][:6] or '（无）'}")

    print()
    print("-" * 62)
    print("关键词追踪（同一个词在四种组合下分别被识别成什么）")
    print("-" * 62)
    for word in WATCH_WORDS:
        if not any(word in t for r in runs for t in r["texts"]):
            continue
        print(f"\n  「{word}」")
        for run in runs:
            found = [t for t in run["texts"] if word in t]
            tag = _tag(run["max_dimension"], run["deskew"])
            line = _short(found[0], 52) if found else "（未识别到）"
            print(f"      [{tag:<12}] {line}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    engine = OCREngine(**BASE_CONFIG['ocr'])
    print("OCR 引擎就绪。")
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
        print(f"原始尺寸 {'x'.join(str(v) for v in base.size)}  |  {label}")

        runs = []
        for max_dim in MAX_DIMS:
            for deskew in DESKEWS:
                print(f"  → 跑 {_tag(max_dim, deskew)} …", flush=True)
                runs.append(run_once(engine, base, max_dim, deskew))

        report(f"{image_path.name}  ({label})", runs)

        stem = image_path.stem.replace(" ", "_")
        for run in runs:
            tag = _tag(run["max_dimension"], run["deskew"]).replace("/", "_")
            with open(OUT_DIR / f"{stem}__{tag}.json", "w", encoding="utf-8") as f:
                json.dump(_jsonable(run), f, ensure_ascii=False, indent=2)
        print(f"\n  结果已存 → {OUT_DIR}")

        # 6350×4490 的 RGBA 图约 114MB，转 numpy 后翻倍，及时释放
        del base, images, runs
        gc.collect()

    print()
    print(BAR)
    print("判读方法：")
    print("  1. 先看[对照检验]：小图两档若不一致，本次结论作废")
    print("  2. 大图看「硬奥HRC / 硬度HRC」「上ダイセッノ / 上ダイセット」这类")
    print("     被切碎或认错的词，在不缩/不转那一档是否恢复正常")
    print("  3. 小图对照上面的 Ground Truth 逐条判对错，别只看条数多少")
    print(BAR)


if __name__ == "__main__":
    main()
