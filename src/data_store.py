"""
数据存储模块 | Data Store Module
==============================
负责翻译结果的结构化存储与操作日志管理。

功能:
  1. JSON 结构化存储识别+翻译结果
  2. 日志系统（按天切割，180天自动清理）
  3. 处理统计与历史记录查询

JSON 输出格式:
  {
    "source_file": "DS501プレート.pdf",
    "processed_at": "2026-06-29T10:30:00",
    "page": 1,
    "image_size": {"width": 2480, "height": 3508},
    "preprocessing": {...},
    "texts": [
      {
        "id": 1,
        "original": "面取り",
        "translated": "倒角",
        "confidence": 0.95,
        "match_method": "exact",
        "bbox": {"min_x": 120, "min_y": 340, "max_x": 200, "max_y": 370}
      }
    ],
    "statistics": {
      "total_detected": 15,
      "translated": 13,
      "unmatched": 2,
      "avg_confidence": 0.93,
      "processing_time_sec": 3.2
    }
  }

日志管理原理:
  使用 Python logging 模块，配合 TimedRotatingFileHandler 实现按天切割:
    - 日志文件命名: app_YYYYMMDD.log
    - 每天午夜自动切割
    - 启动时扫描日志目录，删除超过 180 天的文件
"""

import json
import logging
import logging.handlers
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple


# ============================================================
# 默认配置
# ============================================================
DEFAULT_CONFIG = {
    'log_dir': 'logs',
    'log_level': 'INFO',
    'retention_days': 180,
    'log_format': '%(asctime)s | %(levelname)s | %(message)s',
}


