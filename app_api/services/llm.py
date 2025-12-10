# -*- coding: utf-8 -*-
import json
import time
import requests
from typing import List, Dict, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from app_api.core.config import (
    OUTPUT_DIR, DASHSCOPE_API_KEY, DASHSCOPE_IMAGE_MODEL, DEFAULT_IMAGE_SIZE,
    API_RETRY_ATTEMPTS, API_RETRY_BASE_DELAY
)
from app_api.core.logging import logger

# 提前导入dashscope相关模块，避免循环内导入
try:
    import dashscope
    from dashscope import Generation, MultiModalConversation
except ImportError as e:
    logger.error(f"未安装dashscope SDK: {e}")
    raise


def generate_storyboard_shots(story: str) -> List[Dict]:
    """调用 DashScope qwen-plus API 生成分镜结构，返回 shots 列表"""
    system_prompt = (
        "角色设定：你是一位拥有无限想象力的AI视频导演和金牌编剧。你的首要任务是在任何情况下，都必须根据用户给出的任意概念或一句话，独立脑补并生成一个完整、结构化、严格格式化的 JSON 分镜脚本。\n\n"

        "### 必须完全遵守以下规则：\n"
        "1. 无论用户输入什么内容，**绝不**提出问题、索要更多信息、要求补充、拒绝生成，或返回与分镜无关的话。\n"
        "2. 如果用户提供的信息不足，你必须自行想象并补全所有细节，包括人物外貌、场景、画面节奏、光线、情绪、动作等。\n"
        "3. 在任何情况下都必须输出一个有效的 JSON，且分镜数量必须在 6~10 条之间。\n"
        "4. 如果某项要求缺失，你必须自动脑补，而不是停下来询问。\n"

        "===============================\n"
        "【JSON 输出强制格式】\n"
        "只返回一个包含'shots' 根节点的 JSON 对象，不允许出现对话、不允许出现说明文本、不允许出现 Markdown。\n"
        "结构如下：\n"
        "{\n"
        "  \"shots\": [\n"
        "    {\n"
        "      \"sequence\": 1, (整数，从1开始)\n"
        "      \"subject\": \"(字符串) 画面主体角色\",\n"
        "      \"detail\": \"(字符串) 包含风格、光线、时序动态、方位的完整中文画面描述\",\n"
        "      \"narration\": \"(字符串) 不超过30字的中文旁白\",\n"
        "      \"camera\": \"(字符串) 运镜关键词\",\n"
        "      \"tone\": \"(字符串) 语音的情感基调(如：平静、紧张、兴奋)\",\n"
        "      \"sound\": \"(字符串) 中文背景音效描述\"\n"
        "    }\n"
        "  ]\n"
        "}\n"

        "===============================\n"
        "【风格继承（强制执行）】\n"
        "- 如果用户输入中包含 style 或任何风格描述，你必须无条件使用用户指定的风格，禁止替换成示例中的写实风格或其他风格。\n"
        "- detail 字段中的视觉风格必须与用户指定风格完全一致。\n"
        "- 如果用户未提供风格，你才可自行选择视觉风格。\n"

        "===============================\n"
        "【字段填充规则】\n"
        "1. detail（必须包含以下内容）：\n"
        "   - 视觉风格（写实风格/水墨/电影感/科幻…任选）\n"
        "   - 光线（必须有：照明风格、方向、阴影、色温）\n"
        "   - 时序：必须使用“先……然后……最后……”句式\n"
        "   - 空间方位：如前景/画面左侧/背景等\n"
        "   - 背景音效：如风声/呼啸声/雨声/机械声等\n"

        "2. camera（必须从以下列表选择且只选一个）：\n"
        "   - 垂直升降拍摄、水平横移拍摄、镜头推进、镜头后退、\n"
        "   - 仰视或俯视调整、绕轴横向左旋转、绕轴横向右旋转、\n"
        "   - 围绕主体拍摄、全方位环绕、锁定主体移动、固定机位\n"

        "3. narration：必须是中文，≤30 字\n"

        "4. 语言要求：所有值都必须是中文\n"

        "===============================\n"
        "【分镜数量规则】\n"
        "任意输入都必须自动生成6~10 条分镜，并以你的最佳理解编排情节节奏。\n"
    )

    # 修复字符串闭合和中文字符问题
    user_message = f"请将以下创意概念扩写并制作成视频分镜脚本：\n【{story}】"

    try:
        logger.info(f"发起 DashScope qwen-plus 请求 (全中文模式), 故事片段: {story[:30]}...")
        attempts = 0
        last_err = None
        
        while attempts < API_RETRY_ATTEMPTS:
            try:
                # 构造请求消息
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
                
                # 调用LLM接口
                data = call_dashscope_llm(messages)
                
                # 保存原始响应（确保目录存在）
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                (OUTPUT_DIR / "dashscope_raw.txt").write_text(data, encoding="utf-8")
                
                # 解析JSON（容错处理）
                json_obj = None
                try:
                    json_obj = json.loads(data)
                except json.JSONDecodeError:
                    # 尝试提取JSON片段
                    start = data.find('{')
                    end = data.rfind('}') + 1
                    if start != -1 and end != 0:
                        try:
                            json_obj = json.loads(data[start:end])
                        except json.JSONDecodeError as e:
                            last_err = Exception(f"JSON提取后仍解析失败: {e}")
                    else:
                        last_err = Exception("未找到有效JSON片段")
                
                if not json_obj:
                    raise last_err or Exception("JSON解析失败")
                
                # 验证并处理分镜数据
                shots = json_obj.get('shots', [])
                valid_shots = []
                for i, shot in enumerate(shots):
                    seq = int(shot.get('sequence', i + 1))
                    narr = shot.get('narration', '').strip()
                    
                    # 旁白长度限制（≤30字，超长截断并加省略号）
                    if len(narr) > 30:
                        narr = narr[:29] + "…"
                    
                    valid_shots.append({
                        'id': f"shot_{seq:02d}",
                        'sequence': seq,
                        'subject': shot.get('subject', ''),
                        'detail': shot.get('detail', ''),
                        'camera': shot.get('camera', ''),
                        'narration': narr,
                        'tone': shot.get('tone', ''),
                    })
                
                # 修复分镜数量判断逻辑（原4-8错误，应为6-10）
                count = len(valid_shots)
                if 6 <= count <= 10:
                    logger.info(f"成功生成 {count} 个中文分镜")
                    return valid_shots
                else:
                    logger.warning(f"分镜数量不在 6-10 范围内 ({count})，重新生成 (attempt={attempts+1})")
                    attempts += 1
                    time.sleep(API_RETRY_BASE_DELAY ** attempts)
                    continue

            except Exception as e:
                last_err = e
                attempts += 1
                logger.warning(f"DashScope qwen-plus 调用失败 (尝试 {attempts}/{API_RETRY_ATTEMPTS}): {e}")
                if attempts < API_RETRY_ATTEMPTS:
                    wait_time = API_RETRY_BASE_DELAY ** attempts
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
        
        # 多次重试失败
        logger.error(f"DashScope qwen-plus 多次重试仍失败: {last_err}")
        raise RuntimeError("LLM 分镜生成失败，请重试")
    
    except Exception as e:
        logger.error(f"DashScope qwen-plus 调用失败: {e}")
        raise


