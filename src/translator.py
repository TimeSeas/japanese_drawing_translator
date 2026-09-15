"""日→中术语翻译：先查词典，查不到再用模糊匹配兜一道。

词典是一个扁平的 {日文: 中文} JSON。机械图纸的术语是封闭集合（面取り、
焼入、バリ…），七百多条就够覆盖绝大多数图纸，用不着上模型 —— 离线、
零延迟、结果可解释，而且给客户看的时候每条译文的来源都能指出来。
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

UNTRANSLATED = '[未翻译]'


class Translator:
    def __init__(self, fuzzy_threshold: int = 85):
        self.fuzzy_threshold = fuzzy_threshold
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

    def translate(self, text: str) -> Tuple[str, str, float]:
        """返回 (译文, 匹配方式, 置信度)。

        匹配方式是 'exact' / 'fuzzy' / 'unmatched'，UI 结果表里会显示，
        方便看出哪些词是猜的、该往词典里补。
        """
        if not self._dictionary:
            return UNTRANSLATED, 'unmatched', 0.0

        cleaned = self._clean(text)
        if not cleaned:
            return UNTRANSLATED, 'unmatched', 0.0

        if cleaned in self._dictionary:
            return self._dictionary[cleaned], 'exact', 1.0

        # 精确查不到，多半是 OCR 认错了个别字符（リ/リ、ソ/ン 这类），
        # 用编辑距离兜一道。partial_ratio 而不是 ratio：OCR 有时会把
        # 相邻的标注文本并进同一个框，部分匹配能扛住这种情况。
        try:
            from fuzzywuzzy import fuzz, process
        except ImportError:
            logger.warning("fuzzywuzzy 未安装，跳过模糊匹配")
            return UNTRANSLATED, 'unmatched', 0.0

        # partial_ratio 在"短串是长串子串"时恒等于 100，于是词典里那些短词条
        # 变成了吸铁石：OCR 出一整行注記，只要碰巧含「公差」「寸法」「のこと」，
        # 就 100 分命中那个两三字的词条，译文变成"公差""尺寸""（请）" —— 还不如
        # 不翻。实测 'の穴加工部は面取リのこと' 就是被这样匹配成 '（请）' 的。
        #
        # 所以只留长度至少是 OCR 文本一半的词条。反过来不设限：词条比 OCR 文本
        # 长是正常的，那是注記被切成两半的情况，正是部分匹配要解决的。
        choices = [k for k in self._dictionary if 2 * len(k) >= len(cleaned)]
        if not choices:
            logger.info(f"未匹配术语: '{cleaned}'")
            return UNTRANSLATED, 'unmatched', 0.0

        best, score = process.extractOne(
            cleaned, choices, scorer=fuzz.partial_ratio
        )
        if score >= self.fuzzy_threshold:
            logger.debug(f"模糊匹配: '{cleaned}' → '{self._dictionary[best]}' "
                         f"(via '{best}', {score})")
            return self._dictionary[best], 'fuzzy', round(score / 100.0, 4)

        logger.info(f"未匹配术语: '{cleaned}'")
        return UNTRANSLATED, 'unmatched', 0.0

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
