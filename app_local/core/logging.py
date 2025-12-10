from loguru import logger
import sys
from datetime import datetime
from pathlib import Path
from app_local.core.config import OUTPUT_DIR

# 日志初始化：统一设置格式和级别，输出�?stdout；可扩展文件输出

logger.remove()
logger.add(sys.stdout, level="INFO", 
           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>")

_log_dir: Path = OUTPUT_DIR / "log"
_log_dir.mkdir(parents=True, exist_ok=True)
_start_date = datetime.now().strftime("%Y%m%d")
logger.add(str((_log_dir / f"{_start_date}.log").resolve()), level="INFO", enqueue=True)

__all__ = ["logger"]
