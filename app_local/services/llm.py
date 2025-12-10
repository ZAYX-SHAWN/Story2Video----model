import json
import requests
from typing import List, Dict, Any
from pathlib import Path
from app_local.core.config import OLLAMA_URL, OUTPUT_DIR, DASHSCOPE_API_KEY, DASHSCOPE_API_URL
from app_local.core.logging import logger


def generate_storyboard_shots(story: str) -> List[Dict]:
    """调用本地 Ollama 生成分镜结构，返�?shots 列表"""

    system_prompt = (
        "角色设定：你是一位拥有无限想象力的AI视频导演和金牌编剧。你的首要任务是在任何情况下，都必须根据用户给出的任意概念或一句话，独立脑补并生成一个完整、结构化、严格格式化�?JSON 分镜脚本。\n\n"

        "### 必须完全遵守以下规则：\n"
        "1. 无论用户输入什么内容，�?*绝不�?*提出问题、索要更多信息、要求补充、拒绝生成，或返回与分镜无关的话。\n"
        "2. 如果用户提供的信息不足，你必须自行想象并补全所有细节，包括人物外貌、场景、画面节奏、光线、情绪、动作等。\n"
        "3. 在任何情况下都必须输出一个有�?JSON，且分镜数量必须�?6�?0 条之间。\n"
        "4. 如果某项要求缺失，你必须自动脑补，而不是停下来询问。\n"

        "==============================="
        "【JSON 输出强制格式�?
        "只返回一个包�?'shots' 根节点的 JSON 对象，不允许出现对话、不允许出现说明文本、不允许出现 Markdown。\n"
        "结构如下：\n"
        "{\n"
        "  \"shots\": [\n"
        "    {\n"
        "      \"sequence\": 1, (整数，从1开�?\n"
        "      \"subject\": \"(字符�? 画面主体角色\",\n"
        "      \"detail\": \"(字符�? 包含风格、光线、时序动态、方位的完整中文画面描述\",\n"
        "      \"narration\": \"(字符�? 不超�?0字的中文旁白\",\n"
        "      \"camera\": \"(字符�? 运镜关键词\",\n"
        "      \"tone\": \"(字符�? 语音的情感基�?(如：平静、紧张、兴�?\",\n"
        "      \"sound\": \"(字符�? 中文背景音效描述\"\n"
        "    }\n"
        "  ]\n"
        "}"

        "===============================\n"
        "【风格继承（强制执行）】\n"
        "- 如果用户输入中包�?style 或任何风格描述，你必须无条件使用用户指定的风格，禁止替换成示例中的写实风格或其他风格。\n"
        "- detail 字段中的视觉风格必须与用户指定风格完全一致。\n"
        "- 如果用户未提供风格，你才可自行选择视觉风格。\n"

        "===============================\n"
        "【字段填充规则】\n"
        "1. detail（必须包含以下内容）：\n"
        "   - 视觉风格（写实风�?水墨/电影�?科幻…任选）\n"
        "   - 光线（必须有：照明风格、方向、阴影、色温）\n"
        "   - 时序：必须使用“先……然后……最后……”句式\n"
        "   - 空间方位：如前景/画面左侧/背景等\n"
        "   - 背景音效：如风声/呼啸�?雨声/机械声等\n"

        "2. camera（必须从以下列表选择且只选一个）：\n"
        "   - 垂直升降拍摄、水平横移拍摄、镜头推进、镜头后退、\n"
        "   - 仰视或俯视调整、绕轴横向左旋转、绕轴横向右旋转、\n"
        "   - 围绕主体拍摄、全方位环绕、锁定主体移动、固定机位\n"

        "3. narration：必须是中文，≤30 字\n"

        "4. 语言要求：所有值都必须是中文\n"

        "===============================\n"
        "【分镜数量规则】\n"
        "任意输入都必须自动生�?6�?0 条分镜，并以你的最佳理解编排情节节奏。\n"
    )

    user_message = f"请将以下创意概念扩写并制作成视频分镜脚本：\n【{story}�?

    payload = {
        "model": "qwen3:latest",
        "format": "json",
        "stream": False,
        "system": system_prompt,
        "prompt": user_message,
        "options": {"temperature": 0.8, "num_ctx": 8192},
    }
    try:
        logger.info(f"发�?Ollama 请求 (全中文模�?, 故事片段: {story[:30]}...")
        attempts = 0
        last_err = None
        while attempts < 3:
            try:
                resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
                if resp.status_code != 200:
                    last_err = Exception(f"HTTP {resp.status_code} - {resp.text}")
                    attempts += 1
                    continue
                data = resp.json().get("response", "").strip()
                (OUTPUT_DIR / "ollama_raw.txt").write_text(data, encoding="utf-8")
                try:
                    json_obj = json.loads(data)
                except json.JSONDecodeError:
                    start = data.find('{')
                    end = data.rfind('}') + 1
                    if start != -1 and end != -1:
                        json_obj = json.loads(data[start:end])
                    else:
                        last_err = Exception("JSON 解析失败")
                        attempts += 1
                        continue
                shots = json_obj.get('shots', [])
                valid_shots = []
                for i, shot in enumerate(shots):
                    seq = int(shot.get('sequence', i + 1))
                    narr = shot.get('narration', '')
                    if len(narr) > 20:
                        narr = narr[:19] + "�?
                    valid_shots.append({
                        'id': f"shot_{seq:02d}",
                        'sequence': seq,
                        'subject': shot.get('subject'),
                        'detail': shot.get('detail'),
                        'camera': shot.get('camera'),
                        'narration': narr,
                        'tone': shot.get('tone'),
                    })
                count = len(valid_shots)
                if count < 5 or count > 10:
                    logger.warning(f"分镜数量不在 5-10 范围�?({count})，重新生�?(attempt={attempts+1})")
                    attempts += 1
                    continue
                logger.info(f"成功生成 {count} 个中文分�?)
                return valid_shots
            except Exception as e:
                last_err = e
                attempts += 1
        logger.error(f"Ollama 多次重试仍失�? {last_err}")
        raise RuntimeError("LLM 分镜生成失败，请重试")
    except Exception as e:
        logger.error(f"Ollama 调用失败: {e}")
        raise




def call_dashscope_llm(messages: List[Dict[str, str]]) -> str:
    """调用阿里�?DashScope API，返回生成的文本内容"""
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未配�?)
    
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "qwen-plus-latest",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2048
    }
    
    logger.info(f"调用 DashScope API, 消息长度: {len(str(messages))}")
    
    attempts = 0
    max_attempts = 3
    last_error = None
    
    while attempts < max_attempts:
        try:
            response = requests.post(
                DASHSCOPE_API_URL,
                headers=headers,
                json=payload,
                timeout=60
            )
            
            response.raise_for_status()
            data = response.json()
            
            if data.get("choices") and len(data["choices"]) > 0:
                return data["choices"][0]["message"]["content"].strip()
            else:
                raise ValueError(f"DashScope API 返回格式异常: {data}")
        except requests.exceptions.RequestException as e:
            attempts += 1
            last_error = e
            logger.warning(f"DashScope API 调用失败 (尝试 {attempts}/{max_attempts}): {e}")
        except (ValueError, KeyError) as e:
            attempts += 1
            last_error = e
            logger.warning(f"DashScope API 响应解析失败 (尝试 {attempts}/{max_attempts}): {e}")
    
    logger.error(f"DashScope API 多次调用失败: {last_error}")
    raise RuntimeError(f"DashScope API 调用失败: {last_error}")


