import copy
import os
import random
import shutil
import time
from pathlib import Path
from queue import Queue
from typing import Dict, Tuple
from app_local.core.config import TEST_FAST_RETURN

import requests
import json

from app_local.core.config import COMFY_HOSTS_LIST, COMFY_INPUT_DIR, COMFY_OUTPUT_DIR, LOCAL_INFERENCE, PIXVERSE_API_KEY, PIXVERSE_UPLOAD_URL, PIXVERSE_GENERATE_URL, PIXVERSE_RESULT_URL
from app_local.services.oss import upload_to_oss
from app_local.core.logging import logger


# 资源池：多个 ComfyUI 实例轮询分发
_comfy_host_queue: Queue[str] = Queue()
for url in COMFY_HOSTS_LIST:
    _comfy_host_queue.put(url)


def acquire_comfy_host() -> str:
    return _comfy_host_queue.get()


def release_comfy_host(host: str) -> None:
    _comfy_host_queue.put(host)


def execute_workflow(host: str, workflow: Dict, output_node_id: str, output_type: str) -> Tuple[str | None, str | None]:
    """调用 ComfyUI /prompt 并轮�?/history 获取结果文件名与子目�?""
    prompt_url = f"{host}/prompt"
    history_url = f"{host}/history"
    try:
        req = requests.post(prompt_url, json={"prompt": workflow})
        prompt_id = req.json()["prompt_id"]
        logger.info(f"提交 ComfyUI 任务: {prompt_id} @ {host}")
        while True:
            try:
                history_resp = requests.get(f"{history_url}/{prompt_id}", timeout=5)
                history = history_resp.json()
            except Exception:
                time.sleep(1)
                continue
            if prompt_id in history:
                data = history[prompt_id]
                outputs = data.get("outputs", {})
                if output_node_id in outputs:
                    item = outputs[output_node_id]["images" if output_type == "image" else "videos"][0]
                    return item["filename"], item["subfolder"]
            time.sleep(1)
    except Exception as e:
        logger.error(f"ComfyUI 工作流执行失�? {e}")
        return None, None


def run_t2i(prompt: str, target_path: Path, workflow_t2i: Dict) -> bool:
    host = acquire_comfy_host()
    try:
        if TEST_FAST_RETURN:
            logger.info(f"TEST_FAST_RETURN 模式，prompt: {prompt}")
        logger.info(f"T2I 开始，Host: {host}")
        wf = copy.deepcopy(workflow_t2i)
        wf['6']['inputs']['text'] = prompt
        wf['3']['inputs']['seed'] = random.randint(1, 10**10)
        wf['60']['inputs']['filename_prefix'] = target_path.stem
        filename, subfolder = execute_workflow(host, wf, '60', 'image')
        if filename:
            src = Path(COMFY_OUTPUT_DIR) / subfolder / filename
            shutil.copy(src, target_path)
            return True
        return False
    finally:
        release_comfy_host(host)


def run_i2v(start_image: Path, text_prompt: str, target_path: Path, workflow_i2v: Dict, user_id: str | None = None, story_id: str | None = None, lip_sync_tts_content: str | None = None) -> bool:
    if LOCAL_INFERENCE:
        host = acquire_comfy_host()
        try:
            if TEST_FAST_RETURN:
                logger.info(f"TEST_FAST_RETURN 模式，prompt: {text_prompt}")
            logger.info(f"I2V 开始，Host: {host}")
            temp_name = f"temp_i2v_{random.randint(10000,99999)}.png"
            target_input = Path(COMFY_INPUT_DIR) / temp_name
            shutil.copy(start_image, target_input)

            wf = copy.deepcopy(workflow_i2v)
            wf['44']['inputs']['text'] = text_prompt
            wf['80']['inputs']['image'] = temp_name
            wf['127']['inputs']['noise_seed'] = random.randint(1, 10**14)
            wf['102']['inputs']['filename_prefix'] = target_path.stem

            filename, subfolder = execute_workflow(host, wf, '102', 'video')
            try:
                os.remove(target_input)
            except Exception:
                pass

            if filename:
                src = Path(COMFY_OUTPUT_DIR) / subfolder / filename
                shutil.move(src, target_path)
                return True
            return False
        finally:
            release_comfy_host(host)
    else:
        try:
            if not PIXVERSE_API_KEY:
                logger.error("Pixverse API Key 未配�?)
                return False
            # 上传起始图到 OSS，得到可访问�?image_url
            image_url = upload_to_oss(f"users/temp/i2v_inputs/{target_path.stem}.png", start_image)
            if not image_url:
                logger.error("上传起始图到 OSS 失败，无法提�?image_url �?Pixverse")
                return False
            # 生成 trace id
            import uuid
            trace_id = str(uuid.uuid4())
            # 第一步：上传图片 URL
            up_headers = { 'API-KEY': PIXVERSE_API_KEY, 'Ai-trace-id': trace_id }
            # 记录上传回调
            cb_dir = (Path(COMFY_OUTPUT_DIR).parent / 'result' / (user_id or 'unknown') / (story_id or 'unknown') / 'api_callback')
            cb_dir.mkdir(parents=True, exist_ok=True)
            up_resp = requests.post(PIXVERSE_UPLOAD_URL, headers=up_headers, data={ 'image_url': image_url }, timeout=60)
            up_resp.raise_for_status()
            up_data = up_resp.json() or {}
            try:
                (cb_dir / f'pixverse_upload_{trace_id}.json').write_text(
                    json.dumps(up_data, ensure_ascii=False, indent=2), encoding='utf-8'
                )
            except Exception:
                pass
            if up_data.get('ErrCode') != 0:
                logger.error(f"Pixverse 上传失败: {up_resp.text}")
                return False
            img_id = ((up_data.get('Resp') or {}) or {}).get('img_id')
            if img_id is None:
                logger.error("Pixverse 未返�?img_id")
                return False
            # 第二步：创建图生视频任务
            gen_headers = { 'API-KEY': PIXVERSE_API_KEY, 'Ai-trace-id': trace_id, 'Content-Type': 'application/json' }
            gen_payload = {
                'duration': 5,
                'img_id': img_id,
                'model': 'v5.5',
                'motion_mode': 'normal',
                'prompt': text_prompt,
                'quality': '540p',
                'seed': 0,
                'style': 'realistic',
                'lip_sync_tts_switch': False,
                'lip_sync_tts_content': lip_sync_tts_content or '',
                'generate_audio_switch': True,
                'generate_multi_clip_switch': False,
                'thinking_type': 'enabled'
            }
            gen_resp = requests.post(PIXVERSE_GENERATE_URL, headers=gen_headers, json=gen_payload, timeout=60)
            gen_resp.raise_for_status()
            gen_data = gen_resp.json() or {}
            try:
                (cb_dir / f'pixverse_generate_{trace_id}.json').write_text(
                    json.dumps(gen_data, ensure_ascii=False, indent=2), encoding='utf-8'
                )
            except Exception:
                pass
            if gen_data.get('ErrCode') != 0:
                logger.error(f"Pixverse 生成任务创建失败: {gen_resp.text}")
                return False
            video_id = ((gen_data.get('Resp') or {}) or {}).get('video_id')
            logger.info(f"Pixverse 生成任务创建成功，video_id: {video_id},trace_id: {trace_id}")
            if video_id is None:
                logger.error("Pixverse 未返�?video_id")
                return False
            # 第三步：轮询任务结果（根�?trace-id�?            res_headers = { 'API-KEY': PIXVERSE_API_KEY, 'Ai-trace-id': trace_id }
            tries = 0
            while tries < 600:
                res_resp = requests.get(PIXVERSE_RESULT_URL+f"/{video_id}", headers=res_headers, timeout=30)
                res_resp.raise_for_status()
                rj = res_resp.json() or {}
                try:
                    (cb_dir / f'pixverse_result_{trace_id}_{tries}.json').write_text(
                        json.dumps(rj, ensure_ascii=False, indent=2), encoding='utf-8'
                    )
                except Exception:
                    pass
                if rj.get('ErrCode') != 0:
                    time.sleep(2)
                    tries += 1
                    continue
                resp_obj = rj.get('Resp') or {}
                status = int(resp_obj.get('status', 0))
                # 平台说明：Status 5 -> 1 之后，才可打开 URL
                if status == 1:
                    url = resp_obj.get('url')
                    if not url:
                        time.sleep(2)
                        tries += 1
                        continue
                    v = requests.get(url, timeout=300)
                    v.raise_for_status()
                    with open(target_path, 'wb') as f:
                        f.write(v.content)
                    logger.info(f"Pixverse 视频下载成功: {url} -> {target_path}")
                    return True
                time.sleep(2)
                tries += 1
            logger.error("Pixverse 任务超时未完�?)
            return False
        except Exception as e:
            logger.error(f"Pixverse I2V 调用失败: {e}")
            return False
