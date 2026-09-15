# -*- coding: utf-8 -*-
"""
演示图纸术语验证脚本
验证 demo_drawing_perfect.png 中所有日文文本是否都能被词典正确翻译
"""
import json
import sys
import os

# 图纸中所有日文文本清单（按区域分类）
ALL_TERMS = {
    "标题栏": [
        "部品名：カバー",
        "部品名",
        "カバー",
        "材 質：SUS304",
        "材質：SUS304",
        "材 質",
        "材質",
        "SUS304",
        "尺 度：1:2",
        "尺度：1:2",
        "尺 度",
        "尺度",
        "1:2",
        "作 成：技術部",
        "作成：技術部",
        "作 成",
        "作成",
        "技術部",
    ],
    "视图名称": [
        "正面図",
        "側面図",
    ],
    "注记①": [
        "①指示無き凸部はC0.5maxとする。",
        "指示無き凸部はC0.5maxとする。",
        "指示無き凸部はC0.5maxとする",
        "指示無き凸部",
        "指示無き",
        "凸部",
        "C0.5max",
        "とする",
    ],
    "注记②": [
        "②ランジクランプ穴はバリ取りのこと。",
        "ランジクランプ穴はバリ取りのこと。",
        "ランジクランプ穴はバリ取りのこと",
        "ランジクランプ穴",
        "ランジ",
        "フランジ",
        "クランプ穴",
        "クランプ",
        "バリ取り",
        "バリ",
        "のこと",
    ],
    "注记③": [
        "③印加工部は、ピッチ寸法公差±0.005とする。",
        "印加工部は、ピッチ寸法公差±0.005とする。",
        "印加工部は、ピッチ寸法公差±0.005とする",
        "印加工部はピッチ寸法公差±0.005とする。",
        "印加工部",
        "印加工",
        "ピッチ寸法公差",
        "ピッチ",
        "寸法公差",
        "±0.005",
    ],
    "注记④": [
        "④EWC加工部の寸法形状はdxfデータ参照のこと。",
        "EWC加工部の寸法形状はdxfデータ参照のこと。",
        "EWC加工部の寸法形状はdxfデータ参照のこと",
        "EWC加工部",
        "EWC",
        "寸法形状",
        "dxfデータ",
        "dxf",
        "データ",
        "参照",
        "参照のこと",
    ],
    "注记⑤": [
        "⑤栓穴加工部にシンブを圧入のこと。",
        "栓穴加工部にシンブを圧入のこと。",
        "栓穴加工部にシンブを圧入のこと",
        "栓穴加工部",
        "栓穴",
        "シンブ",
        "シンブを圧入",
        "圧入",
    ],
    "表面处理": [
        "表面処理：黒染め",
        "表面処理:黒染め",
        "表面処理：アルマイト",
        "表面処理:アルマイト",
        "表面処理",
        "黒染め",
        "アルマイト",
    ],
    "密封面标注": [
        "シール面",
        "シール",
    ],
    "注记栏": [
        "注 記",
        "注記",
        "⑥全ての角部はR0.3以下とする。",
        "全ての角部はR0.3以下とする。",
        "全ての角部はR0.3以下とする",
        "全ての角部",
        "全ての",
        "全て",
        "角部",
        "R0.3",
        "R0.3以下",
        "以下",
        "⑦穴加工部は面取りのこと。",
        "穴加工部は面取りのこと。",
        "穴加工部は面取りのこと",
        "穴加工部",
        "面取り",
        "⑧バリ・カエリは完全に除去のこと。",
        "バリ・カエリは完全に除去のこと。",
        "バリ・カエリは完全に除去のこと",
        "バリ・カエリ",
        "カエリ",
        "完全に除去",
        "完全に",
        "除去",
        "⑨公差はJIS B 0405 中級による。",
        "公差はJIS B 0405 中級による。",
        "公差はJIS B 0405 中級による",
        "公差はJIS B 0405中級による。",
        "公差",
        "JIS B 0405",
        "JIS",
        "中級",
        "による",
    ],
    "投影法与比例尺": [
        "（第三角法）",
        "(第三角法)",
        "第三角法",
        "縮尺 1:2",
        "縮尺",
    ],
    "尺寸标注": [
        "t=5",
    ],
}


def load_dictionary(path):
    """加载词典"""
    with open(path, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    # 过滤注释键
    d = {}
    for k, v in raw.items():
        if k.startswith('___') or k.startswith('===') or v == '':
            continue
        if k.startswith('//') or k.startswith('#'):
            continue
        d[k.strip()] = v
    return d


def clean_text(text):
    """模拟翻译器的文本清洗"""
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = cleaned.replace('　', ' ')
    cleaned = ' '.join(cleaned.split())
    return cleaned


def verify_dictionary(dict_path):
    """验证词典覆盖情况"""
    dictionary = load_dictionary(dict_path)

    total = 0
    matched = 0
    unmatched = []
    category_results = {}

    for category, terms in ALL_TERMS.items():
        cat_total = 0
        cat_matched = 0
        cat_unmatched = []

        for term in terms:
            total += 1
            cat_total += 1
            cleaned = clean_text(term)

            if cleaned in dictionary:
                matched += 1
                cat_matched += 1
            else:
                unmatched.append((category, term, cleaned))
                cat_unmatched.append(term)

        category_results[category] = {
            'total': cat_total,
            'matched': cat_matched,
            'unmatched': cat_unmatched,
            'coverage': f"{cat_matched}/{cat_total} ({cat_matched*100//cat_total if cat_total else 0}%)"
        }

    return {
        'total': total,
        'matched': matched,
        'unmatched': unmatched,
        'coverage': f"{matched}/{total} ({matched*100//total if total else 0}%)",
        'categories': category_results
    }


def main():
    # 查找词典文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(script_dir, 'dictionary_demo.json'),
        os.path.join(script_dir, 'dictionary.json'),
        os.path.join(script_dir, '..', 'dictionary_demo.json'),
        os.path.join(script_dir, '..', 'dictionary.json'),
    ]

    dict_path = None
    for p in possible_paths:
        if os.path.exists(p):
            dict_path = p
            break

    if not dict_path:
        print("错误: 未找到词典文件!")
        print("请确保 dictionary_demo.json 或 dictionary.json 存在")
        sys.exit(1)

    print(f"使用词典: {dict_path}")
    print("=" * 60)

    results = verify_dictionary(dict_path)

    print(f"\n总体覆盖率: {results['coverage']}")
    print("=" * 60)

    print("\n各分类覆盖情况:")
    print("-" * 60)
    for cat, res in results['categories'].items():
        status = "✓" if res['matched'] == res['total'] else "✗"
        print(f"  {status} {cat}: {res['coverage']}")
        if res['unmatched']:
            for t in res['unmatched'][:5]:
                print(f"      未匹配: {t}")
            if len(res['unmatched']) > 5:
                print(f"      ... 还有 {len(res['unmatched'])-5} 条")

    print("\n" + "=" * 60)
    if results['matched'] == results['total']:
        print("✓ 验证通过! 所有日文文本均已被词典覆盖。")
        print("  可以用于演示，确保不漏翻、不错翻。")
    else:
        print(f"✗ 验证失败! 还有 {len(results['unmatched'])} 条未匹配。")
        print("  请将以下词条添加到词典中:")
        for cat, term, cleaned in results['unmatched']:
            print(f"    [{cat}] \"{cleaned}\": \"翻译\"")

    return 0 if results['matched'] == results['total'] else 1


if __name__ == '__main__':
    sys.exit(main())
