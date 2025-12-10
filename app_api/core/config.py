import os
from pathlib import Path

# 配置中心：集中读取环境变量并设定默认值，便于生产环境注入和本地开发调试
PROJECT_ROOT = Path(r"D:\Story2Video-main")

OUTPUT_DIR: Path = PROJECT_ROOT / "result"

SERVICE_PORT: int = int(os.getenv("SERVICE_PORT", "12345"))

# 初始化必要目录
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# OSS 配置
OSS_ENDPOINT: str = os.getenv("OSS_ENDPOINT", "oss-cn-beijing.aliyuncs.com")
OSS_ACCESS_KEY_ID: str = os.getenv("OSS_ACCESS_KEY_ID", "")
OSS_ACCESS_KEY_SECRET: str = os.getenv("OSS_ACCESS_KEY_SECRET", "")
OSS_BUCKET: str = os.getenv("OSS_BUCKET", "bytedance-s2v")
OSS_BASE_URL: str = os.getenv("OSS_BASE_URL", "")
OSS_URL_EXPIRES: int = int(os.getenv("OSS_URL_EXPIRES", "86400"))

# DashScope API 配置
DASHSCOPE_API_KEY: str =  ""
DASHSCOPE_IMAGE_MODEL: str = os.getenv("DASHSCOPE_IMAGE_MODEL", "qwen-image-plus")
DEFAULT_IMAGE_SIZE: str = os.getenv("DEFAULT_IMAGE_SIZE", "928*1664")
API_RETRY_ATTEMPTS: int = int(os.getenv("API_RETRY_ATTEMPTS", "3"))
API_RETRY_BASE_DELAY: int = int(os.getenv("API_RETRY_BASE_DELAY", "2"))
