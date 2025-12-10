"""
主调度脚本 - 根据 LOCAL_INFERENCE 环境变量选择使用本地推理或API推理

使用方法:
    LOCAL_INFERENCE=true python main_dispatcher.py   # 使用本地推理
    LOCAL_INFERENCE=false python main_dispatcher.py  # 使用API推理

注意：首次使用前需要完成文件夹重命名（见下方说明）
"""

import os
import sys
from pathlib import Path

# 获取当前脚本所在目录的父目录（model-serving）
BASE_DIR = Path(__file__).parent

# 读取 LOCAL_INFERENCE 环境变量
LOCAL_INFERENCE = os.getenv("LOCAL_INFERENCE", "false").lower() in {"1", "true", "yes"}

# 根据 LOCAL_INFERENCE 选择模块路径
if LOCAL_INFERENCE:
    app_dir = BASE_DIR / "app_local"
    mode_name = "本地推理模式"
    t2i_desc = "ComfyUI 本地模型"
    i2v_desc = "Pixverse API"
else:
    app_dir = BASE_DIR / "app_api"
    mode_name = "API推理模式"
    t2i_desc = "DashScope qwen-image-plus API"
    i2v_desc = "DashScope wan2.5-preview API"

# 检查目录是否存在
if not app_dir.exists():
    print("\n" + "=" * 70)
    print("❌ 错误: 找不到目录", app_dir)
    print("=" * 70)
    print("\n请先完成以下步骤：")
    print("\n1. 重命名文件夹:")
    print(f"   cd {BASE_DIR}")
    print("   Move-Item -Path 'app' -Destination 'app_api'")
    print("   Move-Item -Path 'app_local' -Destination '../app_local'")
    print("   （或手动重命名：app → app_api, app(1) → app_local）")
    print("\n2. 然后运行:")
    print("   LOCAL_INFERENCE=false python app/main_dispatcher.py  # API模式")
    print("   LOCAL_INFERENCE=true python app/main_dispatcher.py   # 本地模式")
    print("=" * 70)
    sys.exit(1)

# 将选择的 app 目录添加到 Python 路径
sys.path.insert(0, str(app_dir.parent))

print("\n" + "=" * 70)
print(f"{'🟢' if LOCAL_INFERENCE else '🔵'} {mode_name} (LOCAL_INFERENCE={LOCAL_INFERENCE})")
print(f"   📁 模块目录: {app_dir}")
print(f"   🖼️  文生图: {t2i_desc}")
print(f"   🎬 图生视频: {i2v_desc}")
print("=" * 70 + "\n")

# 动态导入对应模块
try:
    # 修改模块名以匹配重命名后的目录
    module_name = app_dir.name  # 'app_api' 或 'app_local'
    
    # 导入配置
    config = __import__(f"{module_name}.core.config", fromlist=['SERVICE_PORT', 'OUTPUT_DIR'])
    
    # 导入日志
    logging = __import__(f"{module_name}.core.logging", fromlist=['logger'])
    logger = logging.logger
    
    # 导入 FastAPI app
    main_module = __import__(f"{module_name}.main", fromlist=['app'])
    app = main_module.app
    
    logger.info(f"✅ 成功加载模块: {module_name}")
    logger.info(f"📡 服务端口: {config.SERVICE_PORT}")
    logger.info(f"📂 输出目录: {config.OUTPUT_DIR}")
    
except ImportError as e:
    print(f"\n❌ 导入错误: {e}")
    print(f"\n请检查 {app_dir} 目录结构是否完整")
    sys.exit(1)

# 启动服务
if __name__ == "__main__":
    import uvicorn
    
    port = config.SERVICE_PORT
    
    logger.info("=" * 70)
    logger.info(f"🚀 启动服务器 - {mode_name}")
    logger.info(f"🌐 监听地址: http://0.0.0.0:{port}")
    logger.info("=" * 70)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
