"""大模型调用层（OpenAI 兼容协议，DeepSeek / 通义 / Kimi / 智谱 / OpenAI 通用）。

设计要点：
* Key 只从环境变量读取；
* 传入规则引擎算好的量化依据，让模型"解释和微调"，而不是凭空编数字；
* 结构化 JSON 输出 + 严格校验 + 失败给出人话报错（页面直接展示）。
"""
from __future__ import annotations

import json
import re

import requests

from config import Config, llm_settings

SYSTEM_PROMPT = """你是「根脉」系统的农业气候决策专家，服务对象是中国的小农户。
你的任务：根据给定的气象数据、作物参数和规则引擎计算结果，给出可执行的灌溉决策。

硬性要求：
1. 只能使用用户消息中给出的数据，不得编造气象数值、产量或价格。
2. 灌水量必须与给定的净需水量、灌溉水利用系数一致，允许 ±15% 的专业微调，并说明微调理由。
3. 语言务实、给农民看：少术语，多动作（什么时候灌、灌多少、注意什么）。
4. 若存在暴雨、霜冻等更高优先级风险，必须明确指出并调整灌溉建议。

只输出 JSON，不要 Markdown 代码块，不要多余文字。字段如下：
{
  "decision": "建议灌溉 | 建议少量补水 | 暂缓灌溉",
  "net_mm": 数字,                       // 净灌水量 mm
  "gross_mm": 数字,                     // 毛灌水量 mm（已折算利用系数）
  "m3_per_mu": 数字,                    // 每亩立方米
  "timing": "具体时段建议，含时长",
  "confidence": "高 | 中 | 低",
  "reasons": ["3-5 条，每条一句话，必须带数值依据"],
  "risks": ["0-3 条需要注意的风险"],
  "next_check": "下次复查的时间点与要看什么"
}"""


FOLLOWUP_SYSTEM = """你是「根脉」系统的农业气候决策专家。农户刚拿到一条灌溉建议，现在要追问细节。

硬性要求：
1. 只能使用下面给出的数据作答；数据里没有的（农产品价格、产量、邻居经验、16 天预报之外的天气）直接说"这些数据里没有"，不要编造。
2. 每个结论后面尽量跟一个数值依据，例如"净缺口 0 mm"。
3. 说人话：短句、给动作，不要 Markdown 标题/表格/代码块；分点最多 3 条，每条独立一行。
4. 总长控制在 160 字以内。
5. 若农户换了条件（土壤、灌溉方式、地块面积、预报变化），要按新条件重新判断，明确写出"按新条件：建议 X，净灌 Y mm"，不要只是复述旧结论。
6. 信息不足时，先按最保守的假设回答，再反问一个最关键的问题。"""


class LLMError(Exception):
    """大模型调用失败，message 可直接展示给用户。"""


def available() -> bool:
    return llm_settings()["enabled"]


def _chat_completion(messages: list[dict], max_tokens: int = 900) -> str:
    """发一次 chat completions 请求，返回助手正文；错误翻译成人话。"""
    cfg = llm_settings()
    if not cfg["enabled"]:
        raise LLMError("未配置大模型 API Key")

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    body = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": Config.LLM_TEMPERATURE,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {Config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    last_error = ""
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=Config.LLM_TIMEOUT)
        except requests.Timeout as exc:
            last_error = "请求超时"
            if attempt == 0:
                continue
            raise LLMError(f"大模型请求超时（{Config.LLM_TIMEOUT:.0f}s），请检查网络或稍后重试") from exc
        except requests.RequestException as exc:
            raise LLMError(f"无法连接大模型服务：{exc.__class__.__name__}") from exc

        if resp.status_code == 401:
            raise LLMError("大模型鉴权失败（401）：LLM_API_KEY 无效或已过期")
        if resp.status_code == 404:
            raise LLMError(f"模型不存在（404）：请检查 LLM_MODEL={cfg['model']}")
        if resp.status_code == 429:
            last_error = "触发限流（429）"
            if attempt == 0:
                continue
            raise LLMError("大模型触发限流（429），请稍后重试或换用其他模型")
        if resp.status_code >= 500:
            last_error = f"服务端错误（{resp.status_code}）"
            if attempt == 0:
                continue
            raise LLMError(f"大模型服务异常（{resp.status_code}），请稍后重试")
        if resp.status_code >= 400:
            snippet = resp.text[:200].replace("\n", " ")
            raise LLMError(f"大模型返回 {resp.status_code}：{snippet}")

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError("大模型响应结构异常，无法解析") from exc

    raise LLMError(last_error or "大模型调用失败")


