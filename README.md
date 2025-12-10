# Story2Video——模型服务

面向“故事成片”的一体化模型服务，提供分镜生成（LLM）、文生图（T2I）、图生视频（I2V）、语音合成（TTS）与片段拼接（FFmpeg）等能力，统一通过 FastAPI 暴露标准化接口。支持两种运行模式：
- API 推理模式：调用外部服务（DashScope、PixVerse、CosyVoice、Aliyun OSS）完成全链路
- 本地推理模式：接入本地 ComfyUI 等组件完成 T2I/I2V，并保留同样的 API 形态

## 1. 项目介绍
- 核心功能与业务价值：
  - 将文本剧本自动拆分为分镜，并生成每个分镜的关键帧图片与对应视频片段，再统一合成最终成片；大幅缩短从脚本到成片的制作时间
  - 提供可替换的推理后端（云端/本地），适应不同环境与成本约束
  - 标准化接口便于前后端联调、自动化测试与集成
- 主要技术栈与版本：
  - Python（建议 3.9+）
  - FastAPI 0.115.2、Uvicorn 0.32.0、Pydantic 2.9.1、Loguru 0.7.2、Requests 2.32.3、ffmpeg-python 0.2.0
  - 外部服务 SDK：DashScope、CosyVoice、Aliyun OSS（oss2）、Pydub
- 目标用户与使用场景：
  - 短视频/广告/教育等内容制作团队，希望自动化从剧本到成片流程
  - 需要统一的推理服务接口以便在客户端或后台系统中集成

## 2. 环境要求
- 运行环境：
  - Python 3.9+
  - FFmpeg（命令行可用）
- 数据库：
  - 无需传统数据库；使用文件系统 JSON 存储为轻量数据层
- 第三方服务依赖（按需选择）：
  - DashScope（文本生成、图像生成、I2V 任务）
  - Aliyun OSS（对象存储与预签名 URL）
  - CosyVoice（TTS 语音）
  - ComfyUI（本地模式的文生图/图生视频）
  - PixVerse（当不走本地 I2V 时的云端图生视频）
  - Ollama（本地 LLM，可用于扩展能力）

## 3. 安装部署
- 克隆与环境：
  - 建议在 Windows 或类 Unix 环境下使用虚拟环境
```
git clone <repo-url>
cd Story2Video----model
python -m venv .venv
.venv\\Scripts\\activate
pip install -r app_api/requirements.txt
```
- 选择运行模式：
  - 通过环境变量 `LOCAL_INFERENCE` 控制模式（false=API 推理；true=本地推理）
  - 端口通过 `SERVICE_PORT` 控制，默认 `12345`
- 启动服务（统一入口）：
```
set LOCAL_INFERENCE=false
python main_dispatcher.py
```
- 直接启动单模式：
```
python app_api/main.py
python app_local/main.py
```
- 配置管理（环境变量）：
  - 通用
    - `SERVICE_PORT`：服务端口（默认 12345）
    - `LOCAL_INFERENCE`：是否使用本地推理（true/false）
  - Aliyun OSS
    - `OSS_ENDPOINT`：OSS 访问端点
    - `OSS_ACCESS_KEY_ID`：访问密钥 ID
    - `OSS_ACCESS_KEY_SECRET`：访问密钥 Secret
    - `OSS_BUCKET`：Bucket 名称
    - `OSS_BASE_URL`：公共访问域（如启用）
    - `OSS_URL_EXPIRES`：预签名 URL 的过期秒数
  - DashScope
    - `DASHSCOPE_API_KEY`：API 密钥
    - `DASHSCOPE_IMAGE_MODEL`：图像模型（默认 qwen-image-plus）
    - `DEFAULT_IMAGE_SIZE`：默认图像尺寸（如 928*1664）
    - `API_RETRY_ATTEMPTS`、`API_RETRY_BASE_DELAY`：重试次数与基准延迟
  - 本地推理
    - `COMFY_HOSTS_LIST`：本地 ComfyUI 主机列表（逗号分隔）
    - `PIXVERSE_*`：PixVerse 相关配置
    - `OLLAMA_URL`、`COSYVOICE_URL`：本地服务地址
- 重要路径说明：
  - `app_api/core/config.py` 中 `PROJECT_ROOT` 默认指向 `D:\\Story2Video-main`，可按部署环境调整
  - `OUTPUT_DIR` 为静态输出目录，服务会自动创建并挂载到 `/static`

## 4. 使用说明
- 接口文档：
  - 运行后访问 `http://localhost:12345/docs`（Swagger UI）或 `http://localhost:12345/redoc`
- 创建分镜（API 推理模式示例）：
```
curl -X POST http://localhost:12345/api/v1/storyboard/create \
  -H "Content-Type: application/json" \
  -d "{\n    \"operation_id\": \"op-001\",\n    \"story_id\": \"story-001\",\n    \"user_id\": \"u-001\",\n    \"display_name\": \"我的故事\",\n    \"script_content\": \"......\",\n    \"style\": \"写实\"\n  }"
```
- 重生成单镜头：
```
curl -X POST http://localhost:12345/api/v1/shot/regenerate \
  -H "Content-Type: application/json" \
  -d "{\n    \"operation_id\": \"op-002\",\n    \"story_id\": \"story-001\",\n+    \"shot_id\": \"shot_01\",\n    \"user_id\": \"u-001\",\n    \"detail\": \"新的细节描述...\"\n  }"
```
- 渲染最终视频：
```
curl -X POST http://localhost:12345/api/v1/video/render \
  -H "Content-Type: application/json" \
  -d "{\n    \"operation_id\": \"op-003\",\n    \"story_id\": \"story-001\",\n    \"user_id\": \"u-001\"\n  }"
```
- 常见问题：
  - FFmpeg 未安装或不可执行：确保命令 `ffmpeg -version` 正常返回；并将其加入系统 PATH
  - DashScope 401/403：检查 `DASHSCOPE_API_KEY` 是否正确、是否有相应模型权限
  - OSS 403/挂载失败：核对 `OSS_*` 配置与 Bucket 策略；如使用预签名 URL，请确认过期时间与时钟同步
  - 本地 ComfyUI 不可达：检查 `COMFY_HOSTS_LIST` 地址/端口，确认进程已启动
  - PixVerse 任务失败：确认 `PIXVERSE_*` 配置与额度；查看服务日志进行重试

## 5. 开发指南
- 代码结构：
```
app_api/
  api/routes.py
  core/{config.py, logging.py}
  models/schemas.py
  services/{llm.py, i2v.py, tts_v2.py, ffmpeg_merge.py, oss.py}
  storage/repository.py
app_local/
  api/routes.py
  core/{config.py, logging.py}
  models/schemas.py
  services/{comfy.py, pixverse.py, ffmpeg_merge.py, oss.py}
  storage/repository.py
main_dispatcher.py
```
- 开发环境配置：
  - 使用虚拟环境并安装 `app_api/requirements.txt` 或 `app_local/requirements.txt`
  - 通过环境变量控制运行模式与外部服务参数
- 测试流程：
  - 现有示例脚本：`app_api/test_qwen_plus.py`、`app_api/test_qwen_image_plus.py`、`app_api/test_cosyvoice.py`
  - 建议后续引入 `pytest` 进行单元/集成测试，并在 CI 中执行
- 提交规范与要求：
  - 推荐采用 Conventional Commits（如 `feat: 添加渲染重试机制`、`fix: 修复 OSS 预签名过期计算`）
  - 在 PR 中附带变更说明、接口影响、测试结果与风险评估