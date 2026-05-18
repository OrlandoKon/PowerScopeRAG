import os
from dotenv import load_dotenv

load_dotenv()  # 从项目根目录的 .env 文件加载环境变量

# DeepSeek V4 API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中添加：DEEPSEEK_API_KEY=sk-xxx")

# 模型选择 — 请在 platform.deepseek.com → API Reference 确认实际模型 ID
# 常见格式示例：deepseek-chat / deepseek-v3 / deepseek-v4 等
MODEL_FLASH = os.environ.get("MODEL_FLASH", "deepseek-chat")   # 画像类智能体用，便宜快速
MODEL_PRO   = os.environ.get("MODEL_PRO",   "deepseek-chat")   # 综合分析智能体用，强推理

# 默认参数（DeepSeek官方推荐）
DEFAULT_TEMPERATURE = 1.0
DEFAULT_TOP_P = 1.0
