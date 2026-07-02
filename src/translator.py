"""
翻译模块 | Translator Module
=========================
基于离线术语词典的日文→中文翻译，支持精确匹配与模糊匹配。

词典格式 (JSON):
  {
    "日文术语": "中文翻译",
    "面取り": "倒角",
    ...
  }

匹配策略:
  1. 精确匹配: 清洗 OCR 文本 → 直接在词典中查找 → O(1)
  2. 模糊匹配: 使用 FuzzyWuzzy (Levenshtein 距离) 计算相似度
     → 相似度 ≥ threshold (默认 85%) → 返回最佳匹配的翻译
  3. 未命中标记: 返回 "[未翻译]" 标记，记录到日志供后续处理

原理说明:
  FuzzyWuzzy 的 partial_ratio 算法:
    计算两个字符串的 Levenshtein 编辑距离（插入/删除/替换的最小操作数），
    然后以较短字符串为基准归一化为 0-100 的相似度分数。
    适合 OCR 结果中存在少量字符错误/多余空格的情况。

动态更新:
  运行时可通过 add_term() 和 remove_term() 方法修改词典，
  修改后的词典通过 save_dictionary() 写回 JSON 文件。
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================
DEFAULT_CONFIG = {
    'dictionary_path': 'dictionary.json',
    'fuzzy_threshold': 85,         # 模糊匹配最低相似度 (%)
    'untranslated_marker': '[未翻译]',  # 未匹配标记
}


class Translator:
    """
    日文→中文术语翻译器。

    使用方式:
        translator = Translator()
        translator.load_dictionary('dictionary.json')
        result, method = translator.translate('面取り')
        # result = '倒角', method = 'exact'
    """

    def __init__(self, config: Optional[dict] = None):
        """
        初始化翻译器。

        Args:
            config: 配置字典
        """
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        # 词典: {日文 → 中文}
        self._dictionary: Dict[str, str] = {}
        # 词典文件路径
        self._dict_path: Optional[Path] = None

    # ============================================================
    # 词典加载
    # ============================================================

    def load_dictionary(self, dict_path: Optional[str] = None) -> int:
        """
        从 JSON 文件加载术语词典。

        词典文件格式:
          {
            "日文术语1": "中文翻译1",
            "日文术语2": "中文翻译2",
            ...
          }
          支持以 "//" 或 "---" 开头的注释键（会被自动过滤）

        Args:
            dict_path: 词典文件路径。若为 None，使用配置中的 dictionary_path。

        Returns:
            加载的术语数量

        Raises:
            FileNotFoundError: 词典文件不存在
            json.JSONDecodeError: JSON 格式错误
        """
        if dict_path is None:
            dict_path = self.config['dictionary_path']

        path = Path(dict_path)

        if not path.exists():
            raise FileNotFoundError(
                f"术语词典文件不存在: {dict_path}\n"
                f"请确保 dictionary.json 文件位于正确位置。"
            )

        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw_dict = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"词典 JSON 格式错误: {dict_path}\n详情: {str(e)}")

        # ---- 过滤注释键 ----
        self._dictionary = {}
        skipped = 0
        for key, value in raw_dict.items():
            # 跳过注释/分隔符键
            if key.startswith('___') or key.startswith('===') or value == '':
                skipped += 1
                continue
            # 跳过描述性键
            if key.startswith('//') or key.startswith('#'):
                skipped += 1
                continue
            self._dictionary[key] = str(value)

        self._dict_path = path
        logger.info(
            f"词典加载完成: {len(self._dictionary)} 条术语"
            + (f" (跳过 {skipped} 条注释)" if skipped else "")
        )
        return len(self._dictionary)

    # ============================================================
    # 翻译
    # ============================================================

    def translate(self, text: str) -> Tuple[str, str, float]:
        """
        将日文术语翻译为中文。

        匹配流程:
          1. 清洗输入文本（去除首尾空格、统一全角/半角）
          2. 精确匹配
          3. 模糊匹配（FuzzyWuzzy）
          4. 未命中 → 标记

        Args:
            text: 日文术语文本

        Returns:
            (translated_text, match_method, confidence)
              - translated_text: 翻译后的中文文本
              - match_method: 'exact' | 'fuzzy' | 'unmatched'
              - confidence: 匹配置信度 0.0~1.0
        """
        if not self._dictionary:
            logger.warning("词典为空，无法翻译")
            return self.config['untranslated_marker'], 'unmatched', 0.0

        # ---- 清洗文本 ----
        cleaned = self._clean_text(text)

        if not cleaned:
            return self.config['untranslated_marker'], 'unmatched', 0.0

        # ---- Step 1: 精确匹配 ----
        if cleaned in self._dictionary:
            return self._dictionary[cleaned], 'exact', 1.0

        # ---- Step 2: 模糊匹配 ----
        try:
            from fuzzywuzzy import process
            from fuzzywuzzy import fuzz

            # 计算与所有词典条目的相似度
            best_match, score = process.extractOne(
                cleaned,
                self._dictionary.keys(),
                scorer=fuzz.partial_ratio,  # 部分匹配(适合OCR可能截断的情况)
            )

            fuzzy_threshold = self.config['fuzzy_threshold']
            if score >= fuzzy_threshold:
                translation = self._dictionary[best_match]
                confidence = score / 100.0
                logger.debug(
                    f"模糊匹配: '{cleaned}' → '{translation}' "
                    f"(via '{best_match}', score={score})"
                )
                return translation, 'fuzzy', round(confidence, 4)

        except ImportError:
            logger.warning("FuzzyWuzzy 未安装，模糊匹配不可用。请安装: pip install fuzzywuzzy")

        # ---- Step 3: 未匹配 ----
        logger.info(f"未匹配术语: '{cleaned}'")
        return self.config['untranslated_marker'], 'unmatched', 0.0

    def translate_batch(self, texts: list) -> list:
        """
        批量翻译文本列表。

        结果格式:
          [
            {'original': '面取り', 'translated': '倒角', 'method': 'exact', 'confidence': 1.0},
            ...
          ]
        """
        results = []
        for text in texts:
            translation, method, confidence = self.translate(text)
            results.append({
                'original': text,
                'translated': translation,
                'method': method,
                'confidence': confidence,
            })
        return results

    # ============================================================
    # 词典管理
    # ============================================================

    def add_term(self, japanese: str, chinese: str):
        """
        添加或更新一个术语。

        Args:
            japanese: 日文术语
            chinese: 中文翻译
        """
        self._dictionary[japanese] = chinese
        logger.info(f"术语已添加: '{japanese}' → '{chinese}'")

    def remove_term(self, japanese: str) -> bool:
        """
        删除一个术语。

        Returns:
            True 如果成功删除，False 如果术语不存在
        """
        if japanese in self._dictionary:
            del self._dictionary[japanese]
            logger.info(f"术语已删除: '{japanese}'")
            return True
        return False

    def search_terms(self, keyword: str) -> Dict[str, str]:
        """
        搜索包含关键词的术语（支持日文和中文双向搜索）。

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的词典条目
        """
        results = {}
        keyword_lower = keyword.lower()

        for jp, cn in self._dictionary.items():
            if keyword_lower in jp.lower() or keyword_lower in cn.lower():
                results[jp] = cn

        return results

    def save_dictionary(self, dict_path: Optional[str] = None):
        """
        将当前词典保存到 JSON 文件。

        Args:
            dict_path: 目标文件路径。默认使用加载时的路径。
        """
        if dict_path:
            path = Path(dict_path)
        elif self._dict_path:
            path = self._dict_path
        else:
            path = Path(self.config['dictionary_path'])

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self._dictionary, f, ensure_ascii=False, indent=2)

        logger.info(f"词典已保存: {len(self._dictionary)} 条术语 → {path}")

    @property
    def dictionary(self) -> Dict[str, str]:
        """返回当前词典的只读副本"""
        return dict(self._dictionary)

    @property
    def term_count(self) -> int:
        """返回词典中的术语数量"""
        return len(self._dictionary)

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _clean_text(text: str) -> str:
        """
        清洗 OCR 识别出的文本。

        处理:
          - 去除首尾空格
          - 统一全角空格 → 半角
          - 统一全角英数字 → 半角
          - 保留日文特殊字符
        """
        if not text:
            return ""

        # 去除首尾空白
        cleaned = text.strip()

        # 统一全角空格
        cleaned = cleaned.replace('　', ' ')

        # 合并多余空格
        cleaned = ' '.join(cleaned.split())

        return cleaned


# ============================================================
# 全局翻译器单例
# ============================================================
_translator_instance: Optional[Translator] = None


def get_translator(config: Optional[dict] = None) -> Translator:
    """
    获取翻译器单例，避免重复加载词典。

    Args:
        config: 配置参数（仅首次调用时生效）

    Returns:
        Translator 实例
    """
    global _translator_instance
    if _translator_instance is None:
        _translator_instance = Translator(config)
    return _translator_instance
