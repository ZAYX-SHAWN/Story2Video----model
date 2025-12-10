import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app_api.core.config import OUTPUT_DIR
from app_api.core.logging import logger


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_operation(user_id: str, operation_id: str, status: str, detail: Optional[str] = None) -> None:
    path = OUTPUT_DIR / user_id / operation_id / "json" / f"{operation_id}.json"
    payload = {"operation_id": operation_id, "status": status}
    if detail:
        payload["detail"] = detail
    _atomic_write(path, payload)
    logger.info(f"Operation 更新: {user_id}/{operation_id} -> {status}")


def upsert_story(user_id: str, story_id: str, display_name: str, style: str, script_content: str) -> None:
    path = OUTPUT_DIR / user_id / story_id / "json" / f"{story_id}.json"
    data = {
        "story_id": story_id,
        "display_name": display_name,
        "style": style,
        "script_content": script_content,
    }
    _atomic_write(path, data)
    logger.info(f"Story 保存: {user_id}/{story_id}")


def save_story_shots(user_id: str, story_id: str, shots: List[Dict[str, Any]]) -> None:
    path = OUTPUT_DIR / user_id / story_id / "json" / "shots.json"
    payload = {"story_id": story_id, "shots": shots}
    _atomic_write(path, payload)
    logger.info(f"Shots 保存: {user_id}/{story_id} -> {len(shots)} 个分镜")


def upsert_shot(user_id: str, story_id: str, shot_id: str, shot: Dict[str, Any]) -> None:
    path = OUTPUT_DIR / user_id / story_id / "json" / "shots" / f"{shot_id}.json"
    _atomic_write(path, shot)
    logger.info(f"Shot 更新: {user_id}/{story_id}/{shot_id}")


def update_story_video_url(user_id: str, story_id: str, url: str) -> None:
    path = OUTPUT_DIR / user_id / story_id / "json" / f"{story_id}.json"
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["video_url"] = url
    _atomic_write(path, data)
    logger.info(f"Story 视频地址更新: {user_id}/{story_id} -> {url}")


def get_story_shots(user_id: str, story_id: str) -> List[Dict[str, Any]]:
    path = OUTPUT_DIR / user_id / story_id / "json" / "shots.json"
    if not path.exists():
        logger.warning(f"Story 分镜文件不存在 {user_id}/{story_id}")
        return []
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        # 兼容两种结构：旧版纯数组、新版带 shots 字段的对象
        if isinstance(data, list):
            logger.info(f"Shots 加载(数组格式): {user_id}/{story_id} -> {len(data)} 个分镜")
            return data
        shots = data.get("shots", []) if isinstance(data, dict) else []
        logger.info(f"Shots 加载: {user_id}/{story_id} -> {len(shots)} 个分镜")
        return shots
    except Exception as e:
        logger.error(f"Shots 加载失败: {user_id}/{story_id}, err={e}")
        return []