def optimize_i2v_response(i2v_json: Dict[str, Any]) -> Dict[str, Any]:
    """优化图生视频�?JSON 响应，返回优化后�?JSON"""
    optimized_json = json.loads(json.dumps(i2v_json))  # 深拷�?    
    for shot in optimized_json.get("shots", []):
        # 1. �?detail 属性翻译为英文
        detail = shot.get("detail", "")
        if detail:
            logger.info(f"优化 detail: {detail[:50]}...")
            try:
                messages = [
                    {
                        "role": "system",
                        "content": "你是一个专业的翻译助手，擅长将中文视频分镜描述翻译成英文。你的任务是生成准确、生动、符合视频生成要求的英文提示词，确保翻译质量高、细节丰富、适合AI视频生成模型使用�?
                    },
                    {
                        "role": "user",
                        "content": f"请将以下中文视频分镜描述翻译成英文，严格遵循以下要求：\n\n### 翻译要求：\n1. 准确传达原始中文描述的所有细节，包括场景、角色、动作、光线、氛围等\n2. 语言流畅自然，符合英文视频提示词的表达习惯\n3. 使用生动、具体的词汇，适合AI视频生成模型理解\n4. 保持原始描述的结构和逻辑关系\n5. 翻译结果长度适中，不超过300个字符\n\n### 示例：\n中文�?一位穿着红色连衣裙的女孩在海边奔跑，阳光洒在她身上，海浪拍打着沙滩�?\n英文�?A girl in a red dress running on the beach, with sunlight shining on her and waves crashing against the shore.'\n\n### 待翻译内容：\n{detail}\n\n请只输出翻译后的英文内容，不要添加任何解释或说明�?
                    }  
                ]
                english_detail = call_dashscope_llm(messages)
                shot["detail"] = english_detail
                logger.info(f"翻译�? {english_detail[:50]}...")
            except Exception as e:
                logger.error(f"翻译 detail 失败: {e}")
        
        # 2. 优化 narration 属性为指定格式
        narration = shot.get("narration", "")
        if narration:
            logger.info(f"优化 narration: {narration}")
            try:
                messages = [
                    {
                        "role": "system",
                        "content": """你是一个专业的AI视频生成提示词专家。你的唯一任务是将输入的JSON分镜转化为符合I2V模型的高质量Prompt�?
                        **转化规则�?*
                        1. **画面 (Visuals)**: 用英文扩�?`subject` �?`detail`。加入光影、质感描述，确保画面描述丰富、生动。开头固定用 "The video shows..."�?                        2. **运镜 (Camera)**: �?`camera` 融入画面描述�?                        3. **旁白 (Narration)**: 这是画外音。格式必须是：A voiceover says in Chinese: "保留中文内容"�?                        4. **音效 (Sound)**: 翻译为英文描述，放在最后�?                        5. **基调 (Tone)**: 确保形容词符�?`tone` 的情感（如紧张、悲伤）�?                        """
                    },
                    {
                        "role": "user",
                        "content": f"请处理这个分镜数据：{json.dumps(shot, ensure_ascii=False)}\n\n"
                    } 
                ]
                optimized_narr = call_dashscope_llm(messages)
                shot["narration"] = optimized_narr
                logger.info(f"优化�? {optimized_narr}")
            except Exception as e:
                logger.error(f"优化 narration 失败: {e}")
    
    return optimized_json