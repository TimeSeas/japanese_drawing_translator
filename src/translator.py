"""日→中术语翻译：先查词典，查不到再用模糊匹配兜一道。

词典是一个扁平的 {日文: 中文} JSON。机械图纸的术语是封闭集合（面取り、
焼入、バリ…），七百多条就够覆盖绝大多数图纸，用不着上模型 —— 离线、
零延迟、结果可解释，而且给客户看的时候每条译文的来源都能指出来。
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

UNTRANSLATED = '[未翻译]'
# 「不用翻」和「翻不出来」是两回事：前者是中文读者自己就看得懂，后者的词条
# 词典里没有。界面和 JSON 里要分开统计，所以用空串而不是那个标记。
NOT_NEEDED = ''

# 假名是"这行得翻"的信号：平假名扛语法（のこと、取り），片假名是外来语
# （カバー、アルマイト），中文读者都读不出来。反过来，全是汉字加数字符号的
# 行 —— 技術部、正面図、部品名、SUS304、縮尺1:2 —— 扫一眼就懂，再拿红字
# 盖一遍只是往图纸上添乱。实测 29 条识别结果里有 9 条属于这种。
_KANA = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')

# \u6a21\u7cca\u5206\u843d\u5728\u9608\u503c\u4e0b\u9762\u7684\u8fd9\u4e2a\u5bbd\u5ea6\u91cc\uff0c\u5c31\u5f53\u6210"\u7591\u4f3c"\u5e26\u56de\u53bb\u7ed9\u754c\u9762\u770b\u3002
# 25 \u662f\u91cf\u51fa\u6765\u7684\uff08\u9608\u503c 85 \u2212 25 = \u63d0\u793a\u7ebf 60\uff09\uff1a
#
#   \u771f\u6b63\u7684\u8fd1\u4f3c\u547d\u4e2d\u90fd\u843d\u5728 65~75 \u5206 \u2014\u2014 \u529b\u30d0 67\u3001\u9762\u53d6\u30ea 67\u3001\u30bf\u30c4\u30d7 67\u3001
#   \u30d0\u30ea\u53d6\u30ea 75\uff0c\u4ee5\u53ca\u4e24\u6761\u5168\u89d2/\u534a\u89d2\u4e0d\u4e00\u81f4\u7684\u6574\u53e5 65\u300167 \u5206\u3002
#   \u968f\u673a\u5783\u573e\uff08120 \u6761\u968f\u673a\u5b57\u6bcd\u6570\u5b57 + \u968f\u673a\u5047\u540d\u4e32\uff0995 \u5206\u4f4d\u53ea\u6709 40\uff0c
#   120 \u6761\u91cc\u53ea\u6709 1 \u6761\u591f\u5230 60 \u5206\u4ee5\u4e0a\u3002
#
# \u4e24\u8fb9\u5728 60 \u5206\u4e0a\u4e0b\u5206\u5f00\u4e86\uff0c\u6ca1\u6709\u5e72\u51c0\u7684\u7a7a\u6863\uff0c\u4f46\u8bef\u62a5\u5f88\u4fbf\u5b9c\uff08\u53ea\u662f\u8ba9\u4eba\u591a\u770b\u4e00\u773c\uff09\uff0c
# \u6f0f\u62a5\u5219\u7b49\u4e8e\u628a\u7ebf\u7d22\u4e22\u4e86 \u2014\u2014 \u6240\u4ee5\u5b81\u53ef\u628a\u7ebf\u653e\u4f4e\u3002
#
# \u5f97\u8bf4\u6e05\u695a\uff1a\u8fd9**\u53ea\u662f\u7ebf\u7d22\uff0c\u4e0d\u662f\u7b54\u6848**\u3002\u529b\u30d0 \u7684\u6700\u9ad8\u5206\u5019\u9009\u662f\u300c\u5de5\u7a0b\u80fd\u529b\u300d\uff0c
# \u662f\u9519\u7684\uff1b\u9762\u53d6\u30ea \u2192 C\u9762\u53d6\u308a\u3001\u30bf\u30c4\u30d7 \u2192 \u30bf\u30c3\u30d7 \u624d\u662f\u5bf9\u7684\u3002\u5206\u6570\u9ad8\u4f4e\u533a\u5206\u4e0d\u51fa
# \u5019\u9009\u5bf9\u4e0d\u5bf9\uff0c\u6240\u4ee5\u754c\u9762\u4e0a\u5fc5\u987b\u5199\u660e\u8fd9\u662f\u63d0\u793a\u3001\u8981\u4eba\u5de5\u5224\u65ad\u3002
_SUSPECT_MARGIN = 25


class Translator:
    def __init__(self, fuzzy_threshold: int = 85, kanji_traps: Iterable[str] = ()):
        self.fuzzy_threshold = fuzzy_threshold
        # 「纯汉字就不翻」这条规则的例外：字面像中文、意思却是另一回事的词
        # （取代 → 加工余量）。名单在 config.yaml 里，按自己的图纸慢慢补。
        self.kanji_traps = set(kanji_traps or ())
        self._dictionary: Dict[str, str] = {}
        self._dict_path: Optional[Path] = None

    def load_dictionary(self, dict_path: str) -> int:
        """读词典。文件里以 ___ === // # 开头的键、以及空值，都当注释跳过。"""
        path = Path(dict_path)
        if not path.exists():
            raise FileNotFoundError(f"术语词典文件不存在: {dict_path}")

        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        self._dictionary = {
            k: str(v) for k, v in raw.items()
            if v != '' and not k.startswith(('___', '===', '//', '#'))
        }
        self._dict_path = path
        logger.info(f"词典加载完成: {len(self._dictionary)} 条")
        return len(self._dictionary)

    def translate(self, text: str) -> Tuple[str, str, float, Optional[Dict]]:
        """返回 (译文, 匹配方式, 置信度, 疑似候选)。

        匹配方式分四种：'exact' / 'fuzzy' / 'unmatched' / 'skipped'，UI 结果
        表里会显示。前两种是翻出来了，'unmatched' 是该翻但词典里没有（要往
        词典里补），'skipped' 是压根不用翻。

        第 4 项「疑似候选」只在未匹配、且分数差一点点没够阈值时才有值 ——
        见下面 _SUSPECT_MARGIN 那段。
        """
        if not self._dictionary:
            return UNTRANSLATED, 'unmatched', 0.0, None

        cleaned = self._clean(text)
        if not cleaned:
            return UNTRANSLATED, 'unmatched', 0.0, None

        # 中文读者自己能看懂的行直接放行，连词典都不用查
        if not _KANA.search(cleaned) and cleaned not in self.kanji_traps:
            logger.debug(f"无需翻译: '{cleaned}'")
            return NOT_NEEDED, 'skipped', 0.0, None

        if cleaned in self._dictionary:
            return self._dictionary[cleaned], 'exact', 1.0, None

        # 精确查不到，多半是 OCR 认错了个别字符（リ/リ、ソ/ン 这类），
        # 用编辑距离兜一道。partial_ratio 而不是 ratio：OCR 有时会把
        # 相邻的标注文本并进同一个框，部分匹配能扛住这种情况。
        try:
            from fuzzywuzzy import fuzz, process
        except ImportError:
            logger.warning("fuzzywuzzy 未安装，跳过模糊匹配")
            return UNTRANSLATED, 'unmatched', 0.0, None

        # partial_ratio 在"短串是长串子串"时恒等于 100，于是词典里那些短词条
        # 变成了吸铁石：OCR 出一整行注記，只要碰巧含「公差」「寸法」「のこと」，
        # 就 100 分命中那个两三字的词条，译文变成"公差""尺寸""（请）" —— 还不如
        # 不翻。实测 'の穴加工部は面取リのこと' 就是被这样匹配成 '（请）' 的。
        #
        # 所以只留长度至少占 OCR 文本 70% 的词条。这条线是量出来的：门槛放在
        # 50% 时，「ランジクランプ穴」这种只占半句的词条还能入选，partial_ratio
        # 因为它是子串照样给 100 分，把整句「…はバリ取りのこと。」挤掉，译文
        # 只剩半截"法兰夹紧孔"。实测 60%~80% 之间结果完全一样，取中间的 70%。
        # 反过来不设限：词条比 OCR 文本长是正常的，那是注記被切成两半的情况，
        # 正是部分匹配要解决的。
        min_key_len = 0.7 * len(cleaned)
        choices = [k for k in self._dictionary if len(k) >= min_key_len]
        if not choices:
            logger.info(f"未匹配术语: '{cleaned}'")
            return UNTRANSLATED, 'unmatched', 0.0, None

        best, score = process.extractOne(
            cleaned, choices, scorer=fuzz.partial_ratio
        )
        if score >= self.fuzzy_threshold:
            logger.debug(f"模糊匹配: '{cleaned}' → '{self._dictionary[best]}' "
                         f"(via '{best}', {score})")
            return self._dictionary[best], 'fuzzy', round(score / 100.0, 4), None

        # 差一点点没够阈值 —— 这个区间本身就是信息。OCR 认错一个字
        # （カバー→力バ、ソ→ン、0→ニ）正好落在这里：整条要么完全对不上，
        # 要么就差几分。而 best/score 上面已经算出来了，直接丢掉太浪费 ——
        # 把"差一点是什么"带回去，比一个光秃秃的 [未翻译] 有用得多。
        #
        # 注意这只是**提示**，不是翻译：match_method 仍然是 unmatched，
        # 不参与"已翻译"的统计，界面上也必须看得出来是猜的。
        suspect = None
        if score >= self.fuzzy_threshold - _SUSPECT_MARGIN:
            suspect = {'term': best, 'translation': self._dictionary[best],
                       'score': score}
            logger.info(f"疑似: '{cleaned}' ≈ '{best}' "
                        f"→ '{self._dictionary[best]}' ({score})")

        logger.info(f"未匹配术语: '{cleaned}'")
        return UNTRANSLATED, 'unmatched', 0.0, suspect

    # ---- 词典维护（词典编辑器用） ----

    @property
    def dictionary(self) -> Dict[str, str]:
        return dict(self._dictionary)

    @property
    def term_count(self) -> int:
        return len(self._dictionary)

    def search_terms(self, keyword: str) -> Dict[str, str]:
        """日文和中文两边都搜，词典编辑器里输中文也能找到词条。"""
        kw = keyword.lower()
        return {
            jp: cn for jp, cn in self._dictionary.items()
            if kw in jp.lower() or kw in cn.lower()
        }

    def add_term(self, japanese: str, chinese: str):
        self._dictionary[japanese] = chinese

    def remove_term(self, japanese: str) -> bool:
        return self._dictionary.pop(japanese, None) is not None

    def save_dictionary(self, dict_path: Optional[str] = None):
        path = Path(dict_path) if dict_path else self._dict_path
        if path is None:
            raise ValueError("词典尚未加载，无法确定保存路径")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self._dictionary, f, ensure_ascii=False, indent=2)
        logger.info(f"词典已保存: {len(self._dictionary)} 条 → {path}")

    @staticmethod
    def _clean(text: str) -> str:
        """OCR 出来的文本要清一遍：去首尾空白、全角空格转半角、合并连续空格。"""
        return ' '.join(text.replace('　', ' ').split())
