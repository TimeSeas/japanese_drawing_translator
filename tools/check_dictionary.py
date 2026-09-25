# -*- coding: utf-8 -*-
"""词典自检：把全部术语扫一遍，挑出结构性的毛病。

背景
----
词条写错了不会报错，只会安静地输出一条读不通的译文 —— 这是最难发现的一类
问题，因为界面上它和"翻对了"长得一模一样。这个脚本把能在**运行之前**查出来
的几类挑出来：

  1. 中文值里还有假名 —— 词条没抄完，输出必错
  2. 两个键互为真子串、译文却不一样 —— partial_ratio 恒等 100 的静态版本
  3. 一个日文键的译文就是它自己 —— 等于没翻
  4. 两个不同的键译成同一个词 —— 多半是复制粘贴留下的
  5. 空值、首尾空白 —— 静默失效的条目

第 2 条值得展开。模糊匹配用的是 partial_ratio，它的特点是「短串只要是长串的
子串就给 100 分」。所以只要词典里同时存在「面取り」和「糸面取り」这种子串对，
拿整句去查的时候短的那个必然胜出。运行时靠"词条长度至少占 OCR 文本 70%"这道
门槛挡着（见 src/translator.py），但那是治标 —— 这里是把这个隐患在词典这一层
直接列出来，加词的时候就能看见。

判定原则
--------
这些全是**疑似**，不是错误。`面取り→倒角` 和 `糸面取り→倒角` 是合理的子串对
（译文一样，不冲突，第 2 条不会报）；`座金→垫圈` 和 `座金具→垫圈` 也一样。
所以脚本只列清单，不做修改 —— 要不要动由人判断。

跑法
----
    python tools/check_dictionary.py [词典路径]

不传路径就用 config.yaml 里的。有问题时退出码为 1，可以直接挂到 CI 上。
"""

import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.translator import Translator  # noqa: E402

# 中文词典值里出现任何一个假名，都说明这条没抄完
_KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')

# 第 2 条会列出所有子串对；键太短的话（「穴」「径」这种单字）命中一大片，
# 全列出来反而把真问题淹了。只报 3 字以上的对。
_MIN_SUBSTRING_KEY = 3

# 和 src/translator.py 里那道候选长度门槛是同一个数：len(候选) >= 0.7 * len(文本)。
# 那里改了这里要跟着改，否则自检报的"危险/安全"就和运行时对不上。
_CANDIDATE_LEN_RATIO = 0.7

# 第 7 条要区分「汉字类」和「字母缩写类」——后者才是跳过规则真正的漏洞
_LATIN = re.compile(r'[A-Za-z]')


def load(dict_path: str):
    """用 Translator 自己的加载逻辑读词典。

    不能自己 open+json.load —— 加载时会按 translator.py 的规则过滤掉空值和
    ___/===/// /# 开头的注释键，自己读会把这 22 条注释也当成词条扫进来，
    报一堆假问题。
    """
    tr = Translator()
    total = tr.load_dictionary(dict_path)
    return tr.dictionary, total


def check_kana(d: dict):
    """第 1 条：中文值里混进了假名。"""
    return [(jp, cn) for jp, cn in d.items() if _KANA.search(cn)]


def check_identity(d: dict):
    """第 3 条：译文和原词一模一样，等于没翻。"""
    return [(jp, cn) for jp, cn in d.items() if jp == cn]


def check_substring_conflict(d: dict):
    """第 2 条：键互为真子串，译文却不一样。

    O(n²)，但键都只有几个字，963 条的规模跑下来是秒级，不值得为它做索引。

    关键是**分危险等级**，不能一股脑列出来 —— 子串对在术语词典里太常见
    （「ガイド」是「ガイドピン」「ガイドポスト」的子串，完全合理），全列就是
    一百多行噪声，真问题反而被淹掉。判据是运行时的长度门槛：

        候选词条要求 len(key) >= 0.7 * len(OCR文本)      （见 translator.py）

    拿整句 long 去查的时候，短键 short 是它的子串，partial_ratio 给 100；
    长键自己也给 100。两个都是 100，那就是谁先谁赢，短的那个很可能胜出。
    但前提是 short 得**先过得了这道门槛**：

        len(short) >= 0.7 * len(long)   门槛挡不住，危险
        len(short) <  0.7 * len(long)   门槛已经挡住了，安全

    所以只有长度比够大的子串对才展开报，其余只报个数。
    """
    dangerous, blocked = [], 0

    by_len = defaultdict(list)
    for key in d:
        by_len[len(key)].append(key)

    lengths = sorted(by_len)
    for short_len in lengths:
        if short_len < _MIN_SUBSTRING_KEY:
            continue
        for short in by_len[short_len]:
            for long_len in lengths:
                if long_len <= short_len:
                    continue
                for long in by_len[long_len]:
                    if short not in long or d[short] == d[long]:
                        continue
                    if short_len >= _CANDIDATE_LEN_RATIO * long_len:
                        dangerous.append((short, d[short], long, d[long]))
                    else:
                        blocked += 1
    return dangerous, blocked