class DataStore:
    """
    数据存储管理器。

    使用方式:
        store = DataStore()
        store.setup_logging()
        store.save_result(source_file, image_size, texts, stats, page_num=1)
    """

    def __init__(self, config: Optional[dict] = None):
        """
        初始化数据存储管理器。

        Args:
            config: 配置字典
        """
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self._logger = None

    # ============================================================
    # 日志系统
    # ============================================================

    def setup_logging(self, log_level: Optional[str] = None):
        """
        配置日志系统。

        设置 TimedRotatingFileHandler 实现按天切割:
          - 日志文件: logs/app_YYYYMMDD.log
          - 每天午夜 (when='midnight') 自动切割
          - 保留 30 个备份文件（即 30 天），启动时再清理 180 天外的

        Args:
            log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        """
        if log_level is None:
            log_level = self.config['log_level']

        level = getattr(logging, log_level.upper(), logging.INFO)

        # ---- 创建日志目录 ----
        log_dir = Path(self.config['log_dir'])
        log_dir.mkdir(parents=True, exist_ok=True)

        # ---- 根日志器配置 ----
        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # 避免重复添加 handler
        if not any(isinstance(h, logging.handlers.TimedRotatingFileHandler)
                   for h in root_logger.handlers):
            # 文件 handler（按天切割）
            log_file = log_dir / 'app.log'
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=str(log_file),
                when='midnight',
                interval=1,
                backupCount=30,  # 保留 30 天（180 天外的由清理函数处理）
                encoding='utf-8',
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(
                logging.Formatter(self.config['log_format'])
            )
            root_logger.addHandler(file_handler)

            # 控制台 handler（仅 WARNING 及以上）
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.WARNING)
            console_handler.setFormatter(
                logging.Formatter(self.config['log_format'])
            )
            root_logger.addHandler(console_handler)

        self._logger = logging.getLogger(__name__)

        # ---- 清理过期日志 ----
        self._cleanup_old_logs()

        self._logger.info("=" * 50)
        self._logger.info("日文零件图纸术语自动翻译应用启动")
        self._logger.info(f"日志目录: {log_dir.absolute()}")
        self._logger.info(f"日志保留: {self.config['retention_days']} 天")
        self._logger.info("=" * 50)

    def _cleanup_old_logs(self):
        """
        清理超过保留期限的日志文件。

        扫描日志目录中所有匹配命名规范的日志文件:
          - app_YYYYMMDD.log
          - app.log.YYYY-MM-DD
        删除修改时间超过 retention_days 天的文件。
        """
        retention_days = self.config['retention_days']
        log_dir = Path(self.config['log_dir'])

        if not log_dir.exists():
            return

        cutoff_time = datetime.now() - timedelta(days=retention_days)
        cutoff_timestamp = cutoff_time.timestamp()

        log_pattern = re.compile(r'^app.*\.log.*$')

        deleted_count = 0
        for log_file in log_dir.iterdir():
            if log_file.is_file() and log_pattern.match(log_file.name):
                try:
                    if log_file.stat().st_mtime < cutoff_timestamp:
                        log_file.unlink()
                        deleted_count += 1
                except OSError:
                    pass  # 文件正在使用中，跳过

        if deleted_count > 0:
            logging.getLogger(__name__).info(
                f"日志清理完成: 删除 {deleted_count} 个过期日志文件"
            )

    # ============================================================
    # 结果存储
    # ============================================================

    def save_result(
        self,
        source_file: str,
        image_size: Tuple[int, int],
        texts: List[Dict],
        stats: Optional[Dict] = None,
        page_num: int = 1,
        preprocessing_config: Optional[dict] = None,
        output_dir: Optional[str] = None,
    ) -> Path:
        """
        将识别+翻译结果保存为 JSON 文件。

        JSON 文件命名: {source_filename}_page{page_num}_{timestamp}.json

        Args:
            source_file: 源文件名
            image_size: 图像尺寸 (width, height)
            texts: 识别翻译结果列表
            stats: 统计信息
            page_num: 页码（对多页 PDF）
            preprocessing_config: 使用的预处理配置（用于追溯）
            output_dir: 输出目录，默认使用配置中的目录

        Returns:
            输出文件的 Path 对象
        """
        if not texts:
            self._logger.warning(f"无翻译结果可保存: {source_file}")
            return None

        # ---- 准备输出目录 ----
        if output_dir is None:
            output_dir = 'output'
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- 计算统计信息 ----
        if stats is None:
            stats = self._compute_statistics(texts)

        # ---- 构建输出数据结构 ----
        result = {
            'source_file': str(source_file),
            'processed_at': datetime.now().isoformat(),
            'page': page_num,
            'image_size': {
                'width': image_size[0],
                'height': image_size[1],
            },
            'preprocessing': preprocessing_config or {},
            'texts': texts,
            'statistics': stats,
        }

        # ---- 写入 JSON ----
        # 安全文件名（去掉特殊字符）
        safe_name = Path(source_file).stem
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

        json_filename = f"{safe_name}_page{page_num}_{timestamp}.json"
        json_path = out_dir / json_filename

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        self._logger.info(
            f"翻译结果已保存: {json_path} "
            f"(共 {len(texts)} 条, 已翻译 {stats.get('translated', 0)} 条)"
        )

        return json_path

    def save_batch_result(
        self,
        source_file: str,
        page_results: List[Dict],
        output_dir: Optional[str] = None,
    ) -> List[Path]:
        """
        批量保存多页 PDF 的翻译结果。

        Args:
            source_file: 源文件名
            page_results: 每页的结果列表 [{'page': 1, 'image_size': ..., 'texts': ...}, ...]
            output_dir: 输出目录

        Returns:
            输出文件路径列表
        """
        saved_files = []

        for page_data in page_results:
            path = self.save_result(
                source_file=source_file,
                image_size=page_data['image_size'],
                texts=page_data['texts'],
                stats=page_data.get('stats'),
                page_num=page_data.get('page', 1),
                output_dir=output_dir,
            )
            if path:
                saved_files.append(path)

        self._logger.info(
            f"批量保存完成: {source_file} → {len(saved_files)} 个文件"
        )
        return saved_files

    # ============================================================
    # 结果加载
    # ============================================================

    @staticmethod
    def load_result(json_path: str) -> Optional[Dict]:
        """
        从 JSON 文件加载之前保存的翻译结果。

        Args:
            json_path: JSON 文件路径

        Returns:
            解析后的结果字典，失败时返回 None
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logging.getLogger(__name__).error(f"加载结果失败: {json_path} - {e}")
            return None

    @staticmethod
    def list_results(output_dir: Optional[str] = None) -> List[Dict]:
        """
        列出所有已保存的结果文件。

        Returns:
            [{'path': str, 'filename': str, 'processed_at': str, 'size_kb': float}, ...]
        """
        if output_dir is None:
            output_dir = 'output'
        out_dir = Path(output_dir)

        if not out_dir.exists():
            return []

        results = []
        for json_file in sorted(out_dir.glob('*.json'), reverse=True):
            try:
                stat = json_file.stat()
                # 尝试读取处理时间
                processed_at = ""
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        processed_at = data.get('processed_at', '')
                except Exception:
                    pass

                results.append({
                    'path': str(json_file),
                    'filename': json_file.name,
                    'processed_at': processed_at,
                    'size_kb': round(stat.st_size / 1024, 1),
                })
            except OSError:
                continue

        return results

    # ============================================================
    # 统计计算
    # ============================================================

    @staticmethod
    def _compute_statistics(texts: List[Dict]) -> Dict[str, Any]:
        """
        从识别结果计算统计信息。

        Args:
            texts: 识别结果列表

        Returns:
            统计字典
        """
        total = len(texts)
        if total == 0:
            return {
                'total_detected': 0,
                'translated': 0,
                'unmatched': 0,
                'avg_confidence': 0.0,
            }

        translated = sum(
            1 for t in texts
            if t.get('translated') and t['translated'] != '[未翻译]'
        )
        unmatched = total - translated

        confidences = [t.get('confidence', 0) for t in texts]
        avg_confidence = sum(confidences) / total if confidences else 0.0

        return {
            'total_detected': total,
            'translated': translated,
            'unmatched': unmatched,
            'avg_confidence': round(avg_confidence, 4),
        }


# ============================================================
# 全局存储管理器单例
# ============================================================
_store_instance: Optional[DataStore] = None


def get_store(config: Optional[dict] = None) -> DataStore:
    """获取数据存储管理器单例"""
    global _store_instance
    if _store_instance is None:
        _store_instance = DataStore(config)
    return _store_instance