def call_dashscope_image_api(prompt: str, target_path: Path, size: str = None, n: int = 1) -> bool:
    """调用 DashScope qwen-image-plus API 生成图片
    
    Args:
        prompt: 文本描述
        target_path: 目标保存路径
        size: 图片尺寸 (默认使用 DEFAULT_IMAGE_SIZE)
        n: 生成图片数量 (默认: 1)
    
    Returns:
        bool: 成功返回 True，失败返回 False
    """
    if not DASHSCOPE_API_KEY:
        logger.error("DASHSCOPE_API_KEY 未配置")
        return False
    
    size = size or DEFAULT_IMAGE_SIZE
    dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
    
    # 构造请求消息
    messages = [
        {
            "role": "user",
            "content": [{"text": prompt}]
        }
    ]
    
    attempts = 0
    last_error = None
    
    while attempts < API_RETRY_ATTEMPTS:
        try:
            logger.info(f"调用 DashScope Image API (尝试 {attempts + 1}/{API_RETRY_ATTEMPTS}), prompt: {prompt[:50]}...")
            
            # 调用多模态API
            response = MultiModalConversation.call(
                api_key=DASHSCOPE_API_KEY,
                model=DASHSCOPE_IMAGE_MODEL,
                messages=messages,
                result_format='message',
                stream=False,
                watermark=False,
                prompt_extend=False,
                negative_prompt='',
                size=size
            )
            
            # 检查响应状态
            if response.status_code == 200:
                output = response.output
                if output and hasattr(output, 'choices') and output.choices:
                    choice = output.choices[0]
                    if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                        content = choice.message.content
                        for item in content:
                            if isinstance(item, dict) and 'image' in item:
                                image_url = item['image']
                                logger.info(f"图像生成成功，正在下载: {image_url}")
                                
                                # 下载并保存图片
                                img_response = requests.get(image_url, timeout=60)
                                img_response.raise_for_status()
                                
                                target_path.parent.mkdir(parents=True, exist_ok=True)
                                with open(target_path, 'wb') as f:
                                    f.write(img_response.content)
                                
                                logger.info(f"图片下载成功: {target_path}")
                                return True
                
                # 未找到图片URL
                response_dict = response.to_dict() if hasattr(response, 'to_dict') else str(response)
                logger.error(f"API返回成功但未找到图片URL，响应内容: {json.dumps(response_dict, ensure_ascii=False)}")
                return False
            else:
                # 处理错误响应
                err_msg = (
                    f"HTTP返回码：{response.status_code}, "
                    f"错误码：{getattr(response, 'code', '未知')}, "
                    f"错误信息：{getattr(response, 'message', '未知')}"
                )
                logger.error(err_msg)
                logger.error("请参考文档：https://www.alibabacloud.com/help/zh/model-studio/error-code")
                
                last_error = err_msg
                attempts += 1
                if attempts < API_RETRY_ATTEMPTS:
                    wait_time = API_RETRY_BASE_DELAY ** attempts
                    logger.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
        
        except Exception as e:
            attempts += 1
            last_error = str(e)
            logger.error(f"图像生成过程中出现异常 (尝试 {attempts}/{API_RETRY_ATTEMPTS}): {e}")
            if attempts < API_RETRY_ATTEMPTS:
                wait_time = API_RETRY_BASE_DELAY ** attempts
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
    
    logger.error(f"DashScope Image API 多次重试失败: {last_error}")
    return False


