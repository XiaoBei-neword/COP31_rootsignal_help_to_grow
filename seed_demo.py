"""生成两个演示账号，用于现场演示与"数据隔离"验证。

用法：python seed_demo.py                # 建/重置演示账号与预置记录
      python seed_demo.py --if-missing   # 仅在账号缺失时补建（start.bat 用，幂等）
账号：demo_a / demo1234     demo_b / demo1234
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import db
from app import app
from services import agronomy
from services.weather import WeatherError, fetch_weather

USERS = [
    {
        "username": "demo_a",
        "password": "demo1234",
        "profile": {
            "crop_key": "maize",
            "location_name": "李庄北地",
            "latitude": 33.6250,
            "longitude": 114.6969,
            "area_mu": 8.0,
            "planting_date": (date.today() - timedelta(days=92)).isoformat(),
            "soil": "loam",
            "irrigation_method": "sprinkler",
        },
    },
    {
        "username": "demo_b",
        "password": "demo1234",
        "profile": {
            "crop_key": "rice",
            "location_name": "东湾三号田",
            "latitude": 30.3350,
            "longitude": 112.2390,
            "area_mu": 12.5,
            "planting_date": (date.today() - timedelta(days=108)).isoformat(),
            "soil": "clay",
            "irrigation_method": "flood",
        },
    },
]


def upsert_user(spec) -> int:
    row = db.query_one("SELECT id FROM users WHERE username = ?", (spec["username"],))
    if row:
        uid = row["id"]
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(spec["password"]), uid))
        print(f"  · 账号已存在，重置密码：{spec['username']}")
    else:
        uid = db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (spec["username"], generate_password_hash(spec["password"]), db.now_text()),
        )
        print(f"  · 新建账号：{spec['username']}")
    db.ensure_profile(uid)
    db.save_profile(uid, **spec["profile"])
    return uid


def seed_records(uid: int) -> None:
    """给 demo_a 预置记录，方便演示"我的记录"和数据隔离。已有记录时不重复写入。"""
    exist = db.query_one(
        "SELECT COUNT(*) AS n FROM irrigation_records WHERE user_id = ?", (uid,)
    )
    if exist and exist["n"]:
        print("  · 已存在决策记录，跳过预置")
        return

    profile = db.ensure_profile(uid)
    crop = agronomy.get_crop(profile["crop_key"])
    stage = agronomy.growth_stage(crop, profile["planting_date"])
    try:
        bundle = fetch_weather(profile["latitude"], profile["longitude"], profile["location_name"])
        risk = agronomy.assess_risk(crop, bundle, stage)
        rule = agronomy.irrigation_plan(crop, stage, risk, profile["soil"],
                                        profile["irrigation_method"], profile["area_mu"])
    except WeatherError as exc:
        print(f"  · 跳过预置记录（取数失败：{exc}）")
        return

    db.execute(
        """INSERT INTO irrigation_records
           (user_id, crop_key, created_at, source, decision, net_mm, result)
           VALUES (?,?,?,?,?,?,?)""",
        (uid, profile["crop_key"], db.now_text(), "rule",
         rule["decision"], rule["net_mm"], json.dumps(rule, ensure_ascii=False)),
    )

    hw = agronomy.harvest_window(crop, bundle, profile["planting_date"], profile["area_mu"])
    if hw.get("available"):
        surplus = agronomy.surplus_plan(crop, profile["area_mu"], hw, risk)
        db.execute(
            """INSERT INTO harvest_plans
               (user_id, crop_key, created_at, window_start, window_end, loss_saved_kg, result)
               VALUES (?,?,?,?,?,?,?)""",
            (uid, profile["crop_key"], db.now_text(), hw["window"]["start"], hw["window"]["end"],
             surplus["saved_kg"],
             json.dumps({"window": hw["window"], "surplus": surplus}, ensure_ascii=False)),
        )
    print(f"  · 已写入 1 条灌溉记录" + ("、1 条收获方案" if hw.get("available") else ""))


def all_ready() -> bool:
    """两个演示账号都存在且地块坐标已填好 → 不必再初始化。"""
    for spec in USERS:
        row = db.query_one(
            """SELECT u.id FROM users u JOIN farm_profiles p ON p.user_id = u.id
               WHERE u.username = ? AND p.latitude IS NOT NULL""",
            (spec["username"],),
        )
        if row is None:
            return False
    return True


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    only_if_missing = "--if-missing" in args

    with app.app_context():
        db.init_db(app)
        if only_if_missing and all_ready():
            print("  · 演示账号已就绪，跳过初始化")
            return 0

        print("初始化演示数据…")
        for spec in USERS:
            uid = upsert_user(spec)
            if spec["username"] == "demo_a":
                seed_records(uid)
        print("\n完成。登录账号：")
        for spec in USERS:
            p = spec["profile"]
            print(f"  {spec['username']} / {spec['password']}   "
                  f"{agronomy.get_crop(p['crop_key'])['name']} · {p['location_name']} · {p['area_mu']} 亩")
        print("\n用两个账号分别登录，互相看不到对方的记录——这就是数据隔离。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