def check_duplicate_values(d: dict):
    """第 4 条：两个不同的日文键译成同一个中文。"""
    by_cn = defaultdict(list)
    for jp, cn in d.items():
        by_cn[cn].append(jp)
    return {cn: jps for cn, jps in by_cn.items() if len(jps) > 1}


def check_blank(dict_path: str):
    """第 5 条：被加载逻辑静默丢掉的条目（空值、只有空白的值）。

    这些在 d 里已经不存在了，所以只能回原始 JSON 里数。
    """
    import json
    raw = json.load(open(dict_path, encoding='utf-8'))
    blanks = [k for k, v in raw.items()
              if not str(v).strip() and not k.startswith(('___', '===', '//', '#'))]
    comments = [k for k in raw if k.startswith(('___', '===', '//', '#'))]
    return blanks, len(comments), len(raw)


def check_near_duplicate_keys(d: dict):
    """第 6 条：两个键只差结尾标点或全半角，却各占一条。

    这类重复会让模糊匹配在两条几乎一样的候选之间随便挑一条，而且
    子串检查（第 2 条）会把它们当成"危险子串对"报出来，噪声很大。
    """
    def norm(s):
        s = unicodedata.normalize('NFKC', s)
        return s.rstrip('。.、,，:：;；!！?？')

    groups = defaultdict(list)
    for key in d:
        groups[norm(key)].append(key)
    return {n: ks for n, ks in groups.items() if len(ks) > 1}


def check_unreachable_nokana(d: dict, kanji_traps):
    """第 7 条：没有假名的键 —— 永远走不到精确匹配。

    translator.py 里跳过规则在查词典**之前**就 return 了：

        if not _KANA.search(cleaned) and cleaned not in self.kanji_traps:
            return NOT_NEEDED, 'skipped', 0.0
        if cleaned in self._dictionary:          # ← 到不了这里
            return self._dictionary[cleaned], 'exact', 1.0

    所以只要键里没有假名、又不在 kanji_traps 里，拿它自己去查永远是 skipped，
    词典里给它写的译文一辈子不会出现在界面上。

    这里必须分两类看，因为跳过规则的**理由**对它们不成立的程度不一样：

      汉字类（技術部、正面図）  —— 理由成立：中文读者确实扫一眼就懂
      字母/缩写类（NG、OK、MOQ、REV、MC加工）—— 理由**不成立**：
          没有假名，但中文读者也读不出来。NG 是"不合格"、MOQ 是"最小起订量"，
          这些恰恰是最该翻的，却被同一条规则一起跳过了。

    第二类是这条规则真正的漏洞 —— 判据用的是"有没有假名"，可它想表达的
    其实是"中文读者看不看得懂"，两者在字母缩写上正好错开。
    """
    unreachable = [k for k in d
                   if not _KANA.search(k) and k not in kanji_traps
                   and d[k] != k]
    latin = [k for k in unreachable if _LATIN.search(k)]
    return unreachable, latin


def _load_kanji_traps():
    """kanji_traps 是跳过规则的例外名单（取代→加工余量、半田→焊锡 这类），
    名单里的纯汉字词是能走到词典的，第 7 条要把它们排除掉。"""
    try:
        import yaml
        cfg = yaml.safe_load(open('config.yaml', encoding='utf-8'))
        return set(cfg['translator'].get('kanji_traps') or ())
    except Exception as e:
        print(f"  （读 config.yaml 的 kanji_traps 失败: {e}，按空名单算）")
        return set()


def _section(title: str, hint: str):
    print(f"\n{'=' * 72}\n{title}\n{hint}\n{'=' * 72}")


