"""
LLM API 配置中心
================
从环境变量解析所有 LLM 相关配置，作为全项目唯一配置来源。
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(APP_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_DIR, ".env"))
    load_dotenv(os.path.join(APP_DIR, ".env"))
except Exception:
    pass

# ---------- API 类型自动探测 ----------
_configured_api_url = (
    os.getenv("LLM_API_URL")
    or os.getenv("ANTHROPIC_API_URL")
    or os.getenv("OPENAI_CHAT_COMPLETIONS_URL")
    or ""
)

LLM_API_TYPE = os.getenv("LLM_API_TYPE", "").strip().lower()
if not LLM_API_TYPE:
    LLM_API_TYPE = (
        "anthropic"
        if _configured_api_url.endswith("/messages") or os.getenv("ANTHROPIC_API_KEY")
        else "openai"
    )

# ---------- 按协议分发配置 ----------
if LLM_API_TYPE == "anthropic":
    API_URL = _configured_api_url or ""
    API_KEY = (
        os.getenv("LLM_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    LLM_MODEL = os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL") or "qwen3.5-plus"
else:
    API_URL = _configured_api_url or ""
    API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    LLM_MODEL = os.getenv("LLM_MODEL") or "gpt-5.4"

VISION_MODEL = os.getenv("VISION_MODEL") or LLM_MODEL
LLM_STREAM = os.getenv("LLM_STREAM", "true").strip().lower() not in ("0", "false", "no")
LLM_MAX_CALLS_PER_MINUTE = int(os.getenv("LLM_MAX_CALLS_PER_MINUTE", "15"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_SSL_VERIFY = os.getenv("LLM_SSL_VERIFY", "true").strip().lower() not in ("0", "false", "no")
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "90"))
LLM_VISION_TIMEOUT = int(os.getenv("LLM_VISION_TIMEOUT", "60"))