def run_t2i_api(prompt: str, target_path: Path) -> bool:
    """
    文生图API包装函数，用于替代原 run_t2i 函数
    
    Args:
        prompt: 文本描述
        target_path: 目标保存路径
    
    Returns:
        bool: 成功返回 True，失败返回 False
    """
    logger.info(f"T2I API 开始，目标路径: {target_path}")
    
    # 调用 DashScope Image API
    success = call_dashscope_image_api(prompt, target_path, size=DEFAULT_IMAGE_SIZE, n=2)
    
    if success:
        logger.info(f"T2I API 完成: {target_path}")
    else:
        logger.error(f"T2I API 失败: {target_path}")
    
    return success


def call_dashscope_llm(messages: List[Dict[str, str]]) -> str:
    """调用阿里云DashScope API，返回生成的文本内容"""
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未配置")
    
    logger.info(f"调用 DashScope API (Generation.call), 消息长度: {len(str(messages))}")
    
    attempts = 0
    last_error = None
    
    while attempts < API_RETRY_ATTEMPTS:
        try:
            # 调用生成式API
            response = Generation.call(
                api_key=DASHSCOPE_API_KEY,
                model="qwen-flash",
                messages=messages,
                result_format="message",
                enable_thinking=False
            )
            
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                return content.strip()
            else:
                raise ValueError(
                    f"DashScope API 返回错误: status_code={response.status_code}, "
                    f"code={getattr(response, 'code', '未知')}, "
                    f"message={getattr(response, 'message', '未知')}"
                )
                
        except Exception as e:
            attempts += 1
            last_error = e
            logger.warning(f"DashScope API 调用失败 (尝试 {attempts}/{API_RETRY_ATTEMPTS}): {e}")
            if attempts < API_RETRY_ATTEMPTS:
                wait_time = API_RETRY_BASE_DELAY ** attempts
                logger.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
    
    logger.error(f"DashScope API 多次调用失败: {last_error}")
    raise RuntimeError(f"DashScope API 调用失败: {last_error}")


