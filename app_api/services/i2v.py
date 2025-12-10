"""
图生视频服务 - 使用 DashScope wan2.5-preview API
"""
# -*- coding: utf-8 -*-
import random
import time
import uuid
from pathlib import Path
from http import HTTPStatus
import requests
import json
import dashscope
from dashscope import VideoSynthesis

from app_api.core.config import DASHSCOPE_API_KEY, OUTPUT_DIR
from app_api.services.oss import upload_to_oss
from app_api.core.logging import logger


def run_i2v(
    start_image: Path, 
    text_prompt: str, 
    target_path: Path, 
    user_id: str | None = None, 
    story_id: str | None = None, 
    audio_url: str | None = None
) -> bool:
    """
    使用 DashScope wan2.5-preview API 生成图生视频
    
    Args:
        start_image: 起始图片路径
        text_prompt: 文本提示词
        target_path: 视频保存路径
        user_id: 用户ID
        story_id: 故事ID
        audio_url: 音频URL（可选）
    
    Returns:
        bool: 成功返回 True，失败返回 False
    """
    try:
        if not DASHSCOPE_API_KEY:
            logger.error("DashScope API Key 未配置")
            return False
        
        # 检查起始图文件是否存在
        if not start_image.exists():
            logger.error(f"起始图文件不存在: {start_image}")
            return False
        
        logger.info(
            f"准备上传起始图到 OSS: {start_image}, "
            f"文件大小: {start_image.stat().st_size} bytes"
        )
        
        # 上传起始图到 OSS，得到可访问的 image_url
        image_url = upload_to_oss(f"users/temp/i2v_inputs/{target_path.stem}.png", start_image)
        if not image_url:
            logger.error(
                f"上传起始图到 OSS 失败，无法提供 image_url 给 wan2.5-preview。文件: {start_image}"
            )
            return False
        
        logger.info(f"起始图上传成功，image_url: {image_url}")
        
        # 生成 trace id 用于追踪
        trace_id = str(uuid.uuid4())
        
        # 创建回调记录目录
        cb_dir = OUTPUT_DIR / (user_id or 'unknown') / (story_id or 'unknown') / 'api_callback'
        cb_dir.mkdir(parents=True, exist_ok=True)
        
        # 设置 DashScope API Key
        dashscope.api_key = DASHSCOPE_API_KEY
        
        # 调用 wan2.5-preview 异步 API
        logger.info(
            f"I2V 开始，使用 wan2.5-preview 模型, trace_id: {trace_id}, "
            f"audio_url: {audio_url or 'None'}"
        )
        
        # 构建 API 调用参数
        api_params = {
            'api_key': dashscope.api_key,
            'model': 'wan2.5-i2v-preview',
            'prompt': text_prompt,
            'img_url': image_url,
            'resolution': "480P",
            'prompt_extend': False,
            'watermark': False,
            'negative_prompt': "",
            'seed': random.randint(1, 99999),
            'duration': 5
        }
        
        # 如果提供了 audio_url，添加到参数中
        if audio_url:
            api_params['audio_url'] = audio_url
            logger.info(f"使用自定义音频: {audio_url}")
        
        rsp = VideoSynthesis.async_call(**api_params)
        
        # 记录异步调用响应
        try:
            async_response = {
                'status_code': rsp.status_code,
                'request_id': rsp.request_id if hasattr(rsp, 'request_id') else None,
                'output': rsp.output.__dict__ if hasattr(rsp, 'output') else None,
                'code': rsp.code if hasattr(rsp, 'code') else None,
                'message': rsp.message if hasattr(rsp, 'message') else None,
            }
            (cb_dir / f'wan25_async_{trace_id}.json').write_text(
                json.dumps(async_response, ensure_ascii=False, indent=2), 
                encoding='utf-8'
            )
        except Exception as e:
            logger.warning(f"记录异步调用响应失败: {e}")
        
        if rsp.status_code != HTTPStatus.OK:
            logger.error(
                f'wan2.5-preview 任务创建失败, status_code: {rsp.status_code}, '
                f'code: {rsp.code}, message: {rsp.message}'
            )
            return False
        
        task_id = rsp.output.task_id
        logger.info(f"wan2.5-preview 任务创建成功，task_id: {task_id}, trace_id: {trace_id}")
        
        # 手动轮询任务状态，留足超时时间
        max_tries = 600  # 最多轮询 600 次
        poll_interval = 2  # 每次间隔 2 秒，最长约 20 分钟
        tries = 0
        
        while tries < max_tries:
            try:
                # 使用 fetch 方法查询任务状态
                status_rsp = VideoSynthesis.fetch(task_id)
                
                # 记录轮询响应
                try:
                    poll_response = {
                        'status_code': status_rsp.status_code,
                        'request_id': status_rsp.request_id if hasattr(status_rsp, 'request_id') else None,
                        'output': status_rsp.output.__dict__ if hasattr(status_rsp, 'output') else None,
                        'code': status_rsp.code if hasattr(status_rsp, 'code') else None,
                        'message': status_rsp.message if hasattr(status_rsp, 'message') else None,
                    }
                    (cb_dir / f'wan25_poll_{trace_id}_{tries}.json').write_text(
                        json.dumps(poll_response, ensure_ascii=False, indent=2), 
                        encoding='utf-8'
                    )
                except Exception as e:
                    logger.warning(f"记录轮询响应失败: {e}")
                
                if status_rsp.status_code != HTTPStatus.OK:
                    logger.warning(
                        f"轮询状态异常 (try {tries}/{max_tries}): "
                        f"status_code={status_rsp.status_code}"
                    )
                    time.sleep(poll_interval)
                    tries += 1
                    continue
                
                # 检查任务状态
                task_status = status_rsp.output.task_status if hasattr(status_rsp.output, 'task_status') else None
                logger.info(f"任务状态 (try {tries}/{max_tries}): {task_status}")
                
                # SUCCEEDED 表示任务完成
                if task_status == 'SUCCEEDED':
                    video_url = status_rsp.output.video_url if hasattr(status_rsp.output, 'video_url') else None
                    if not video_url:
                        logger.warning(
                            f"任务成功但未返回 video_url (try {tries}/{max_tries})"
                        )
                        time.sleep(poll_interval)
                        tries += 1
                        continue
                    
                    # 下载视频
                    logger.info(f"wan2.5-preview 视频生成成功，正在下载 {video_url}")
                    v = requests.get(video_url, timeout=300)
                    v.raise_for_status()
                    with open(target_path, 'wb') as f:
                        f.write(v.content)
                    
                    logger.info(
                        f"wan2.5-preview 视频下载成功: {video_url} -> {target_path}"
                    )
                    return True
                
                # FAILED 表示任务失败
                elif task_status == 'FAILED':
                    error_msg = status_rsp.message if hasattr(status_rsp, 'message') else 'Unknown error'
                    logger.error(f"wan2.5-preview 任务失败: {error_msg}")
                    return False
                
                # 其他状态继续等待
                time.sleep(poll_interval)
                tries += 1
                
            except Exception as e:
                logger.warning(
                    f"轮询过程中出现异常 (try {tries}/{max_tries}): {e}"
                )
                time.sleep(poll_interval)
                tries += 1
        
        logger.error(f"wan2.5-preview 任务超时未完成，已轮询 {tries} 次")
        return False
        
    except Exception as e:
        logger.error(f"wan2.5-preview I2V 调用失败: {e}")
        return False