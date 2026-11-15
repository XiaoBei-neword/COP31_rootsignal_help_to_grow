"""根脉 RootSignal —— 气候助农决策系统（本地运行版）。

闭环：气候异常 → 灌溉决策 → 收获窗口 → 减少产后损失
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db
from config import Config, llm_settings, weather_status
from services import agronomy, llm
from services.weather import WeatherError, fetch_weather, geocode, last_source

app = Flask(__name__)
app.config.from_object(Config)
app.teardown_appcontext(db.close_db)

NAV = [
    {"endpoint": "dashboard", "label": "田间风险看板", "step": "01", "hint": "气候异常"},
    {"endpoint": "irrigation", "label": "灌溉决策 Agent", "step": "02", "hint": "灌溉决策"},
    {"endpoint": "harvest", "label": "收获窗口与调度", "step": "03", "hint": "减少产后损失"},
    {"endpoint": "settings", "label": "地块与账号", "step": "04", "hint": "数据与隔离"},
]


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
def current_user():
    if "user" in g:
        return g.user
    uid = session.get("uid")
    g.user = db.query_one("SELECT * FROM users WHERE id = ?", (uid,)) if uid else None
    return g.user


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_globals():
    user = current_user()
    profile = db.ensure_profile(user["id"]) if user else None
    crop = agronomy.get_crop(profile["crop_key"]) if profile else None
    cfg = weather_status()
    live = last_source()
    if live:
        cfg = {"provider": live["provider"], "label": live["label"],
               "keyed": not live["is_demo"]}
    return {
        "user": user,
        "profile": profile,
        "crop": crop,
        "crops": agronomy.crop_list(),
        "nav": NAV,
        "today": date.today().isoformat(),
        "weather_cfg": cfg,
        "llm_cfg": llm_settings(),
    }


def _profile_or_redirect():
    """地块关键信息缺失时，把用户引到设置页补齐。"""
    user = current_user()
    profile = db.ensure_profile(user["id"])
    return profile


def _need_location(profile) -> bool:
    return profile["latitude"] is None or profile["longitude"] is None


def _bundle_for(profile):
    return fetch_weather(
        float(profile["latitude"]),
        float(profile["longitude"]),
        profile["location_name"] or "未命名地块",
    )


@app.template_filter("cn_date")
def cn_date(value: str) -> str:
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return value or ""
    return f"{d.month}月{d.day}日"


@app.template_filter("weekday")
def weekday(value: str) -> str:
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return "一二三四五六日"[d.weekday()]


# --------------------------------------------------------------------------- #
# 账号
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        error = None
        if not 2 <= len(username) <= 20:
            error = "账号长度需 2–20 个字符"
        elif len(password) < 6:
            error = "密码至少 6 位"
        elif password != confirm:
            error = "两次输入的密码不一致"
        elif db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
            error = "该账号已存在，直接登录即可"

        if error:
            return render_template("register.html", error=error, username=username)

        uid = db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (username, generate_password_hash(password), db.now_text()),
        )
        db.ensure_profile(uid)
        session.clear()
        session["uid"] = uid
        flash("账号已创建。先补全地块信息，看板才有数据可算。", "info")
        return redirect(url_for("settings", first="1"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        row = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
        if not row or not check_password_hash(row["password_hash"], password):
            # 登录失败用 200 重新渲染表单：401 会让浏览器/预览面板/代理
            # 直接显示「无法访问」，把错误提示吞掉。
            return render_template("login.html", error="账号或密码不正确", username=username)
        session.clear()
        session["uid"] = row["id"]
        nxt = request.args.get("next") or url_for("dashboard")
        if not nxt.startswith("/"):
            nxt = url_for("dashboard")
        return redirect(nxt)
    return render_template("login.html")


@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------- #
# 地块设置
# --------------------------------------------------------------------------- #
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    profile = _profile_or_redirect()
    if request.method == "POST":
        crop_key = request.form.get("crop_key") or "rice"
        area = request.form.get("area_mu") or "10"
        try:
            area_val = max(0.1, float(area))
        except ValueError:
            area_val = 10.0
        fields = {
            "crop_key": crop_key if crop_key in agronomy.load_crops() else "rice",
            "area_mu": area_val,
            "soil": request.form.get("soil") or "loam",
            "irrigation_method": request.form.get("irrigation_method") or "sprinkler",
            "planting_date": request.form.get("planting_date") or None,
        }
        lat = request.form.get("latitude")
        lon = request.form.get("longitude")
        if lat and lon:
            try:
                fields["latitude"] = float(lat)
                fields["longitude"] = float(lon)
                fields["location_name"] = (request.form.get("location_name") or "").strip()
            except ValueError:
                flash("经纬度格式不正确，未保存坐标", "error")
        db.save_profile(current_user()["id"], **fields)
        flash("地块信息已保存", "ok")
        return redirect(url_for("dashboard"))
    return render_template("settings.html", profile=profile,
                           soils=agronomy.SOIL_LABEL,
                           methods=agronomy.IRRIGATION_LABEL)


@app.route("/api/geocode")
@login_required
def api_geocode():
    q = request.args.get("q", "")
    try:
        return jsonify({"ok": True, "results": geocode(q)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"地点检索失败：{exc}"}), 502


@app.route("/api/crop/<key>")
@login_required
def api_crop(key: str):
    """切换作物时前端即时刷新参数卡片。"""
    crop = agronomy.get_crop(key)
    return jsonify({"ok": True, "crop": crop})


# --------------------------------------------------------------------------- #
# 模块 1：田间风险看板
# --------------------------------------------------------------------------- #
@app.route("/dashboard")
@login_required
def dashboard():
    profile = _profile_or_redirect()
    if _need_location(profile):
        flash("先设置地块位置，系统才知道去哪里取气象数据。", "info")
        return redirect(url_for("settings", first="1"))

    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    ctx = {"profile": profile, "crop": crop, "stage": stage, "bundle": None,
           "risk": None, "error": None}

    try:
        bundle = _bundle_for(profile)
        risk = agronomy.assess_risk(crop, bundle, stage)
        ctx.update(bundle=bundle, risk=risk,
                   precip_total=round(sum(d["precip"] for d in risk["daily"]), 1),
                   heat_days=sum(1 for d in risk["daily"] if d["tmax"] >= crop["heat_threshold"]))
    except WeatherError as exc:
        ctx["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("dashboard failed")
        ctx["error"] = f"数据处理失败：{exc}"

    return render_template("dashboard.html", **ctx, active="dashboard")


# --------------------------------------------------------------------------- #
# 模块 2：灌溉决策 Agent
# --------------------------------------------------------------------------- #
def _build_context(profile, crop, stage, bundle, risk):
    rule = agronomy.irrigation_plan(
        crop, stage, risk, profile["soil"], profile["irrigation_method"], profile["area_mu"]
    )
    forecast3 = [
        {"日期": d["date"], "最高温": d["tmax"], "最低温": d["tmin"],
         "降水_mm": d["precip"], "作物需水_ETc_mm": d["etc"], "有效降水_mm": d["pe"]}
        for d in risk["daily"][:3]
    ]
    return {
        "crop": crop,
        "stage": stage,
        "area_mu": profile["area_mu"],
        "soil_label": agronomy.SOIL_LABEL.get(profile["soil"], "壤土"),
        "method_label": agronomy.IRRIGATION_LABEL.get(profile["irrigation_method"], "喷灌"),
        "current": bundle.current,
        "forecast3": forecast3,
        "risk": risk,
        "rule": rule,
    }


@app.route("/irrigation")
@login_required
def irrigation():
    profile = _profile_or_redirect()
    if _need_location(profile):
        flash("先设置地块位置，才能计算灌溉需水。", "info")
        return redirect(url_for("settings", first="1"))

    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    error = None
    risk = rule = bundle = None
    try:
        bundle = _bundle_for(profile)
        risk = agronomy.assess_risk(crop, bundle, stage)
        rule = agronomy.irrigation_plan(crop, stage, risk, profile["soil"],
                                        profile["irrigation_method"], profile["area_mu"])
    except WeatherError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("irrigation page failed")
        error = f"数据处理失败：{exc}"

    history = db.query(
        "SELECT * FROM irrigation_records WHERE user_id = ? ORDER BY id DESC LIMIT 5",
        (current_user()["id"],),
    )
    records = []
    for row in history:
        try:
            payload = json.loads(row["result"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        records.append({"row": row, "data": payload})

    return render_template("irrigation.html", crop=crop, stage=stage, bundle=bundle,
                           risk=risk, rule=rule, error=error, records=records,
                           active="irrigation")


@app.route("/api/irrigation", methods=["POST"])
@login_required
def api_irrigation():
    profile = _profile_or_redirect()
    if _need_location(profile):
        return jsonify({"ok": False, "error": "尚未设置地块位置"}), 400

    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    try:
        bundle = _bundle_for(profile)
    except WeatherError as exc:
        return jsonify({"ok": False, "error": f"气象数据获取失败：{exc}"}), 502

    risk = agronomy.assess_risk(crop, bundle, stage)
    ctx = _build_context(profile, crop, stage, bundle, risk)
    rule = ctx["rule"]
    mode = request.json.get("mode") if request.is_json else None

    if mode == "rule" or not llm.available():
        result = rule
        note = None if mode == "rule" else "未配置大模型 API Key，已使用本地规则引擎"
    else:
        try:
            result = llm.generate_irrigation(ctx)
            note = None
        except llm.LLMError as exc:
            return jsonify({
                "ok": False,
                "error": str(exc),
                "fallback": rule,
                "hint": "可点击「改用本地规则引擎」继续演示，或在 .env 中配置 LLM_API_KEY 后重试。",
            }), 502

    db.execute(
        """INSERT INTO irrigation_records
           (user_id, crop_key, created_at, source, decision, net_mm, result)
           VALUES (?,?,?,?,?,?,?)""",
        (current_user()["id"], profile["crop_key"], db.now_text(),
         result.get("source", "rule"), result.get("decision", ""), result.get("net_mm", 0),
         json.dumps(result, ensure_ascii=False)),
    )
    return jsonify({"ok": True, "result": result, "note": note,
                    "weather": {"label": bundle.source_label, "is_demo": bundle.is_demo,
                                "fetched_at": bundle.fetched_at}})


@app.route("/api/irrigation/chat", methods=["POST"])
@login_required
def api_irrigation_chat():
    """围绕刚生成的建议多轮追问。对话历史由前端带上，后端只负责拼上下文。"""
    profile = _profile_or_redirect()
    if _need_location(profile):
        return jsonify({"ok": False, "error": "尚未设置地块位置"}), 400
    if not llm.available():
        return jsonify({
            "ok": False,
            "error": "未配置大模型 API Key，多轮追问需要模型支持。上面的数值仍来自本地规则引擎，可继续使用。",
        }), 400

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"ok": False, "error": "请先输入想问的问题"}), 400
    if len(question) > 300:
        question = question[:300]
    history = payload.get("history")
    if not isinstance(history, list):
        history = []
    decision = payload.get("decision")
    if not isinstance(decision, dict):
        decision = {}

    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    try:
        bundle = _bundle_for(profile)
    except WeatherError as exc:
        return jsonify({"ok": False, "error": f"气象数据获取失败：{exc}"}), 502

    risk = agronomy.assess_risk(crop, bundle, stage)
    ctx = _build_context(profile, crop, stage, bundle, risk)
    try:
        answer = llm.chat(ctx, decision, history, question)
    except llm.LLMError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    if not answer:
        return jsonify({"ok": False, "error": "模型没有返回内容，请重试一次"}), 502
    return jsonify({"ok": True, "answer": answer})


# --------------------------------------------------------------------------- #
# 模块 3：收获窗口与余量调度
# --------------------------------------------------------------------------- #
@app.route("/harvest")
@login_required
def harvest():
    profile = _profile_or_redirect()
    if _need_location(profile):
        flash("先设置地块位置，才能结合预报排收获窗口。", "info")
        return redirect(url_for("settings", first="1"))

    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    error = None
    hw = surplus = risk = bundle = None
    try:
        bundle = _bundle_for(profile)
        risk = agronomy.assess_risk(crop, bundle, stage)
        hw = agronomy.harvest_window(crop, bundle, profile["planting_date"], profile["area_mu"])
        if hw.get("available"):
            surplus = agronomy.surplus_plan(crop, profile["area_mu"], hw, risk)
    except WeatherError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("harvest page failed")
        error = f"数据处理失败：{exc}"

    plans = db.query(
        "SELECT * FROM harvest_plans WHERE user_id = ? ORDER BY id DESC LIMIT 5",
        (current_user()["id"],),
    )
    return render_template("harvest.html", crop=crop, stage=stage, hw=hw, surplus=surplus,
                           risk=risk, bundle=bundle, error=error, plans=plans,
                           active="harvest")


@app.route("/api/harvest/plan", methods=["POST"])
@login_required
def api_harvest_plan():
    profile = _profile_or_redirect()
    if _need_location(profile):
        return jsonify({"ok": False, "error": "尚未设置地块位置"}), 400
    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    try:
        bundle = _bundle_for(profile)
    except WeatherError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    risk = agronomy.assess_risk(crop, bundle, stage)
    hw = agronomy.harvest_window(crop, bundle, profile["planting_date"], profile["area_mu"])
    if not hw.get("available"):
        return jsonify({"ok": False, "error": hw.get("message", "无法评估收获窗口")}), 400
    surplus = agronomy.surplus_plan(crop, profile["area_mu"], hw, risk)

    payload = {"window": hw["window"], "best": hw["best"], "surplus": surplus,
               "crop": crop["name"], "area_mu": profile["area_mu"]}
    pid = db.execute(
        """INSERT INTO harvest_plans
           (user_id, crop_key, created_at, window_start, window_end, loss_saved_kg, result)
           VALUES (?,?,?,?,?,?,?)""",
        (current_user()["id"], profile["crop_key"], db.now_text(),
         hw["window"]["start"], hw["window"]["end"], surplus["saved_kg"],
         json.dumps(payload, ensure_ascii=False)),
    )
    return jsonify({"ok": True, "id": pid,
                    "window": hw["window"], "saved_kg": surplus["saved_kg"]})


# --------------------------------------------------------------------------- #
# 错误页
# --------------------------------------------------------------------------- #
@app.errorhandler(404)
def not_found(_e):
    return render_template("error.html", code=404,
                           message="页面不存在，回到看板继续。"), 404


@app.errorhandler(500)
def server_error(_e):
    return render_template("error.html", code=500,
                           message="服务器内部错误，检查终端日志。"), 500


if __name__ == "__main__":
    db.init_db(app)
    debug = (os.getenv("FLASK_DEBUG", "0").strip() == "1")
    print("\n  根脉 RootSignal 已启动 →  http://127.0.0.1:5000\n"
          f"  气象数据源：{weather_status()['label']}    大模型："
          f"{'已配置 ' + llm_settings()['model'] if llm.available() else '未配置（规则引擎兜底）'}\n")
    app.run(host="127.0.0.1", port=5000, debug=debug)