def optimize_i2v_response(i2v_json: Dict[str, Any]) -> Dict[str, Any]:
    """优化图生视频的 JSON 响应，为 wan2.5-preview 生成优化的画面prompt（并发处理）"""
    # 深拷贝避免修改原数据
    optimized_json = json.loads(json.dumps(i2v_json))
    shots_list = optimized_json.get("shots", [])
    
    if not shots_list:
        logger.warning("分镜列表为空，无需优化")
        return optimized_json
    
    logger.info(f"开始并发优化 {len(shots_list)} 个分镜的 prompt (并发数: 10)")
    
    def optimize_single_shot(shot):
        """优化单个分镜的prompt"""
        detail = shot.get("detail", "").strip()
        tone = shot.get("tone", "").strip()
        camera = shot.get("camera", "").strip()
        narration = shot.get("narration", "").strip()
        shot_id = shot.get('id', 'unknown')
        
        if not detail:
            logger.warning(f"Shot {shot_id} 缺少 detail 字段，跳过优化")
            return shot
        
        logger.info(f"优化 shot {shot_id}: detail={detail[:50]}..., tone={tone}, camera={camera}")
        
        try:
            # 构造优化prompt
            system_prompt = """你是一个专业的AI视频生成提示词专家。你的任务是将分镜信息优化为适合wan2.5-preview模型的画面描述prompt。

**注意：音频已经通过 audio_url 单独提供给模型，所以提示词中不需要描述旁白、配音、音效等音频内容。**

**必须强调的要求：**
1. **字幕显示**：视频必须在正下方配上中文字幕
2. **字幕内容**：字幕内容必须完全一致地显示 narration 字段的文本，不要添加、删除或修改任何文字
3. **画面描述**：结合 detail、tone、camera 等信息，用中文详细描述画面的视觉内容

**画面优化要求：**
1. 包括场景、动作、光线、氛围、运镜、色彩、构图等视觉元素
2. 根据 tone（情感基调）调整画面的氛围描述
3. 根据 camera（运镜）添加镜头运动的描述
4. 画面描述应该生动、具体、流畅，适合视频生成
5. 不要提及除字幕外的音频元素（旁白、配音、音效等）

**输出格式：**
在视频正下方配上中文字幕，字幕内容为"{narration的完整内容}"。[画面描述内容]

**示例输入：**
detail: 电影写实风格，阴天灰光，先奥特曼在空中翻滚，然后他张开光之翼，最后光翼反射出母舰的强光
tone: 紧张
camera: 镜头推进
narration: 地球要完了

**示例输出：**
在视频正下方配上中文字幕，字幕内容为"地球要完了"。延续电影写实风格，阴天灰色冷光笼罩整个画面，营造紧张压抑的氛围。镜头缓缓推进，聚焦空中的奥特曼：他在云层中快速翻滚，身形矫健有力；随后双臂展开，背后的光之翼瞬间绽放出耀眼光芒，光翼边缘闪烁着金色能量粒子；最后光翼的强光反射在远处的巨型母舰金属表面，形成震撼的光影对比。整个画面节奏紧凑，色调从灰暗逐渐转向明亮，光影变化强烈，渲染出紧张激烈的战斗氛围。

请只输出最终的描述文本，不要添加任何解释。"""

            user_prompt = f"""请将以下分镜信息优化为画面描述prompt：

detail: {detail}
tone: {tone}
camera: {camera}
narration: {narration}

请记住：
1. 必须强调：在视频正下方配上中文字幕，字幕内容为"{narration}"
2. 字幕内容必须是 narration 的完整文本，不要修改、添加或删除任何文字
3. 只描述画面的视觉内容，不要提及音频、旁白、配音等（字幕除外）
4. 结合 tone 调整画面氛围
5. 结合 camera 描述镜头运动
6. 使用生动、具体的中文描述"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            
            # 调用LLM优化prompt
            optimized_prompt = call_dashscope_llm(messages)
            shot["detail"] = optimized_prompt
            logger.info(f"Shot {shot_id} 优化完成: {optimized_prompt[:100]}...")
            
        except Exception as e:
            logger.error(f"优化 shot {shot_id} 失败: {e}")
            logger.warning(f"Shot {shot_id} 使用原始 detail 作为降级方案")
        
        return shot
    
    # 并发处理分镜优化
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(optimize_single_shot, shot): shot for shot in shots_list}
        
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"并发优化过程中出现异常: {e}")
    
    logger.info(f"所有分镜prompt 优化完成")
    return optimized_json