import requests
from pathlib import Path
from typing import Optional

from app_local.core.config import COSYVOICE_URL
from app_local.core.logging import logger


def synthesize_tts(text: str, output_path: Path, tone: Optional[str] = None, speaker: str = "xiaoyue", speed: float = 1.0) -> bool:
    """调用 CosyVoice 生成语音并保存到文件"""
    if not text:
        return True
    payload = {"text": text, "speaker": speaker, "speed": speed}
    if tone:
        payload["style"] = tone
    try:
        resp = requests.post(COSYVOICE_URL, json=payload, timeout=60)
        output_path.write_bytes(resp.content)
        return True
    except Exception as e:
        logger.error(f"TTS 失败: {e}")
        return False

