"""集中配置：所有密钥只从环境变量 / .env 读取，绝不硬编码在源码里。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY")
    if key:
        return key
    # 本地演示：首次启动生成并落盘，避免每次重启掉登录态
    path = DATA_DIR / ".secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    path.write_text(key, encoding="utf-8")
    return key


class Config:
    SECRET_KEY = _secret_key()

    DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "rootsignal.db")))

    # ---------- 气象 ----------
    # auto: 按 qweather -> seniverse -> openmeteo -> demo 顺序挑第一个可用的
    WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "auto").strip().lower()
    QWEATHER_KEY = os.getenv("QWEATHER_KEY", "").strip()
    QWEATHER_HOST = os.getenv("QWEATHER_HOST", "https://devapi.qweather.com").strip().rstrip("/")
    SENIVERSE_KEY = os.getenv("SENIVERSE_KEY", "").strip()
    WEATHER_TIMEOUT = float(os.getenv("WEATHER_TIMEOUT", "12"))
    WEATHER_CACHE_TTL = int(os.getenv("WEATHER_CACHE_TTL", "900"))
    # 没有配置任何真实气象 Key 时，是否允许回落到"演示数据"。
    # 回落时页面会持续显示醒目的「演示数据」标识，不会伪装成真实气象。
    ALLOW_DEMO_WEATHER = _bool("ALLOW_DEMO_WEATHER", True)

    # ---------- 大模型 ----------
    # 任何 OpenAI 兼容接口都能用：deepseek / dashscope / moonshot / zhipu / openai ...
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()
    LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
    LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
    LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.3"))

    # 未配置大模型时是否使用本地规则引擎给出建议（UI 会明确标注来源）
    ALLOW_RULE_FALLBACK = _bool("ALLOW_RULE_FALLBACK", True)


LLM_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}


def llm_settings() -> dict:
    """返回实际生效的大模型配置；任一必需项缺失则 enabled=False。"""
    provider = Config.LLM_PROVIDER
    preset = LLM_PRESETS.get(provider, {})
    base_url = Config.LLM_BASE_URL or preset.get("base_url", "")
    model = Config.LLM_MODEL or preset.get("model", "")
    key = Config.LLM_API_KEY
    enabled = bool(key and base_url and model)
    return {
        "provider": provider or "custom",
        "base_url": base_url,
        "model": model,
        "enabled": enabled,
    }


def weather_status() -> dict:
    """暴露给页面显示：当前用的是哪个数据源、是否演示数据。

    页面首次取数前用它做"预期值"，取数成功后由 services.weather.last_source() 覆盖。
    """
    provider = Config.WEATHER_PROVIDER
    if provider == "qweather" or (provider == "auto" and Config.QWEATHER_KEY):
        return {"provider": "qweather", "label": "和风天气 QWeather", "keyed": True}
    if provider == "seniverse" or (provider == "auto" and Config.SENIVERSE_KEY):
        return {"provider": "seniverse", "label": "心知天气 Seniverse", "keyed": True}
    if provider in ("openmeteo", "auto"):
        return {"provider": "openmeteo", "label": "Open-Meteo", "keyed": False}
    return {"provider": "demo", "label": "演示数据", "keyed": False}
