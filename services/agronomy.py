"""农艺计算引擎：参考蒸散、水分平衡、气候风险打分、收获窗口、余量调度。

这里的每个数字都给出可复核的口径（页面上的「计算口径」），
避免出现"看起来很专业但说不清怎么来的"结论。
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

CROP_FILE = Path(__file__).resolve().parent.parent / "data" / "crops.json"

SOIL_AWC = {           # 有效水容量 (mm 水 / m 土)
    "sand": 90.0,
    "loam": 160.0,
    "clay": 190.0,
}
SOIL_LABEL = {"sand": "砂质土", "loam": "壤土", "clay": "黏质土"}

IRRIGATION_EFFICIENCY = {"flood": 0.55, "sprinkler": 0.75, "drip": 0.90}
IRRIGATION_LABEL = {"flood": "地面漫灌", "sprinkler": "喷灌", "drip": "滴灌"}

LEVELS = [
    (75, "极高", "critical"),
    (50, "高", "high"),
    (25, "中", "medium"),
    (0, "低", "low"),
]


@lru_cache(maxsize=1)
def load_crops() -> dict:
    with CROP_FILE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    data.pop("_meta", None)
    return data


def get_crop(key: str) -> dict:
    crops = load_crops()
    return crops.get(key) or crops["rice"]


def crop_list() -> list[dict]:
    return [{"key": k, **v} for k, v in load_crops().items()]


def level_of(score: float) -> tuple[str, str]:
    for threshold, label, css in LEVELS:
        if score >= threshold:
            return label, css
    return "低", "low"


# --------------------------------------------------------------------------- #
# 参考蒸散 ET0：Hargreaves-Samani（缺测站气象要素时的 FAO 推荐替代法）
# --------------------------------------------------------------------------- #
def _extraterrestrial_radiation(lat: float, doy: int) -> float:
    """日天文辐射 Ra，返回 mm/day 当量。"""
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    x = max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta)))
    omega = math.acos(x)
    ra_mj = (24 * 60 / math.pi) * 0.0820 * dr * (
        omega * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(omega)
    )
    return max(0.0, ra_mj * 0.408)


def et0_day(tmax: float, tmin: float, lat: float, day: date) -> float:
    if tmax < tmin:
        tmax, tmin = tmin, tmax
    ra = _extraterrestrial_radiation(lat, day.timetuple().tm_yday)
    tmean = (tmax + tmin) / 2
    td = max(0.1, tmax - tmin)
    et0 = 0.0023 * (tmean + 17.8) * math.sqrt(td) * ra
    return round(max(0.0, min(et0, 14.0)), 2)


def effective_precip(p: float) -> float:
    """有效降水简化式：小雨全计，中雨以上按 80% 计。"""
    if p <= 0:
        return 0.0
    return p * (1.0 if p <= 5 else 0.8)


# --------------------------------------------------------------------------- #
# 生育期
# --------------------------------------------------------------------------- #
def growth_stage(crop: dict, planting_date: str | None) -> dict:
    stages = crop["stages"]
    total = crop["growth_days"]
    if not planting_date:
        return {
            "known": False, "index": 2, "name": stages[2]["name"],
            "k": stages[2]["k"], "progress": 0.5,
            "days_since": None, "days_to_maturity": None, "maturity_date": None,
        }
    try:
        planted = datetime.strptime(planting_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return growth_stage(crop, None)

    elapsed = (date.today() - planted).days
    progress = max(0.0, min(1.15, elapsed / total))
    idx = 0
    for i, st in enumerate(stages):
        if progress <= st["end"]:
            idx = i
            break
        idx = i
    maturity = planted + timedelta(days=total)
    return {
        "known": True,
        "index": idx,
        "name": stages[idx]["name"],
        "k": stages[idx]["k"],
        "progress": round(progress, 3),
        "days_since": elapsed,
        "days_to_maturity": (maturity - date.today()).days,
        "maturity_date": maturity.isoformat(),
    }


# --------------------------------------------------------------------------- #
# 气候风险评估
# --------------------------------------------------------------------------- #
def assess_risk(crop: dict, bundle, stage: dict) -> dict:
    lat = bundle.latitude
    days = bundle.daily[:7]
    if not days:
        return {"score": 0, "level": "无数据", "css": "low", "items": [], "daily": [], "window": 0}

    rows = []
    for item in days:
        d = datetime.strptime(item["date"], "%Y-%m-%d").date()
        et0 = item.get("et0")
        if et0 is None:
            et0 = et0_day(item["tmax"], item["tmin"], lat, d)
        etc = et0 * stage["k"]
        pe = effective_precip(item["precip"])
        rows.append({**item, "et0": round(float(et0), 2), "etc": round(etc, 2), "pe": round(pe, 2),
                     "deficit": round(max(0.0, etc - pe), 2)})

    items = []
    sensitivity = 1.25 if stage["k"] >= 1.1 else (1.0 if stage["k"] >= 0.85 else 0.85)

    # 1) 高温胁迫
    heat_rows = [r for r in rows if r["tmax"] >= crop["heat_threshold"]]
    if heat_rows:
        peak = max(heat_rows, key=lambda r: r["tmax"])
        excess = peak["tmax"] - crop["heat_threshold"]
        score = min(100, (28 + excess * 11) * sensitivity)
        items.append({
            "kind": "heat", "title": "高温热害",
            "value": f"{peak['tmax']:.1f}℃", "threshold": f"阈值 {crop['heat_threshold']}℃",
            "date": peak["date"], "score": round(score, 1), **dict(zip(("level", "css"), level_of(score))),
            "detail": f"最高温超阈值 {excess:.1f}℃，共 {len(heat_rows)} 天受影响，"
                      f"当前处于{stage['name']}，{'开花/灌浆' if sensitivity > 1 else '生长'}期对高温敏感。",
        })

    # 2) 低温冷害 / 霜冻
    cold_rows = [r for r in rows if r["tmin"] <= crop["cold_threshold"]]
    if cold_rows:
        low = min(cold_rows, key=lambda r: r["tmin"])
        gap = crop["cold_threshold"] - low["tmin"]
        is_frost = low["tmin"] <= crop["frost_threshold"]
        score = min(100, (40 if is_frost else 22) + gap * 8)
        items.append({
            "kind": "cold", "title": "霜冻" if is_frost else "低温冷害",
            "value": f"{low['tmin']:.1f}℃", "threshold": f"阈值 {crop['cold_threshold']}℃",
            "date": low["date"], "score": round(score, 1), **dict(zip(("level", "css"), level_of(score))),
            "detail": f"最低温 {low['tmin']:.1f}℃，{'已达霜冻标准，' if is_frost else ''}低于{crop['name']}耐受下限 {gap:.1f}℃。",
        })

    # 3) 水分亏缺（干旱）
    total_etc = sum(r["etc"] for r in rows)
    total_pe = sum(r["pe"] for r in rows)
    dry_run = 0
    for r in rows:
        if r["precip"] < 0.5:
            dry_run += 1
        else:
            break
    if bundle.past:
        for r in reversed(bundle.past[-7:]):
            if r["precip"] < 0.5:
                dry_run += 1
            else:
                break
    water_gap = max(0.0, total_etc - total_pe)
    gap_per_day = water_gap / len(rows)
    if gap_per_day >= 1.0 or dry_run >= 5:
        score = min(100, gap_per_day * 11 + max(0, dry_run - 3) * 5)
        items.append({
            "kind": "drought", "title": "水分亏缺",
            "value": f"{water_gap:.0f} mm / {len(rows)}天",
            "threshold": f"作物需水 {total_etc:.0f} mm",
            "date": rows[0]["date"], "score": round(score, 1),
            **dict(zip(("level", "css"), level_of(score))),
            "detail": f"未来 {len(rows)} 天参考蒸散 {total_etc:.0f} mm，有效降水仅 {total_pe:.0f} mm，"
                      f"缺口 {water_gap:.0f} mm；已连续 {dry_run} 天无明显降水。",
        })

    # 4) 暴雨 / 内涝
    flood_rows = [r for r in rows if r["precip"] >= crop["flood_threshold"]]
    three_day = [sum(x["precip"] for x in rows[i:i + 3]) for i in range(len(rows) - 2)]
    if flood_rows or (three_day and max(three_day) >= crop["flood_threshold"] * 1.3):
        peak = max(rows, key=lambda r: r["precip"])
        score = min(100, 30 + peak["precip"] * 0.7)
        items.append({
            "kind": "flood", "title": "暴雨内涝",
            "value": f"{peak['precip']:.1f} mm", "threshold": f"阈值 {crop['flood_threshold']} mm/日",
            "date": peak["date"], "score": round(score, 1), **dict(zip(("level", "css"), level_of(score))),
            "detail": f"{peak['date']} 预报降水 {peak['precip']:.1f} mm，"
                      f"{crop['name']}耐涝性{crop['waterlogging_sensitivity']}，需提前疏通沟渠并暂缓灌溉。",
        })

    # 5) 大风倒伏
    wind_rows = [r for r in rows if r["wind_kmh"] >= crop["wind_threshold"]]
    if wind_rows:
        peak = max(wind_rows, key=lambda r: r["wind_kmh"])
        score = min(100, 30 + (peak["wind_kmh"] - crop["wind_threshold"]) * 1.6)
        items.append({
            "kind": "wind", "title": "大风倒伏",
            "value": f"{peak['wind_kmh']:.0f} km/h", "threshold": f"阈值 {crop['wind_threshold']} km/h",
            "date": peak["date"], "score": round(score, 1), **dict(zip(("level", "css"), level_of(score))),
            "detail": f"{peak['date']} 阵风可达 {peak['wind_kmh']:.0f} km/h，倒伏风险影响机械收割与产量。",
        })

    # 6) 高湿病害
    humid = [r for r in rows[:4] if r["humidity"] >= 85 and r["precip"] > 0]
    if len(humid) >= 2:
        score = 28 + len(humid) * 7
        items.append({
            "kind": "disease", "title": "高湿病害",
            "value": f"连续 {len(humid)} 天",
            "threshold": "湿度≥85% 且 有降水",
            "date": humid[0]["date"], "score": round(score, 1),
            **dict(zip(("level", "css"), level_of(score))),
            "detail": f"连续 {len(humid)} 天高湿伴降水，叶面结露时间长，"
                      f"易诱发{crop['name']}真菌性病害，建议雨后巡田。",
        })

    # 综合评分：取最高项为主，叠加次要项
    if items:
        items.sort(key=lambda x: -x["score"])
        top = items[0]["score"]
        others = sum(i["score"] for i in items[1:]) * 0.18
        total = min(100, top * 0.85 + others)
    else:
        total = 12.0

    daily_risk = []
    for r in rows:
        day_score = 10.0
        if r["tmax"] >= crop["heat_threshold"]:
            day_score += 22 + (r["tmax"] - crop["heat_threshold"]) * 8
        if r["tmin"] <= crop["cold_threshold"]:
            day_score += min(72, 32 + (crop["cold_threshold"] - r["tmin"]) * 5.5)
        if r["precip"] >= crop["flood_threshold"]:
            day_score += 34
        elif r["precip"] >= crop["flood_threshold"] * 0.55:
            day_score += 14
        if r["wind_kmh"] >= crop["wind_threshold"]:
            day_score += 16
        if r["deficit"] >= 5:
            day_score += 12
        day_score = min(100, day_score)
        label, css = level_of(day_score)
        daily_risk.append({
            "date": r["date"], "score": round(day_score, 1), "level": label, "css": css,
            "tmax": r["tmax"], "tmin": r["tmin"], "precip": r["precip"],
            "et0": r["et0"], "etc": r["etc"], "pe": r["pe"], "deficit": r["deficit"],
            "text": r["text"], "wind_kmh": r["wind_kmh"], "humidity": r["humidity"],
        })

    label, css = level_of(total)
    return {
        "score": round(total, 1), "level": label, "css": css,
        "items": items, "daily": daily_risk, "window": len(rows),
        "water": {
            "etc": round(total_etc, 1), "pe": round(total_pe, 1),
            "gap": round(water_gap, 1), "dry_run": dry_run,
        },
    }


# --------------------------------------------------------------------------- #
# 灌溉决策：规则引擎（大模型不可用时的兜底，也作为大模型的输入依据）
# --------------------------------------------------------------------------- #
def irrigation_plan(crop, stage, risk, soil: str, method: str, area_mu: float) -> dict:
    days = risk["daily"][:3]
    etc3 = sum(d["etc"] for d in days) or 0.0
    pe3 = sum(d["pe"] for d in days) or 0.0
    gap = max(0.0, etc3 - pe3)

    awc = SOIL_AWC.get(soil, 160.0) * min(0.25 + stage["progress"] * 0.55, 0.8)
    dry_bonus = min(0.25, risk["water"]["dry_run"] * 0.03)
    net_mm = min(gap * (1 + dry_bonus), awc * 0.8)

    heavy_rain = any(d["precip"] >= crop["flood_threshold"] * 0.6 for d in days)
    if heavy_rain:
        net_mm = 0.0

    eff = IRRIGATION_EFFICIENCY.get(method, 0.75)
    gross_mm = net_mm / eff if net_mm > 0 else 0.0
    m3_per_mu = gross_mm * 0.667
    total_m3 = m3_per_mu * max(area_mu, 0)

    if net_mm <= 0.5:
        decision = "暂缓灌溉"
        timing = "本轮无需灌溉，48 小时后复看预报"
        confidence = "高" if heavy_rain else "中"
    else:
        decision = "建议灌溉" if net_mm >= 5 else "建议少量补水"
        hot = any(d["tmax"] >= crop["heat_threshold"] for d in days)
        timing = "清晨 05:00–08:00 灌溉" if hot else "傍晚 17:00 后或清晨灌溉"
        confidence = "高" if risk["water"]["gap"] > 6 else "中"

    reasons = [
        f"未来 3 天{stage['name']}耗水（ETc）约 {etc3:.1f} mm，有效降水仅 {pe3:.1f} mm，净缺口 {gap:.1f} mm。",
        f"当前{SOIL_LABEL.get(soil, '壤土')}有效水容量约 {awc:.0f} mm，允许管理亏缺按 80% 计，上限 {awc * 0.8:.0f} mm。",
    ]
    if risk["water"]["dry_run"] >= 5:
        reasons.append(f"已连续 {risk['water']['dry_run']} 天无明显降水，土壤水库处于净消耗状态，净需水上调 {dry_bonus * 100:.0f}%。")
    if heavy_rain:
        reasons.append("预报 3 天内有强降水过程，灌溉将加剧渍害风险，故本轮归零。")
    if net_mm > 0:
        reasons.append(f"{IRRIGATION_LABEL.get(method, '喷灌')}利用系数取 {eff:.2f}，折算毛灌水量 {gross_mm:.1f} mm。")

    risks = []
    for item in risk["items"][:3]:
        if item["kind"] in {"heat", "flood", "cold", "wind"}:
            risks.append(f"{item['title']}：{item['detail']}")

    return {
        "decision": decision,
        "net_mm": round(net_mm, 1),
        "gross_mm": round(gross_mm, 1),
        "m3_per_mu": round(m3_per_mu, 1),
        "total_m3": round(total_m3, 1),
        "timing": timing,
        "confidence": confidence,
        "reasons": reasons,
        "risks": risks or ["未来 3 天无明显灾害性天气，按常规田管执行。"],
        "next_check": "48 小时后复看降水预报与土壤墒情" if net_mm > 0 else "72 小时后复看",
        "source": "rule",
        # 供大模型提示词引用的中间量（页面不直接展示）
        "_etc3": round(etc3, 1),
        "_pe3": round(pe3, 1),
        "_gap3": round(gap, 1),
        "_eff": eff,
        "_awc": round(awc, 1),
    }


# --------------------------------------------------------------------------- #
# 收获窗口
# --------------------------------------------------------------------------- #
def harvest_window(crop, bundle, planting_date: str | None, area_mu: float) -> dict:
    today = date.today()
    forcast = {}
    for item in bundle.daily:
        forcast[item["date"]] = item

    stage = growth_stage(crop, planting_date)
    if stage["known"]:
        maturity = datetime.strptime(stage["maturity_date"], "%Y-%m-%d").date()
    else:
        maturity = today + timedelta(days=10)

    horizon_end = today + timedelta(days=max(0, bundle.horizon_days - 1))
    start = max(today, maturity - timedelta(days=14))
    end = min(horizon_end, maturity + timedelta(days=10))
    if end < start:
        end = horizon_end

    candidates = []
    cur = start
    while cur <= end:
        key = cur.isoformat()
        if key in forcast:
            w = forcast[key]
            penalty = 0.0
            notes = []
            if w["precip"] >= 25:
                penalty += 42
                notes.append("强降水")
            elif w["precip"] >= 8:
                penalty += w["precip"] * 2.0
                notes.append("有降水")
            elif w["precip"] > 0.5:
                penalty += w["precip"] * 3.0
                notes.append("零星降水")
            if w["humidity"] >= 88:
                penalty += 10
                notes.append("高湿")
            if w["tmax"] >= crop["heat_threshold"]:
                penalty += 8
                notes.append("高温")
            if w["wind_kmh"] >= crop["wind_threshold"]:
                penalty += 10
                notes.append("大风")
            over = (cur - maturity).days
            if over > 7:
                penalty += (over - 7) * 2.5
                notes.append("过熟")
            if over < -7:
                penalty += (-over - 7) * 2.5
                notes.append("未熟")
            candidates.append({
                "date": key, "score": round(penalty, 1),
                "precip": w["precip"], "tmax": w["tmax"], "tmin": w["tmin"],
                "humidity": w["humidity"], "wind_kmh": w["wind_kmh"],
                "text": w["text"], "notes": notes,
                "suitability": "适宜" if penalty < 8 else ("可作业" if penalty < 22 else "不宜"),
            })
        cur += timedelta(days=1)

    if not candidates:
        return {"available": False, "maturity_date": maturity.isoformat(),
                "message": "预报窗口尚未覆盖成熟期，暂无法评估收获窗口。"}

    best = min(candidates, key=lambda c: c["score"])
    threshold = best["score"] + 14
    window_days = [c for c in candidates if c["score"] <= threshold]
    # 取与最佳日连续的区间
    idx_best = candidates.index(best)
    lo = hi = idx_best
    while lo - 1 >= 0 and candidates[lo - 1]["score"] <= threshold:
        lo -= 1
    while hi + 1 < len(candidates) and candidates[hi + 1]["score"] <= threshold:
        hi += 1
    window_days = candidates[lo:hi + 1]

    coverage = (maturity - today).days
    if coverage < 0:
        confidence = "高" if best["score"] < 10 else "中"
    elif coverage <= bundle.horizon_days:
        confidence = "中" if bundle.horizon_days >= 7 else "低"
    else:
        confidence = "低"

    # 收获后 5 天天气 → 产后损失情景
    end_date = datetime.strptime(window_days[-1]["date"], "%Y-%m-%d").date()
    rain_after = 0
    for i in range(1, 6):
        key = (end_date + timedelta(days=i)).isoformat()
        if key in forcast and forcast[key]["precip"] >= 5:
            rain_after += 1

    base_loss = crop["postharvest_loss"]
    if rain_after == 0:
        opt_loss = round(base_loss * 0.58, 1)
        risk_note = "收获后 5 天无明显降水，可田间自然晾晒，霉变与发芽风险低。"
    elif rain_after <= 2:
        opt_loss = round(base_loss * 0.74, 1)
        risk_note = f"收获后 5 天内有 {rain_after} 天降水，需在 24 小时内完成烘干或入库。"
    else:
        opt_loss = round(base_loss * 0.86, 1)
        risk_note = f"收获后 5 天内有 {rain_after} 天降水，必须机械烘干，否则霉变损失迅速放大。"

    return {
        "available": True,
        "maturity_date": maturity.isoformat(),
        "stage": stage,
        "candidates": candidates,
        "best": best,
        "window": {"start": window_days[0]["date"], "end": window_days[-1]["date"], "days": len(window_days)},
        "window_days": window_days,
        "confidence": confidence,
        "rain_days_after": rain_after,
        "loss_note": risk_note,
        "base_loss_pct": base_loss,
        "optimized_loss_pct": opt_loss,
        "forecast_covered": end >= min(maturity, horizon_end),
        "horizon_end": horizon_end.isoformat(),
    }


# --------------------------------------------------------------------------- #
# 余量调度 + 减损测算
# --------------------------------------------------------------------------- #
def surplus_plan(crop, area_mu: float, hw: dict, risk: dict) -> dict:
    y_lo, y_hi = crop["yield_kg_per_mu"]
    yield_per_mu = (y_lo + y_hi) / 2
    total_kg = yield_per_mu * max(area_mu, 0.1)

    base_loss = hw.get("base_loss_pct", crop["postharvest_loss"])
    opt_loss = hw.get("optimized_loss_pct", base_loss * 0.7)
    base_kg = total_kg * base_loss / 100
    opt_kg = total_kg * opt_loss / 100
    saved_kg = max(0.0, base_kg - opt_kg)
    price = crop["price_per_kg"]

    rain_after = hw.get("rain_days_after", 0)
    window_days = hw.get("window", {}).get("days", 1)

    # 抢收比例：窗口越窄、后续降雨越多，越要提前抢
    rush_ratio = 0.45
    if window_days <= 2:
        rush_ratio += 0.2
    if rain_after >= 2:
        rush_ratio += 0.15
    rush_ratio = min(0.85, rush_ratio)
    rush_kg = total_kg * rush_ratio

    dry_ratio = 0.35 if rain_after == 0 else (0.6 if rain_after <= 2 else 0.8)
    dry_kg = total_kg * dry_ratio
    market_kg = total_kg - rush_kg * 0.0  # 上市量按总产计

    actions = [
        {
            "title": "抢收排程",
            "quantity": f"{rush_kg / 1000:.1f} 吨（{area_mu * rush_ratio:.1f} 亩）",
            "detail": f"在 {hw.get('window', {}).get('start', '—')} 至 {hw.get('window', {}).get('end', '—')} 的"
                      f"{window_days} 天窗口内，优先收割成熟度最高的 {area_mu * rush_ratio:.1f} 亩；"
                      f"若窗口内有强降水，提前 1 天启动。",
            "saved_kg": round(saved_kg * 0.45, 0),
        },
        {
            "title": "干燥与暂存",
            "quantity": f"{dry_kg / 1000:.1f} 吨",
            "detail": (f"收获后 5 天降雨 {rain_after} 天，{hw.get('loss_note', '')}"
                       if rain_after else f"可田间晾晒至含水率 {crop['safe_moisture']:.0f}% 再入库，"
                                          f"预计减少霉变损失。"),
            "saved_kg": round(saved_kg * 0.35, 0),
        },
        {
            "title": "余量外运 / 订单对接",
            "quantity": f"{max(0.0, total_kg * 0.6) / 1000:.1f} 吨",
            "detail": f"本地消化按总产 40% 计，剩余约 {total_kg * 0.6 / 1000:.1f} 吨"
                      f"建议在收获前 5 天联系合作社或订单企业，分批上市避开集中上市价格低谷。",
            "saved_kg": round(saved_kg * 0.20, 0),
        },
    ]

    return {
        "yield_per_mu": yield_per_mu,
        "total_kg": round(total_kg, 0),
        "total_t": round(total_kg / 1000, 2),
        "base_loss_pct": base_loss,
        "optimized_loss_pct": opt_loss,
        "base_loss_kg": round(base_kg, 0),
        "optimized_loss_kg": round(opt_kg, 0),
        "saved_kg": round(saved_kg, 0),
        "saved_t": round(saved_kg / 1000, 2),
        "saved_value": round(saved_kg * price, 0),
        "price": price,
        "actions": actions,
        "rush_ratio": round(rush_ratio, 2),
        "rain_days_after": rain_after,
        "risk_level": risk.get("level", "低"),
    }
