from typing import List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks

from app_local.core.logging import logger
from app_local.core.config import OUTPUT_DIR, TEST_FAST_RETURN, LOCAL_INFERENCE, COMFY_HOSTS_LIST, PIXVERSE_MAX_CONCURRENCY
from app_local.models.schemas import (
    CreateStoryboardRequest, CreateStoryboardResponse,
    RegenerateShotRequest, RegenerateShotResponse,
    RenderVideoRequest, RenderVideoResponse,
    OperationStatus, Shot
)
from app_local.services.llm import generate_storyboard_shots, optimize_i2v_response
from app_local.services.comfy import run_t2i, run_i2v
from app_local.services.ffmpeg_merge import concat_clips
import shutil
from app_local.services.oss import upload_to_oss
from app_local.storage.repository import (
    update_operation, upsert_story, save_story_shots,
    upsert_shot, update_story_video_url, get_story_shots
)


# 复制 test.py 中的工作流模板（真实项目可从文件加载或配置中心提供）
COMFY_WORKFLOW_T2I = {
  "3": {"inputs": {"seed": 42, "steps": 20, "cfg": 4, "sampler_name": "euler", "scheduler": "simple", "denoise": 1, "model": ["66", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["58", 0]}, "class_type": "KSampler"},
  "6": {"inputs": {"text": "PLACEHOLDER_PROMPT", "clip": ["38", 0]}, "class_type": "CLIPTextEncode"},
  "7": {"inputs": {"text": "", "clip": ["38", 0]}, "class_type": "CLIPTextEncode"},
  "8": {"inputs": {"samples": ["3", 0], "vae": ["39", 0]}, "class_type": "VAEDecode"},
  "37": {"inputs": {"unet_name": "qwen_image_fp8_e4m3fn.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
  "38": {"inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}, "class_type": "CLIPLoader"},
  "39": {"inputs": {"vae_name": "qwen_image_vae.safetensors"}, "class_type": "VAELoader"},
  "58": {"inputs": {"width": 1280, "height": 720, "batch_size": 2}, "class_type": "EmptySD3LatentImage"},
  "60": {"inputs": {"filename_prefix": "T2I_Keyframe", "images": ["8", 0]}, "class_type": "SaveImage"},
  "66": {"inputs": {"shift": 3.5, "model": ["37", 0]}, "class_type": "ModelSamplingAuraFlow"}
}

COMFY_WORKFLOW_I2V = {
  "8": {"inputs": {"samples": ["125", 0], "vae": ["10", 0]}, "class_type": "VAEDecode"},
  "10": {"inputs": {"vae_name": "hunyuanvideo15_vae_fp16.safetensors"}, "class_type": "VAELoader"},
  "11": {"inputs": {"clip_name1": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "clip_name2": "byt5_small_glyphxl_fp16.safetensors", "type": "hunyuan_video_15", "device": "default"}, "class_type": "DualCLIPLoader"},
  "12": {"inputs": {"unet_name": "hunyuanvideo1.5_720p_i2v_fp16.safetensors", "weight_dtype": "default"}, "class_type": "UNETLoader"},
  "44": {"inputs": {"text": "PLACEHOLDER_PROMPT", "clip": ["11", 0]}, "class_type": "CLIPTextEncode"},
  "78": {"inputs": {"width": 1280, "height": 720, "length": 85, "batch_size": 2, "positive": ["44", 0], "negative": ["93", 0], "vae": ["10", 0], "start_image": ["80", 0], "clip_vision_output": ["79", 0]}, "class_type": "HunyuanVideo15ImageToVideo"},
  "79": {"inputs": {"crop": "center", "clip_vision": ["81", 0], "image": ["80", 0]}, "class_type": "CLIPVisionEncode"},
  "80": {"inputs": {"image": "PLACEHOLDER_IMAGE_FILENAME"}, "class_type": "LoadImage"},
  "81": {"inputs": {"clip_name": "sigclip_vision_patch14_384.safetensors"}, "class_type": "CLIPVisionLoader"},
  "93": {"inputs": {"text": "blur, distortion, low quality, watermark", "clip": ["11", 0]}, "class_type": "CLIPTextEncode"},
  "101": {"inputs": {"fps": 24, "images": ["8", 0]}, "class_type": "CreateVideo"},
  "102": {"inputs": {"filename_prefix": "Hunyuan_I2V", "format": "auto", "codec": "h264", "video": ["101", 0]}, "class_type": "SaveVideo"},
  "125": {"inputs": {"noise": ["127", 0], "guider": ["129", 0], "sampler": ["128", 0], "sigmas": ["126", 0], "latent_image": ["78", 2]}, "class_type": "SamplerCustomAdvanced"},
  "126": {"inputs": {"scheduler": "simple", "steps": 20, "denoise": 1, "model": ["12", 0]}, "class_type": "BasicScheduler"},
  "127": {"inputs": {"noise_seed": 0}, "class_type": "RandomNoise"},
  "128": {"inputs": {"sampler_name": "euler"}, "class_type": "KSamplerSelect"},
  "129": {"inputs": {"cfg": 6, "model": ["130", 0], "positive": ["78", 0], "negative": ["78", 1]}, "class_type": "CFGGuider"},
  "130": {"inputs": {"shift": 7, "model": ["12", 0]}, "class_type": "ModelSamplingSD3"}
}


router = APIRouter(prefix="/api/v1")


@router.post("/storyboard/create", response_model=CreateStoryboardResponse)
def create_storyboard(req: CreateStoryboardRequest, background_tasks: BackgroundTasks):
    logger.info(f"CreateStoryboardTask 开�? op={req.operation_id}, story={req.story_id}")
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
    num_gpu_workers = 2
    with ThreadPoolExecutor(max_workers=num_gpu_workers) as ex:
        futures = []
        for shot in processed_shots:
            keyframe = t2i_dir / f"shot_{shot.sequence:02d}_keyframe.png"
            text_prompt = shot.detail or ""
            futures.append(ex.submit(run_t2i, text_prompt, keyframe, COMFY_WORKFLOW_T2I))
        for _ in as_completed(futures):
            pass

    # 为每个已生成的关键帧上传�?OSS，并设置 image_url（HTTP URL�?    for shot in processed_shots:
        keyframe = t2i_dir / f"shot_{shot.sequence:02d}_keyframe.png"
        if keyframe.exists():
            object_key = f"users/{req.user_id}/stories/{req.story_id}/t2i/shot_{shot.sequence:02d}/keyframe.png"
            url = upload_to_oss(object_key, keyframe)
            shot.image_url = url or f"/static/{req.user_id}/{req.story_id}/T2I/{keyframe.name}"

    # 保存 shots 初始结构到“数据库�?    save_story_shots(req.user_id, req.story_id, [shot.dict() for shot in processed_shots])
    import json as _json
    (json_dir / "shots.json").write_text(_json.dumps({"story_id": req.story_id, "shots": [shot.dict() for shot in processed_shots]}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        raw_path = OUTPUT_DIR / "ollama_raw.txt"
        if raw_path.exists():
            (json_dir / "ollama_raw.txt").write_text(raw_path.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

    # 仅生成分镜并落库，按接口规范立即标记�?Success
    update_operation(req.user_id, req.operation_id, "Success")
    return CreateStoryboardResponse(operation=OperationStatus(operation_id=req.operation_id, status="Success"), shots=processed_shots)


@router.post("/shot/regenerate", response_model=RegenerateShotResponse)
def regenerate_shot(req: RegenerateShotRequest, background_tasks: BackgroundTasks):
    logger.info(f"RegenerateShot 开�? op={req.operation_id}, user={req.user_id}, story={req.story_id}, shot={req.shot_id}")
    # 读取已有分镜，保留除 detail 以外的字�?    shots_list = get_story_shots(req.user_id, req.story_id)
    existed = None
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

    # 生成关键帧（同步），避免返回�?URL
    base_dir = OUTPUT_DIR / req.user_id / req.story_id
    t2i_dir = base_dir / "T2I"
    t2i_dir.mkdir(parents=True, exist_ok=True)
    keyframe = t2i_dir / f"{req.shot_id}_keyframe.png"
    text_prompt = detail_text
    if not text_prompt and existed and existed.get('detail'):
        text_prompt = existed['detail']
    if not text_prompt and existed:
        text_prompt = f"参考上一帧风格，保持镜头语义一致：{existed.get('subject','')}。{existed.get('narration','')}"

    run_t2i(text_prompt, keyframe, COMFY_WORKFLOW_T2I)
    k_obj = f"users/{req.user_id}/stories/{req.story_id}/t2i/{req.shot_id}/keyframe.png"
    k_url = upload_to_oss(k_obj, keyframe)

    # 构造返回的 Shot，保留原有字段，仅更�?detail �?image_url
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
    logger.info("RegenerateShot 完成：保留其他字段，只更�?detail �?image_url")
    return RegenerateShotResponse(operation=OperationStatus(operation_id=req.operation_id, status="Success"), shot=shot)


@router.post("/video/render", response_model=RenderVideoResponse)
def render_video(req: RenderVideoRequest, background_tasks: BackgroundTasks):
    logger.info(f"RenderVideo 开�? op={req.operation_id}, story={req.story_id}")
    base_dir = OUTPUT_DIR / req.user_id / req.story_id
    json_dir = base_dir / "json"
    t2i_dir = base_dir / "T2I"
    i2v_dir = base_dir / "I2V"
    for d in (json_dir, t2i_dir, i2v_dir):
        d.mkdir(parents=True, exist_ok=True)
    final_out = i2v_dir / "final.mp4"

    def worker_concat():
        shots_list = get_story_shots(req.user_id, req.story_id)
        if shots_list:
            # 优化图生视频响应
            logger.info(f"开始优化图生视频响应，�?{len(shots_list)} 个分�?)
            try:
                optimized_result = optimize_i2v_response({"shots": shots_list})
                shots_list = optimized_result.get("shots", shots_list)
                logger.info("图生视频响应优化完成")
            except Exception as e:
                logger.error(f"图生视频响应优化失败: {e}")
                # 优化失败时使用原始数据继续处�?                logger.info("使用原始数据继续处理")
            max_workers = (PIXVERSE_MAX_CONCURRENCY if not LOCAL_INFERENCE else len(COMFY_HOSTS_LIST)) or 1
            with ThreadPoolExecutor(max_workers=max_workers or 1) as ex:
                futures = []
                for s in shots_list:
                    seq = int(s.get('sequence', 0))
                    keyframe = t2i_dir / f"shot_{seq:02d}_keyframe.png"
                    video_raw = i2v_dir / f"shot_{seq:02d}_raw.mp4"
                    text_prompt = s.get('detail') or ""
                    tone = s.get('tone') or ''
                    narr = s.get('narration') or ''
                    lip_sync_tts_content = narr if not isinstance(narr, dict) else (narr.get(tone) or narr.get('default') or next(iter(narr.values()), ''))
                    futures.append(ex.submit(run_i2v, keyframe, text_prompt, video_raw, COMFY_WORKFLOW_I2V, req.user_id, req.story_id, lip_sync_tts_content))
                for _ in as_completed(futures):
                    pass
            for s in shots_list:
                seq = int(s.get('sequence', 0))
                video_raw = i2v_dir / f"shot_{seq:02d}_raw.mp4"
                video_final = i2v_dir / f"shot_{seq:02d}_final.mp4"
                # 取消 TTS，保�?Pixverse 自带音频
                shutil.copyfile(video_raw, video_final)
                obj = f"users/{req.user_id}/stories/{req.story_id}/i2v/shot_{seq:02d}/final.mp4"
                url = upload_to_oss(obj, video_final)
                s['video_url'] = url or f"/static/{req.user_id}/{req.story_id}/I2V/{video_final.name}"
                upsert_shot(req.user_id, req.story_id, s.get('id', f'shot_{seq:02d}'), s)
            save_story_shots(req.user_id, req.story_id, shots_list)
        valid_clips = sorted([p for p in i2v_dir.glob(f"shot_*_final.mp4")])
        if valid_clips:
            list_file = i2v_dir / "concat_list.txt"
            with list_file.open("w", encoding="utf-8") as f:
                for p in valid_clips:
                    f.write(f"file '{p.resolve()}'\n")
            concat_clips(list_file, final_out)
        mv_obj = f"users/{req.user_id}/stories/{req.story_id}/movie/final.mp4"
        mv_url = upload_to_oss(mv_obj, final_out)
        update_story_video_url(req.user_id, req.story_id, mv_url or str(final_out.resolve()))
        update_operation(req.user_id, req.operation_id, "Success")
        logger.info("RenderVideo 完成，Operation 标记�?Success")
        return mv_url or f"/static/{req.user_id}/{req.story_id}/I2V/{final_out.name}"

    update_operation(req.user_id, req.operation_id, "Running")
    # if TEST_FAST_RETURN:
    #     logger.info(f"TEST_FAST_RETURN 模式，直接返�?)
    #     placeholder = next(i2v_dir.glob("final.mp4"), final_out)
    #     return RenderVideoResponse(operation=OperationStatus(operation_id=req.operation_id, status="Running"), video_url=f"/static/{req.user_id}/{req.story_id}/I2V/{placeholder.name}")
    video_url = worker_concat()
    return RenderVideoResponse(operation=OperationStatus(operation_id=req.operation_id, status="Success"), video_url=video_url)
