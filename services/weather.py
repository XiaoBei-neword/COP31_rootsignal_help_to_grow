"""气象数据接入层。

优先真实 API（和风 / 心知 / Open-Meteo），任一 Key 缺失或调用失败时：
  * 若 ALLOW_DEMO_WEATHER=true，回落到「演示数据」，并在返回值里标记 is_demo=True，
    页面必须显示醒目标识，绝不伪装成真实气象；
  * 否则抛出 WeatherError，由页面明确提示"无数据"。

所有 provider 统一输出 WeatherBundle（current + 逐日 daily(+可选 past)）。
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests

from config import Config

UA = {"User-Agent": "RootSignal/1.0 (climate-agri-demo)"}


class WeatherError(Exception):
    """气象数据获取失败（Key 未配置、网络异常、接口返回错误等）。"""


@dataclass
class WeatherBundle:
    source: str                 # qweather / seniverse / openmeteo / demo
    source_label: str
    is_demo: bool
    location_name: str
    latitude: float
    longitude: float
    fetched_at: str
    current: dict = field(default_factory=dict)
    daily: list = field(default_factory=list)      # 今天起，逐日
    past: list = field(default_factory=list)       # 近 7 天（有则更好）
    horizon_days: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_label": self.source_label,
            "is_demo": self.is_demo,
            "location_name": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "fetched_at": self.fetched_at,
            "current": self.current,
            "daily": self.daily,
            "past": self.past,
            "horizon_days": self.horizon_days,
        }


_CACHE: dict[str, tuple[float, WeatherBundle]] = {}
_LAST_SOURCE: dict = {}


def last_source() -> dict:
    """最近一次成功取数的数据源，供侧栏状态灯显示真实情况。"""
    return dict(_LAST_SOURCE)


def _remember(bundle: WeatherBundle) -> None:
    _LAST_SOURCE.update({
        "provider": bundle.source,
        "label": bundle.source_label,
        "is_demo": bundle.is_demo,
        "fetched_at": bundle.fetched_at,
    })


def _cache_key(lat: float, lon: float) -> str:
    return f"{Config.WEATHER_PROVIDER}:{round(lat, 3)}:{round(lon, 3)}"


def fetch_weather(lat: float, lon: float, location_name: str = "") -> WeatherBundle:
    key = _cache_key(lat, lon)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < Config.WEATHER_CACHE_TTL:
        bundle = hit[1]
        if location_name:
            bundle.location_name = location_name
        return bundle

    errors: list[str] = []
    for provider in _provider_chain():
        try:
            bundle = provider(lat, lon, location_name)
            _remember(bundle)
            _CACHE[key] = (time.time(), bundle)
            return bundle
        except WeatherError as exc:
            errors.append(f"{provider.__name__}: {exc}")

    if Config.ALLOW_DEMO_WEATHER:
        bundle = _demo(lat, lon, location_name)
        bundle.source_label = "演示数据（模拟）"
        bundle.current["_fallback_reason"] = "；".join(errors) or "未配置气象 API Key"
        _remember(bundle)
        _CACHE[key] = (time.time(), bundle)
        return bundle

    raise WeatherError("；".join(errors) or "未配置可用的气象数据源")


def _provider_chain():
    p = Config.WEATHER_PROVIDER
    if p == "qweather":
        return [_qweather]
    if p == "seniverse":
        return [_seniverse]
    if p == "openmeteo":
        return [_openmeteo]
    if p == "demo":
        return [_demo]
    chain = []
    if Config.QWEATHER_KEY:
        chain.append(_qweather)
    if Config.SENIVERSE_KEY:
        chain.append(_seniverse)
    chain.append(_openmeteo)   # 无需 Key，作为兜底真实数据源
    return chain


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _get(url: str, **kwargs) -> dict:
    try:
        resp = requests.get(url, timeout=Config.WEATHER_TIMEOUT, headers=UA, **kwargs)
    except requests.RequestException as exc:
        raise WeatherError(f"网络请求失败：{exc.__class__.__name__}") from exc
    if resp.status_code >= 400:
        raise WeatherError(f"接口返回 HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise WeatherError("接口返回非 JSON 数据") from exc
    return payload


# --------------------------------------------------------------------------- #
# 和风天气
# --------------------------------------------------------------------------- #
def _qweather(lat: float, lon: float, location_name: str) -> WeatherBundle:
    if not Config.QWEATHER_KEY:
        raise WeatherError("未配置 QWEATHER_KEY")
    key = Config.QWEATHER_KEY
    loc = f"{lon:.4f},{lat:.4f}"
    now = _get(f"{Config.QWEATHER_HOST}/v7/weather/now", params={"location": loc, "key": key})
    if now.get("code") != "200":
        raise WeatherError(f"和风 now 接口 code={now.get('code')}")
    d7 = _get(f"{Config.QWEATHER_HOST}/v7/weather/7d", params={"location": loc, "key": key})
    if d7.get("code") != "200":
        raise WeatherError(f"和风 7d 接口 code={d7.get('code')}")

    n = now["now"]
    current = {
        "temp": float(n.get("temp", 0)),
        "feels_like": float(n.get("feelsLike", n.get("temp", 0))),
        "humidity": float(n.get("humidity", 0)),
        "precip": float(n.get("precip", 0) or 0),
        "wind_kmh": float(n.get("windSpeed", 0) or 0),
        "text": n.get("text", ""),
        "pressure": float(n.get("pressure", 0) or 0),
        "vis": float(n.get("vis", 0) or 0),
    }
    daily = []
    for d in d7.get("daily", []):
        daily.append({
            "date": d["fxDate"],
            "tmax": float(d.get("tempMax", 0)),
            "tmin": float(d.get("tempMin", 0)),
            "precip": float(d.get("precip", 0) or 0),
            "humidity": float(d.get("humidity", 0) or 0),
            "wind_kmh": float(d.get("windSpeedDay", 0) or 0),
            "text": d.get("textDay", ""),
            "et0": None,
            "uv": float(d.get("uvIndex", 0) or 0),
        })
    return WeatherBundle(
        source="qweather", source_label="和风天气 QWeather", is_demo=False,
        location_name=location_name, latitude=lat, longitude=lon,
        fetched_at=_now_text(), current=current, daily=daily, past=[],
        horizon_days=len(daily),
    )


# --------------------------------------------------------------------------- #
# 心知天气
# --------------------------------------------------------------------------- #
def _seniverse(lat: float, lon: float, location_name: str) -> WeatherBundle:
    if not Config.SENIVERSE_KEY:
        raise WeatherError("未配置 SENIVERSE_KEY")
    loc = f"{lat:.4f}:{lon:.4f}"
    now = _get("https://api.seniverse.com/v3/weather/now.json",
               params={"key": Config.SENIVERSE_KEY, "location": loc, "language": "zh-Hans", "unit": "c"})
    daily_raw = _get("https://api.seniverse.com/v3/weather/daily.json",
                     params={"key": Config.SENIVERSE_KEY, "location": loc, "language": "zh-Hans",
                             "unit": "c", "start": 0, "days": 7})
    try:
        n = now["results"][0]["now"]
        days = daily_raw["results"][0]["daily"]
    except (KeyError, IndexError) as exc:
        raise WeatherError("心知返回结构异常") from exc

    current = {
        "temp": float(n["temperature"]),
        "feels_like": float(n["temperature"]),
        "humidity": float(n.get("humidity", 0) or 0),
        "precip": 0.0,
        "wind_kmh": float(n.get("wind_speed", 0) or 0),
        "text": n.get("text", ""),
        "pressure": float(n.get("pressure", 0) or 0),
        "vis": float(n.get("visibility", 0) or 0),
    }
    daily = []
    for d in days:
        daily.append({
            "date": d["date"],
            "tmax": float(d["high"]),
            "tmin": float(d["low"]),
            "precip": float(d.get("precip", 0) or 0),
            "humidity": float(d.get("humidity", 0) or 0),
            "wind_kmh": float(d.get("wind_speed", 0) or 0),
            "text": d.get("text_day", ""),
            "et0": None,
            "uv": None,
        })
    return WeatherBundle(
        source="seniverse", source_label="心知天气 Seniverse", is_demo=False,
        location_name=location_name, latitude=lat, longitude=lon,
        fetched_at=_now_text(), current=current, daily=daily, past=[],
        horizon_days=len(daily),
    )


# --------------------------------------------------------------------------- #
# Open-Meteo（免 Key）
# --------------------------------------------------------------------------- #
WMO_TEXT = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨", 71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "强阵雨", 82: "暴雨", 95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴",
}


def _openmeteo(lat: float, lon: float, location_name: str) -> WeatherBundle:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
                   "weather_code,wind_speed_10m,surface_pressure",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "et0_fao_evapotranspiration,wind_speed_10m_max,relative_humidity_2m_mean,weather_code",
        "past_days": 7, "forecast_days": 16,
    }
    payload = _get("https://api.open-meteo.com/v1/forecast", params=params)
    cur = payload.get("current") or {}
    d = payload.get("daily") or {}
    dates = d.get("time") or []
    if not dates:
        raise WeatherError("Open-Meteo 未返回逐日数据")

    today = date.today().isoformat()
    current = {
        "temp": float(cur.get("temperature_2m") or 0),
        "feels_like": float(cur.get("apparent_temperature") or cur.get("temperature_2m") or 0),
        "humidity": float(cur.get("relative_humidity_2m") or 0),
        "precip": float(cur.get("precipitation") or 0),
        "wind_kmh": float(cur.get("wind_speed_10m") or 0),
        "text": WMO_TEXT.get(cur.get("weather_code"), "—"),
        "pressure": float(cur.get("surface_pressure") or 0),
        "vis": None,
    }
    daily, past = [], []
    for i, day in enumerate(dates):
        item = {
            "date": day,
            "tmax": float(d["temperature_2m_max"][i] or 0),
            "tmin": float(d["temperature_2m_min"][i] or 0),
            "precip": float(d["precipitation_sum"][i] or 0),
            "humidity": float((d.get("relative_humidity_2m_mean") or [None] * len(dates))[i] or 0),
            "wind_kmh": float(d["wind_speed_10m_max"][i] or 0),
            "text": WMO_TEXT.get((d.get("weather_code") or [None] * len(dates))[i], "—"),
            "et0": (d.get("et0_fao_evapotranspiration") or [None] * len(dates))[i],
            "uv": None,
        }
        (past if day < today else daily).append(item)

    return WeatherBundle(
        source="openmeteo", source_label="Open-Meteo", is_demo=False,
        location_name=location_name, latitude=lat, longitude=lon,
        fetched_at=_now_text(), current=current, daily=daily, past=past,
        horizon_days=len(daily),
    )


# --------------------------------------------------------------------------- #
# 演示数据（确定性生成，同一地点同一天结果一致）
# --------------------------------------------------------------------------- #
def _demo(lat: float, lon: float, location_name: str) -> WeatherBundle:
    seed_src = f"{round(lat, 2)}:{round(lon, 2)}:{date.today().isoformat()}"
    seed = int(hashlib.md5(seed_src.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)

    # 气候基线：纬度决定年均温与年振幅，季节由日序驱动
    base = 23.5 - abs(lat - 23.0) * 0.52
    amp = 7.0 + max(0.0, lat - 20.0) * 0.55
    scenario = ["heat_dry", "storm", "rainy", "dry_spell"][seed % 4]

    def day_temp(doy: int) -> float:
        seasonal = base + amp * math.cos(2 * math.pi * (doy - 205) / 365.0)
        return seasonal + rng.uniform(-1.4, 1.4)

    today = date.today()
    daily, past = [], []
    for offset in range(-7, 15):
        cur_date = today + timedelta(days=offset)
        tmean = day_temp(cur_date.timetuple().tm_yday)
        diurnal = rng.uniform(7.5, 11.5)
        tmax = tmean + diurnal / 2
        tmin = tmean - diurnal / 2
        precip = 0.0
        text = "晴"
        r = rng.random()
        if r > 0.72:
            precip = round(rng.uniform(3, 22), 1)
            text = "中雨" if precip > 10 else "小雨"

        # 注入气候异常场景，让演示有戏可讲
        if offset >= 0:
            if scenario == "heat_dry" and 1 <= offset <= 4:
                tmax += 5.2; tmin += 2.4; precip = 0.0; text = "晴热"
            if scenario == "storm" and offset in (2, 3):
                precip = [64.0, 41.5][offset - 2]; text = "暴雨"
            if scenario == "rainy" and 1 <= offset <= 5:
                precip = round(rng.uniform(12, 28), 1); text = "连阴雨"
            if scenario == "dry_spell" and offset <= 3:
                precip = 0.0; text = "多云" if offset % 2 else "晴"

        item = {
            "date": cur_date.isoformat(),
            "tmax": round(tmax, 1), "tmin": round(tmin, 1),
            "precip": precip,
            "humidity": round(min(96, max(35, rng.uniform(48, 88) + (12 if precip else 0))), 0),
            "wind_kmh": round(rng.uniform(8, 26) + (18 if precip > 30 else 0), 1),
            "text": text,
            "et0": None,
            "uv": None,
        }
        (past if offset < 0 else daily).append(item)

    cur = daily[0]
    current = {
        "temp": round((cur["tmax"] + cur["tmin"]) / 2, 1),
        "feels_like": round((cur["tmax"] + cur["tmin"]) / 2 + 1.2, 1),
        "humidity": cur["humidity"],
        "precip": 0.0,
        "wind_kmh": cur["wind_kmh"],
        "text": cur["text"],
        "pressure": 1006,
        "vis": 18,
    }
    bundle = WeatherBundle(
        source="demo", source_label="演示数据（模拟）", is_demo=True,
        location_name=location_name, latitude=lat, longitude=lon,
        fetched_at=_now_text(), current=current, daily=daily, past=past,
        horizon_days=len(daily),
    )
    bundle.current["_scenario"] = scenario
    return bundle


# --------------------------------------------------------------------------- #
# 地点检索
# --------------------------------------------------------------------------- #
FALLBACK_CITIES = [
    ("北京", 39.9042, 116.4074), ("天津", 39.0842, 117.2010), ("石家庄", 38.0428, 114.5149),
    ("太原", 37.8706, 112.5489), ("呼和浩特", 40.8414, 111.7519), ("沈阳", 41.8057, 123.4315),
    ("长春", 43.8171, 125.3235), ("哈尔滨", 45.8038, 126.5349), ("上海", 31.2304, 121.4737),
    ("南京", 32.0603, 118.7969), ("杭州", 30.2741, 120.1551), ("合肥", 31.8206, 117.2272),
    ("福州", 26.0745, 119.2965), ("南昌", 28.6820, 115.8579), ("济南", 36.6512, 117.1201),
    ("郑州", 34.7466, 113.6254), ("武汉", 30.5928, 114.3055), ("长沙", 28.2282, 112.9388),
    ("广州", 23.1291, 113.2644), ("南宁", 22.8170, 108.3665), ("海口", 20.0444, 110.1999),
    ("重庆", 29.5630, 106.5516), ("成都", 30.5728, 104.0668), ("贵阳", 26.6470, 106.6302),
    ("昆明", 25.0389, 102.7183), ("拉萨", 29.6520, 91.1721), ("西安", 34.3416, 108.9398),
    ("兰州", 36.0611, 103.8343), ("西宁", 36.6171, 101.7782), ("银川", 38.4872, 106.2309),
    ("乌鲁木齐", 43.8256, 87.6168), ("荆州", 30.3350, 112.2390), ("南阳", 32.9908, 112.5285),
    ("周口", 33.6250, 114.6969), ("驻马店", 33.0114, 114.0223), ("菏泽", 35.2333, 115.4690),
    ("潍坊", 36.7069, 119.1618), ("德州", 37.4355, 116.3593), ("盐城", 33.3496, 120.1616),
    ("徐州", 34.2058, 117.2841), ("衡阳", 26.8938, 112.5719), ("赣州", 25.8310, 114.9330),
    ("遵义", 27.7256, 106.9272), ("曲靖", 25.4900, 103.7962), ("运城", 35.0263, 111.0069),
]


def geocode(name: str, count: int = 6) -> list[dict]:
    name = (name or "").strip()
    if not name:
        return []
    results: list[dict] = []
    try:
        payload = _get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": name, "count": count, "language": "zh", "format": "json"})
        for item in payload.get("results") or []:
            label = " ".join(x for x in [
                item.get("name"), item.get("admin1"), item.get("country")] if x)
            results.append({
                "name": item.get("name") or label,
                "label": label,
                "latitude": item.get("latitude"),
                "longitude": item.get("longitude"),
                "country": item.get("country", ""),
                "admin": item.get("admin1", ""),
            })
    except WeatherError:
        pass

    if not results:
        for city, la, lo in FALLBACK_CITIES:
            if name in city or city in name:
                results.append({
                    "name": city, "label": f"{city} 中国",
                    "latitude": la, "longitude": lo,
                    "country": "中国", "admin": "",
                })
    return results[:count]