def main():
    dict_path = (sys.argv[1] if len(sys.argv) > 1
                 else 'dictionary.json')
    if not Path(dict_path).exists():
        print(f"词典不存在: {dict_path}", file=sys.stderr)
        return 2

    d, total = load(dict_path)
    print(f"词典: {dict_path}")
    print(f"有效词条: {total} 条")

    problems = 0

    _section("1. 中文值里含假名", "词条没抄完，输出必然读不通")
    hits = check_kana(d)
    if hits:
        problems += len(hits)
        for jp, cn in hits:
            print(f"  {jp}  →  {cn!r}   ← 译文里有假名")
    else:
        print("  无")

    _section("2. 键互为子串但译文不同",
             "partial_ratio 对子串恒给 100 分，短的那个会挤掉长的")
    dangerous, blocked = check_substring_conflict(d)
    if dangerous:
        problems += len(dangerous)
        for short, scn, long, lcn in dangerous:
            ratio = len(short) / len(long)
            print(f"  [危险 {ratio:.0%}] {short}({scn})")
            print(f"           是  {long}({lcn})  的子串，长度比过线，"
                  f"70% 门槛挡不住")
    else:
        print("  无危险项")
    if blocked:
        print(f"\n  另有 {blocked} 对子串的长度比没到 70%，已被运行时门槛挡住，"
              f"不展开。")

    _section("3. 译文与原词相同", "等于没翻，要确认是不是漏填")
    hits = check_identity(d)
    if hits:
        problems += len(hits)
        for jp, cn in hits:
            print(f"  {jp} → {cn}")
    else:
        print("  无")

    _section("4. 多个日文键译成同一个中文", "多半是复制粘贴留下的")
    dups = check_duplicate_values(d)
    if dups:
        # 这一类比前几类轻：多对一多半是正常的（「焼入」和「焼入れ」都译「淬火」，
        # 是同一个词的长短两种写法）。全列出来有九十多行，反而没人看 —— 只列
        # 前几条当样品，其余报个数。
        for cn, jps in sorted(dups.items(), key=lambda kv: -len(kv[1]))[:8]:
            print(f"  {cn:14s} ← {' / '.join(jps)}")
        print(f"\n  共 {len(dups)} 组，上面是其中 8 组。多对一不一定是错的，"
              f"自己看一眼。")
    else:
        print("  无")

    _section("5. 被静默丢掉的条目", "空值/纯空白，加载时不会进词典")
    blanks, n_comments, n_raw = check_blank(dict_path)
    if blanks:
        problems += len(blanks)
        for k in blanks:
            print(f"  空值: {k}")
    else:
        print("  无空值条目")
    print(f"\n  原始键 {n_raw} 个 —— 其中注释键 {n_comments} 个、空值 "
          f"{len(blanks)} 个 → 有效词条 {total} 条")

    _section("6. 键几乎重复（只差结尾标点/全半角）",
             "模糊匹配会在两条几乎一样的候选里随便挑一条")
    groups = check_near_duplicate_keys(d)
    if groups:
        problems += sum(len(ks) - 1 for ks in groups.values())
        for _, keys in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:12]:
            print(f"  {'  |  '.join(keys)}")
            for k in keys:
                print(f"        {k!r} → {d[k]}")
        if len(groups) > 12:
            print(f"  …… 还有 {len(groups) - 12} 组")
    else:
        print("  无")

    _section("7. 无假名的键：永远走不到精确匹配",
             "跳过规则在查词典之前就 return 了，这些译文出不来")
    traps = _load_kanji_traps()
    unreachable, latin = check_unreachable_nokana(d, traps)
    if unreachable:
        print(f"  【字母/缩写类 {len(latin)} 条】—— 跳过规则的理由在这里不成立")
        for k in latin[:20]:
            print(f"      {k} → {d[k]}")
        if len(latin) > 20:
            print(f"      …… 还有 {len(latin) - 20} 条")
        print(f"\n  汉字类 {len(unreachable) - len(latin)} 条 —— "
              f"中文读者确实看得懂，跳过是对的，不展开")
        print(f"\n  两类合计 {len(unreachable)} 条 / 全词典 {total} 条"
              f"（{len(unreachable)/total:.0%}）。")
        print("  它们还能当模糊匹配的候选，不是完全没用 —— 但「这条到底生效没有」，"
              "加词的人应该能看见。")
    else:
        print("  无")

    print(f"\n{'=' * 72}")
    if problems:
        print(f"有 {problems} 处需要人工确认。都是疑似，不是错误 —— "
              f"要不要改自己判断。")
    else:
        print("未发现结构性问题。")
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
