# -*- coding: utf-8 -*-
from pathlib import Path
import os
from app_api.core.logging import logger
from app_api.core.config import DASHSCOPE_API_KEY, OUTPUT_DIR
from app_api.services.oss import upload_to_oss


def generate_tts_audio(text: str, user_id: str, story_id: str, shot_id: str) -> str:
    """
    使用 CosyVoice 生成语音文件并上传到 OSS
    Args:
        text: 要合成的文本（narration 字段）
        user_id: 用户ID
        story_id: 故事ID
        shot_id: 分镜ID
    
    Returns:
        str: OSS 上的音频文件 URL，失败返回空字符串
    """
    if not text or not text.strip():
        logger.warning(f"TTS 文本为空，跳过生成 {user_id}/{story_id}/{shot_id}")
        return ""
    
    if not DASHSCOPE_API_KEY:
        logger.error("DASHSCOPE_API_KEY 未配置，无法生成 TTS")
        return ""
    
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer
        
        # 设置 API Key
        dashscope.api_key = DASHSCOPE_API_KEY
        
        # 生成本地文件路径
        tts_dir = OUTPUT_DIR / user_id / story_id / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件命名格式: user_id-story_id-shot_id.mp3
        filename = f"{user_id}-{story_id}-{shot_id}.mp3"
        local_path = tts_dir / filename
        
        logger.info(f"开始生成 TTS 音频: text='{text[:30]}...', file={filename}")
        
        # 初始化语音合成器并调用
        try:
            speech_synthesizer = SpeechSynthesizer(
                model='cosyvoice-v3-flash',  # 使用 v1 模型
                voice='longanyang'  # 标准女声
            )
            
            # 调用合成
            logger.info("调用 CosyVoice API...")
            audio = speech_synthesizer.call(text)
            
        except Exception as api_error:
            logger.error(f"CosyVoice API 调用异常: {api_error}")
            logger.error(f"请检查：1) API Key 是否有效 2) 是否有 CosyVoice 权限 3) 是否超出配额")
            return ""
        
        # 验证返回的音频数据
        if not audio:
            logger.error(f"TTS API 返回空数据: text='{text[:30]}...'")
            logger.error("可能原因: 1) API调用失败 2) 文本无法合成 3) 服务暂时不可用")
            return ""
        
        if not isinstance(audio, bytes):
            logger.error(f"TTS API 返回数据类型错误: {type(audio)}, text='{text[:30]}...'")
            return ""
        
        if len(audio) < 100:  # 有效的 MP3 文件应该至少有几百字节
            logger.error(f"TTS API 返回数据过小 ({len(audio)} bytes): text='{text[:30]}...'")
            return ""
        
        logger.info(f"TTS API 返回音频数据: {len(audio)} bytes")
        
        # 检查音频时长，如果小于4秒则添加静音补齐
        from io import BytesIO
        from pydub import AudioSegment
        
        try:
            audio_segment = AudioSegment.from_file(BytesIO(audio), format="mp3")
        except Exception as e:
            logger.error(f"解析 TTS 音频数据失败: {e}")
            logger.error(f"音频数据前 100 字节 (hex): {audio[:100].hex()}")
            return ""
        
        duration_ms = len(audio_segment)
        duration_sec = duration_ms / 1000.0
        logger.info(f"TTS 原始音频时长: {duration_sec:.2f} 秒")
        # wan2.5-preview 要求至少 4 秒
        MIN_DURATION_SEC = 4.0
        if duration_sec < MIN_DURATION_SEC:
            # 添加静音到末尾
            silence_duration_ms = int((MIN_DURATION_SEC - duration_sec) * 1000)
            silence = AudioSegment.silent(duration=silence_duration_ms)
            audio_segment = audio_segment + silence
            logger.info(f"音频时长不足 4 秒，添加 {silence_duration_ms/1000:.2f} 秒静音，新时长: {MIN_DURATION_SEC} 秒")
        
        # 保存到本地
        audio_segment.export(local_path, format="mp3")
        
        try:
            request_id = speech_synthesizer.get_last_request_id()
            delay = speech_synthesizer.get_first_package_delay()
            logger.info(f"TTS 音频生成成功: {filename}, requestId={request_id}, delay={delay}ms")
        except Exception:
            logger.info(f"TTS 音频生成成功: {filename}")
        
        # 上传到 OSS
        object_key = f"users/{user_id}/stories/{story_id}/tts/{filename}"
        audio_url = upload_to_oss(object_key, local_path)
        
        if audio_url:
            logger.info(f"TTS 音频上传成功: {audio_url}")
            return audio_url
        else:
            logger.warning(f"TTS 音频上传失败，返回本地路径")
            return f"/static/{user_id}/{story_id}/tts/{filename}"
            
    except Exception as e:
        logger.error(f"TTS 音频生成失败: {e}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        return ""

