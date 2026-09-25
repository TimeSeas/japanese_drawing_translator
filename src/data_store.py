"""结果落盘 + 日志。

JSON 里存的不只是译文，还有每条的原文、bbox、置信度和匹配方式。加 bbox
是为了把译文按原坐标叠回图纸上（否则对照图就没法和原文对上位置）；
存匹配方式是为了区分"查到的"和"猜的"——客户复核时只需要看 fuzzy 那些。
"""

import json
import logging
import logging.handlers
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LOG_FORMAT = '%(asctime)s | %(levelname)s | %(message)s'


class DataStore:
    def __init__(self, log_dir: str = 'logs', retention_days: int = 180,
                 level: str = 'INFO'):
        self.log_dir = Path(log_dir)
        self.retention_days = retention_days
        self.level = level

    def setup_logging(self, level: Optional[str] = None):
        log_level = getattr(logging, (level or self.level).upper(), logging.INFO)

        self.log_dir.mkdir(parents=True, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(log_level)

        # 重复调用（比如重开窗口）不能把 handler 叠上去，否则日志会翻倍
        if not any(isinstance(h, logging.handlers.TimedRotatingFileHandler)
                   for h in root.handlers):
            file_handler = logging.handlers.TimedRotatingFileHandler(
                filename=str(self.log_dir / 'app.log'),
                when='midnight', interval=1,
                backupCount=self.retention_days, encoding='utf-8',
            )
            file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
            root.addHandler(file_handler)

            # 控制台只放 WARNING 以上：GUI 程序，正常信息不用打扰用户
            console = logging.StreamHandler()
            console.setLevel(logging.WARNING)
            console.setFormatter(logging.Formatter(LOG_FORMAT))
            root.addHandler(console)

        self._cleanup_old_logs()
        logger.info(f"启动 | 日志目录 {self.log_dir.absolute()}")

    def _cleanup_old_logs(self):
        """TimedRotatingFileHandler 只管自己切的备份；被手动改名或
        遗留的日志文件它不管，所以每次启动再扫一遍目录。"""
        if not self.log_dir.exists():
            return

        cutoff = (datetime.now() - timedelta(days=self.retention_days)).timestamp()
        pattern = re.compile(r'^app.*\.log.*$')

        deleted = 0
        for f in self.log_dir.iterdir():
            if not (f.is_file() and pattern.match(f.name)):
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except OSError:
                pass    # 文件被占用就跳过，不值得为此报错
        if deleted:
            logger.info(f"清理了 {deleted} 个过期日志")

    def save_result(
        self,
        source_file: str,
        image_size: Tuple[int, int],
        texts: List[Dict],
        output_dir: str = 'output',
        page_num: int = 1,
        extra: Optional[dict] = None,
    ) -> Optional[Path]:
        """写一个 {源文件名}_page{N}_{时间戳}.json，返回路径。

        texts 为空时不写文件 —— 空结果没有存档价值，写了反而污染 output 目录。
        """
        if not texts:
            logger.warning(f"无识别结果，跳过保存: {source_file}")
            return None

        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        payload = {
            'source_file': str(source_file),
            'processed_at': datetime.now().isoformat(timespec='seconds'),
            'page': page_num,
            'image_size': {'width': image_size[0], 'height': image_size[1]},
            'texts': texts,
            'statistics': _statistics(texts),
        }
        if extra:
            payload.update(extra)

        # 源文件名可能带 : / \ 等字符，直接当文件名会写失败
        stem = re.sub(r'[<>:"/\\|?*]', '_', Path(source_file).stem)
        path = out_dir / f"{stem}_page{page_num}_{datetime.now():%Y%m%d_%H%M%S}.json"

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        logger.info(f"结果已保存: {path} ({len(texts)} 条)")
        return path


def _statistics(texts: List[Dict]) -> Dict:
    total = len(texts)
    if total == 0:
        return {'total_detected': 0, 'translated': 0, 'skipped': 0,
                'unmatched': 0, 'avg_confidence': 0.0}

    translated = [t for t in texts
                  if t.get('match_method') in ('exact', 'fuzzy')]
    skipped = sum(1 for t in texts if t.get('match_method') == 'skipped')

    # 平均置信度只算真翻了的那些。未匹配和无需翻译的 confidence 是占位 0，
    # 一起平均只会把这个数拉低，看不出翻译本身的质量。
    avg_conf = (sum(t.get('confidence', 0) for t in translated)
                / len(translated) if translated else 0.0)

    return {
        'total_detected': total,
        'translated': len(translated),
        'skipped': skipped,
        'unmatched': total - len(translated) - skipped,
        'avg_confidence': round(avg_conf, 4),
    }
