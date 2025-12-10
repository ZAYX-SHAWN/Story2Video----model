from pathlib import Path
import ffmpeg
from app_local.core.logging import logger


def merge_clip(video_path: Path, audio_path: Path, output_path: Path) -> bool:
    """将视频和音频合并为最终片段，音频不存在则生成静音"""
    if not video_path.exists():
        return False
    try:
        audio_dur = float(ffmpeg.probe(str(audio_path))['format']['duration']) if audio_path.exists() else 5.0
        input_v = ffmpeg.input(str(video_path), stream_loop=-1)
        input_a = ffmpeg.input(str(audio_path)) if audio_path.exists() else ffmpeg.input('anullsrc', f='lavfi', t=audio_dur)
        (
            ffmpeg
            .output(input_v, input_a, str(output_path), vcodec='libx264', acodec='aac', t=audio_dur, shortest=None)
            .overwrite_output()
            .run(quiet=True)
        )
        return True
    except Exception as e:
        logger.error(f"合并失败: {e}")
        return False


def concat_clips(list_file: Path, final_out: Path) -> bool:
    try:
        ffmpeg.input(str(list_file), f='concat', safe=0).output(str(final_out), c='copy').overwrite_output().run(quiet=True)
        return True
    except Exception as e:
        logger.error(f"拼接失败: {e}")
        return False