def _build_messages(ctx: dict) -> list[dict]:
    payload = {
        "作物": ctx["crop"]["name"],
        "生育期": ctx["stage"]["name"],
        "距成熟天数": ctx["stage"]["days_to_maturity"],
        "地块面积_亩": ctx["area_mu"],
        "土壤": ctx["soil_label"],
        "灌溉方式": ctx["method_label"],
        "当前天气": ctx["current"],
        "未来3天": ctx["forecast3"],
        "风险等级": ctx["risk"]["level"],
        "风险明细": [
            {"类型": i["title"], "数值": i["value"], "阈值": i["threshold"], "说明": i["detail"]}
            for i in ctx["risk"]["items"]
        ],
        "规则引擎计算": {
            "3日作物需水_ETc_mm": ctx["rule"]["_etc3"],
            "3日有效降水_mm": ctx["rule"]["_pe3"],
            "净缺口_mm": ctx["rule"]["_gap3"],
            "推荐净灌水_mm": ctx["rule"]["net_mm"],
            "折算毛灌水_mm": ctx["rule"]["gross_mm"],
            "每亩立方米": ctx["rule"]["m3_per_mu"],
            "建议": ctx["rule"]["decision"],
        },
    }
    user = (
        "以下是今天的决策输入，请给出灌溉建议：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n请严格按要求输出 JSON。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise LLMError("大模型返回内容不是合法 JSON，已保留原文供排查")


def generate_irrigation(ctx: dict) -> dict:
    cfg = llm_settings()
    if not cfg["enabled"]:
        raise LLMError("未配置大模型 API Key")
    content = _chat_completion(_build_messages(ctx), max_tokens=900)
    parsed = _extract_json(content)
    return _normalize(parsed, ctx, cfg)


def _followup_brief(ctx: dict, decision: dict) -> dict:
    """追问时喂给模型的背景：与决策同一批数据，保证口径一致。"""
    return {
        "地块": {
            "作物": ctx["crop"]["name"],
            "生育期": ctx["stage"]["name"],
            "距成熟天数": ctx["stage"]["days_to_maturity"],
            "面积_亩": ctx["area_mu"],
            "土壤": ctx["soil_label"],
            "灌溉方式": ctx["method_label"],
        },
        "当前天气": ctx["current"],
        "未来3天": ctx["forecast3"],
        "风险等级": ctx["risk"]["level"],
        "风险明细": [
            {"类型": i["title"], "数值": i["value"], "阈值": i["threshold"], "说明": i["detail"]}
            for i in ctx["risk"]["items"]
        ],
        "水分平衡": {
            "3日作物需水_ETc_mm": ctx["rule"]["_etc3"],
            "3日有效降水_mm": ctx["rule"]["_pe3"],
            "净缺口_mm": ctx["rule"]["_gap3"],
            "土壤有效水容量_mm": ctx["rule"]["_awc"],
            "灌溉水利用系数": ctx["rule"]["_eff"],
        },
        "已给出的建议": {
            "决策": decision.get("decision"),
            "净灌_mm": decision.get("net_mm"),
            "毛灌_mm": decision.get("gross_mm"),
            "每亩立方米": decision.get("m3_per_mu"),
            "地块总用水_m3": decision.get("total_m3"),
            "时段": decision.get("timing"),
            "置信度": decision.get("confidence"),
            "理由": decision.get("reasons") or [],
            "风险": decision.get("risks") or [],
            "复查": decision.get("next_check"),
            "来源": "大模型" if decision.get("source") == "llm" else "本地规则引擎",
        },
    }


def chat(ctx: dict, decision: dict, history: list, question: str) -> str:
    """围绕当前决策做多轮追问。history 为 [{role, content}]，不含本轮问题。"""
    system = (
        FOLLOWUP_SYSTEM
        + "\n\n以下是本次决策的数据与结论（JSON）：\n"
        + json.dumps(_followup_brief(ctx, decision or {}), ensure_ascii=False, indent=2)
    )
    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-8:]:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": content[:600]})
    messages.append({"role": "user", "content": question.strip()[:600]})
    return (_chat_completion(messages, max_tokens=700) or "").strip()


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize(data: dict, ctx: dict, cfg: dict) -> dict:
    rule = ctx["rule"]
    net = max(0.0, min(_num(data.get("net_mm"), rule["net_mm"]), rule["net_mm"] * 1.6 + 6))
    gross = _num(data.get("gross_mm"), rule["gross_mm"])
    if gross <= 0 and net > 0:
        gross = net / max(0.4, rule.get("_eff", 0.75))
    m3 = _num(data.get("m3_per_mu"), gross * 0.667)

    reasons = data.get("reasons") or []
    risks = data.get("risks") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    if not isinstance(risks, list):
        risks = [str(risks)]

    return {
        "decision": str(data.get("decision") or rule["decision"]),
        "net_mm": round(net, 1),
        "gross_mm": round(gross, 1),
        "m3_per_mu": round(m3, 1),
        "total_m3": round(m3 * max(ctx["area_mu"], 0), 1),
        "timing": str(data.get("timing") or rule["timing"]),
        "confidence": str(data.get("confidence") or "中"),
        "reasons": [str(r) for r in reasons][:6],
        "risks": [str(r) for r in risks][:4],
        "next_check": str(data.get("next_check") or rule["next_check"]),
        "source": "llm",
        "model": cfg["model"],
    }
