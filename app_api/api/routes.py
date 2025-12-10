from typing import List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks

from app_api.core.logging import logger
from app_api.core.config import OUTPUT_DIR
from app_api.models.schemas import (
    CreateStoryboardRequest, CreateStoryboardResponse,
    RegenerateShotRequest, RegenerateShotResponse,
    RenderVideoRequest, RenderVideoResponse,
    OperationStatus, Shot
)
from app_api.services.llm import generate_storyboard_shots, optimize_i2v_response, run_t2i_api
from app_api.services.i2v import run_i2v
from app_api.services.ffmpeg_merge import concat_clips
from app_api.services.tts_v2 import generate_tts_audio
import shutil
from app_api.services.oss import upload_to_oss
from app_api.storage.repository import (
    update_operation, upsert_story, save_story_shots,
    upsert_shot, update_story_video_url, get_story_shots
)


router = APIRouter(prefix="/api/v1")


@router.post("/storyboard/create", response_model=CreateStoryboardResponse)
def create_storyboard(req: CreateStoryboardRequest, background_tasks: BackgroundTasks):
    logger.info(f"CreateStoryboardTask 开始，op={req.operation_id}, story={req.story_id}")
    upsert_story(req.user_id, req.story_id, req.display_name, req.style, req.script_content)
    try:
        shots_raw = generate_storyboard_shots("style:" + req.style + ":" + req.script_content)
    except Exception as e:
        update_operation(req.user_id, req.operation_id, "Failed", detail=str(e))
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail="LLM 分镜生成失败，请稍后重试")
    processed_shots: List[Shot] = []
    for i, s in enumerate(shots_raw):
        shot = Shot(
            id=s.get('id', f'shot_{i+1:02d}'),
            sequence=s.get('sequence', i+1),
            subject=s.get('subject'),
            detail=s.get('detail'),
            camera=s.get('camera'),
            narration=s.get('narration'),
            tone=s.get('tone'),
            style=s.get('style'),
        )
        processed_shots.append(shot)
    # 目录结构：OUTPUT_DIR/user_id/story_id/{json,T2I,I2V}
    base_dir = OUTPUT_DIR / req.user_id / req.story_id
    json_dir = base_dir / "json"
    t2i_dir = base_dir / "T2I"
    i2v_dir = base_dir / "I2V"
    for d in (json_dir, t2i_dir, i2v_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 在生成分镜后，同步执行文生图（生成关键帧）并生成 image_url
    num_gpu_workers = 2  # 文生图并发数设置
    with ThreadPoolExecutor(max_workers=num_gpu_workers) as ex:
        futures = []
        for shot in processed_shots:
            keyframe = t2i_dir / f"shot_{shot.sequence:02d}_keyframe.png"
            text_prompt = shot.detail or ""
            futures.append(ex.submit(run_t2i_api, text_prompt, keyframe))
        for _ in as_completed(futures):
            pass

    # 为每个已生成的关键帧上传到 OSS，并设置 image_url（HTTP URL）
    for shot in processed_shots:
        keyframe = t2i_dir / f"shot_{shot.sequence:02d}_keyframe.png"
        logger.info(f"检查关键帧: {keyframe}, 存在: {keyframe.exists()}")
        if keyframe.exists():
            object_key = f"users/{req.user_id}/stories/{req.story_id}/t2i/shot_{shot.sequence:02d}/keyframe.png"
            url = upload_to_oss(object_key, keyframe)
            shot.image_url = url or f"/static/{req.user_id}/{req.story_id}/T2I/{keyframe.name}"
            logger.info(f"Shot {shot.sequence} 图片URL: {shot.image_url}")
        else:
            logger.warning(f"Shot {shot.sequence} 关键帧文件不存在: {keyframe}")

    # 保存 shots 初始结构到“数据库”
    save_story_shots(req.user_id, req.story_id, [shot.dict() for shot in processed_shots])
    import json as _json
    (json_dir / "shots.json").write_text(_json.dumps({"story_id": req.story_id, "shots": [shot.dict() for shot in processed_shots]}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 仅生成分镜并落库，按接口规范立即标记 Success
    update_operation(req.user_id, req.operation_id, "Success")
    return CreateStoryboardResponse(operation=OperationStatus(operation_id=req.operation_id, status="Success"), shots=processed_shots)


@router.post("/shot/regenerate", response_model=RegenerateShotResponse)
def regenerate_shot(req: RegenerateShotRequest, background_tasks: BackgroundTasks):
    logger.info(f"RegenerateShot 开始，op={req.operation_id}, user={req.user_id}, story={req.story_id}, shot={req.shot_id}")
    # 读取已有分镜，保留除 detail 以外的字典    shots_list = get_story_shots(req.user_id, req.story_id)
    existed = None
    shots_list = get_story_shots(req.user_id, req.story_id)
    for s in shots_list:
        if s.get('id') == req.shot_id:
            existed = s
            break

    detail_text = (req.detail or req.details or req.prompt or (existed or {}).get('detail') or "").strip()
    subject = req.subject if req.subject is not None else (existed or {}).get('subject')
    camera = req.camera if req.camera is not None else (existed or {}).get('camera')
    narration = req.narration if req.narration is not None else (existed or {}).get('narration')
    tone = req.tone if req.tone is not None else (existed or {}).get('tone')
    sequence = (existed or {}).get('sequence') or 0

    # 生成关键帧（同步），避免返回 URL
    base_dir = OUTPUT_DIR / req.user_id / req.story_id
    t2i_dir = base_dir / "T2I"
    t2i_dir.mkdir(parents=True, exist_ok=True)
    keyframe = t2i_dir / f"{req.shot_id}_keyframe.png"
    text_prompt = detail_text
    if not text_prompt and existed and existed.get('detail'):
        text_prompt = existed['detail']
    if not text_prompt and existed:
        text_prompt = f"参考上一帧风格，保持镜头语义一致：{existed.get('subject','')}。{existed.get('narration','')}"

    run_t2i_api(text_prompt, keyframe)
    k_obj = f"users/{req.user_id}/stories/{req.story_id}/t2i/{req.shot_id}/keyframe.png"
    k_url = upload_to_oss(k_obj, keyframe)

    # 构造返回的 Shot，保留原有字段，仅更新detail 和image_url
    shot = Shot(
        id=req.shot_id,
        sequence=sequence,
        subject=subject,
        detail=detail_text,
        camera=camera,
        narration=narration,
        tone=tone,
        image_url=k_url or f"/static/{req.user_id}/{req.story_id}/T2I/{keyframe.name}",
        video_url=(existed or {}).get('video_url')
    )

    # 持久化当前分镜与列表
    upsert_shot(req.user_id, req.story_id, req.shot_id, shot.dict())
    if shots_list:
        for s in shots_list:
            if s.get('id') == req.shot_id:
                s.update({
                    'detail': shot.detail,
                    'image_url': shot.image_url,
                    'subject': shot.subject,
                    'camera': shot.camera,
                    'narration': shot.narration,
                    'tone': shot.tone,
                    'sequence': shot.sequence,
                    'video_url': shot.video_url,
                })
                break
        save_story_shots(req.user_id, req.story_id, shots_list)

    update_operation(req.user_id, req.operation_id, "Success")
    logger.info("RegenerateShot 完成：保留其他字段，只更新detail和image_url")
    return RegenerateShotResponse(operation=OperationStatus(operation_id=req.operation_id, status="Success"), shot=shot)


@router.post("/video/render", response_model=RenderVideoResponse)
def render_video(req: RenderVideoRequest, background_tasks: BackgroundTasks):
    # 使用新的提取方法获取 IDs
    try:
        operation_id = req.get_operation_id()
        story_id = req.get_story_id()
        user_id = req.get_user_id()
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    
    logger.info(f"RenderVideo 开始 op={operation_id}, story={story_id}, user={user_id}")
    
    # 如果提供shots，先保存到数据库/文件系统
    if req.shots:
        logger.info(f"使用请求中提供的 {len(req.shots)} 个 shots")
        save_story_shots(user_id, story_id, [shot.dict() for shot in req.shots])
    
    base_dir = OUTPUT_DIR / user_id / story_id
    json_dir = base_dir / "json"
    t2i_dir = base_dir / "T2I"
    i2v_dir = base_dir / "I2V"
    for d in (json_dir, t2i_dir, i2v_dir):
        d.mkdir(parents=True, exist_ok=True)
    final_out = i2v_dir / "final.mp4"

    def worker_concat():
        shots_list = get_story_shots(user_id, story_id)
        if shots_list:
            # 优化图生视频响应
            logger.info(f"开始优化图生视频响应，包含{len(shots_list)}个分镜")
            try:
                optimized_result = optimize_i2v_response({"shots": shots_list})
                shots_list = optimized_result.get("shots", shots_list)
                logger.info("图生视频响应优化完成")
            except Exception as e:
                logger.error(f"图生视频响应优化失败: {e}")
                # 优化失败时使用原始数据继续处理
                logger.info("使用原始数据继续处理")
            # 固定并发数为5
            max_workers = 5
            logger.info(f"开始生成视频，并发数 {max_workers}")
            
            # 定义带重试的视频生成函数
            def run_i2v_with_retry(keyframe, text_prompt, video_raw, user_id, story_id, shot_seq, audio_url, max_retries=5):
                """带重试机制的视频生成函数"""
                for attempt in range(1, max_retries + 1):
                    try:
                        logger.info(f"Shot {shot_seq}: 开始生成视频(尝试 {attempt}/{max_retries})")
                        success = run_i2v(keyframe, text_prompt, video_raw, user_id, story_id, audio_url)
                        
                        if success:
                            logger.info(f"Shot {shot_seq}: 视频生成成功 (尝试 {attempt}/{max_retries})")
                            return True
                        else:
                            logger.warning(f"Shot {shot_seq}: 视频生成失败 (尝试 {attempt}/{max_retries})")
                            if attempt < max_retries:
                                import time
                                wait_time = min(2 ** attempt, 30)  # 指数退避，最多等待30秒
                                logger.info(f"Shot {shot_seq}: 等待 {wait_time} 秒后重试...")
                                time.sleep(wait_time)
                    except Exception as e:
                        logger.error(f"Shot {shot_seq}: 视频生成异常 (尝试 {attempt}/{max_retries}): {e}")
                        if attempt < max_retries:
                            import time
                            wait_time = min(2 ** attempt, 30)
                            logger.info(f"Shot {shot_seq}: 等待 {wait_time} 秒后重试...")
                            time.sleep(wait_time)
                
                logger.error(f"Shot {shot_seq}: 视频生成失败，已达到最大重试次数 {max_retries}")
                return False
            
            
            # 第一步：为所有分镜并发生成TTS 音频
            logger.info(f"开始为 {len(shots_list)} 个分镜并发生成TTS 音频 (并发数 2)")
            
            def generate_tts_for_shot(s):
                """为单个分镜生成TTS 音频"""
                narration = s.get('narration', '')
                shot_id = s.get('id', f"shot_{s.get('sequence', 0):02d}")
                
                if narration and narration.strip():
                    audio_url = generate_tts_audio(narration, user_id, story_id, shot_id)
                    s['audio_url'] = audio_url
                    if audio_url:
                        logger.info(f"Shot {shot_id}: TTS 音频已生成 {audio_url}")
                    else:
                        logger.warning(f"Shot {shot_id}: TTS 音频生成失败")
                else:
                    s['audio_url'] = None
                    logger.info(f"Shot {shot_id}: 无旁白内容，跳过 TTS 生成")
                return s
            
            # 使用线程池并发生成TTS 音频
            with ThreadPoolExecutor(max_workers=2) as tts_executor:
                tts_futures = {tts_executor.submit(generate_tts_for_shot, s): s for s in shots_list}
                for tts_future in as_completed(tts_futures):
                    try:
                        tts_future.result()
                    except Exception as e:
                        logger.error(f"TTS 生成过程中出现异常: {e}")
            
            logger.info(f"所有TTS 音频生成完成")

            
            # 第二步：并行生成视频
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {}
                for s in shots_list:
                    seq = int(s.get('sequence', 0))
                    keyframe = t2i_dir / f"shot_{seq:02d}_keyframe.png"
                    
                    # 如果 keyframe 不存在但 shot 中有 image_url，先下载图片
                    if not keyframe.exists() and s.get('image_url'):
                        import requests
                        import time
                        
                        max_retries = 3
                        download_success = False
                        
                        for retry in range(max_retries):
                            try:
                                logger.info(f"Shot {seq}: keyframe 不存在，尝试从image_url下载 (尝试 {retry + 1}/{max_retries}): {s.get('image_url')}")
                                img_resp = requests.get(s.get('image_url'), timeout=30)
                                img_resp.raise_for_status()
                                keyframe.write_bytes(img_resp.content)
                                logger.info(f"Shot {seq}: 图片下载成功: {keyframe}")
                                download_success = True
                                break
                            except Exception as e:
                                logger.warning(f"Shot {seq}: 下载 image_url 失败 (尝试 {retry + 1}/{max_retries}): {e}")
                                if retry < max_retries - 1:
                                    wait_time = 2 ** (retry + 1)  # 指数退避，最多等待30秒
                                    logger.info(f"Shot {seq}: 等待 {wait_time} 秒后重试...")
                                    time.sleep(wait_time)
                        
                        if not download_success:
                            logger.error(f"Shot {seq}: 图片下载失败，已达到最大重试次数 {max_retries}，跳过该分镜")
                            continue
                    
                    video_file = i2v_dir / f"shot_{seq:02d}.mp4"
                    text_prompt = s.get('detail') or ""
                    audio_url = s.get('audio_url')  # 获取 TTS 音频 URL
                    future = ex.submit(run_i2v_with_retry, keyframe, text_prompt, video_file, user_id, story_id, seq, audio_url)
                    futures[future] = seq
                
                # 等待所有任务完成并记录结果
                success_count = 0
                failed_count = 0
                for future in as_completed(futures):
                    seq = futures[future]
                    try:
                        result = future.result()
                        if result:
                            success_count += 1
                            logger.info(f"Shot {seq}: 最终状态- 成功")
                        else:
                            failed_count += 1
                            logger.error(f"Shot {seq}: 最终状态- 失败")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"Shot {seq}: 任务执行异常: {e}")
                
                logger.info(f"视频生成完成: 成功 {success_count} 个，失败 {failed_count} 个")
            
            for s in shots_list:
                seq = int(s.get('sequence', 0))
                video_file = i2v_dir / f"shot_{seq:02d}.mp4"
                if video_file.exists():
                    # 只保存本地路径，不上传到 OSS
                    s['video_url'] = f"/static/{user_id}/{story_id}/I2V/{video_file.name}"
                else:
                    logger.warning(f"Shot {seq}: 视频文件不存在，跳过: {video_file}")
                upsert_shot(user_id, story_id, s.get('id', f'shot_{seq:02d}'), s)
            save_story_shots(user_id, story_id, shots_list)
        valid_clips = sorted([p for p in i2v_dir.glob(f"shot_*.mp4") if p.name != "final.mp4"])
        if valid_clips:
            logger.info(f"找到 {len(valid_clips)} 个分镜视频，开始合并..")
            list_file = i2v_dir / "concat_list.txt"
            with list_file.open("w", encoding="utf-8") as f:
                for p in valid_clips:
                    f.write(f"file '{p.resolve()}'\n")
            concat_clips(list_file, final_out)
            logger.info(f"视频合并完成: {final_out}")
        else:
            logger.warning("没有找到任何分镜视频用于合并")
        
        # 只上传最终合并的视频到OSS
        if final_out.exists():
            logger.info(f"开始上传最终视频到 OSS: {final_out}")
            mv_obj = f"users/{user_id}/stories/{story_id}/movie/final.mp4"
            mv_url = upload_to_oss(mv_obj, final_out)
            if mv_url:
                logger.info(f"最终视频上传成功，OSS URL: {mv_url}")
            else:
                logger.warning(f"最终视频上传到OSS失败，使用本地路径: {final_out}")
            update_story_video_url(user_id, story_id, mv_url or str(final_out.resolve()))
            update_operation(user_id, operation_id, "Success")
            logger.info("RenderVideo 完成，Operation 标记为Success")
            return mv_url or f"/static/{user_id}/{story_id}/I2V/{final_out.name}"
        else:
            logger.error(f"最终视频文件不存在: {final_out}")
            update_operation(user_id, operation_id, "Failed", detail="视频合并失败")
            return f"/static/{user_id}/{story_id}/I2V/{final_out.name}"

    update_operation(user_id, operation_id, "Running")
    video_url = worker_concat()
    return RenderVideoResponse(operation=OperationStatus(operation_id=operation_id, status="Success"), video_url=video_url)
