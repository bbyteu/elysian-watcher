#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Elysian Horizon Discord 邀请监控
使用 Gmail 发送邮件通知。
"""

import json
import os
import sys
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses

import requests


# ============================================================
# 配置
# ============================================================

# Discord 邀请码
INVITE_CODE = os.environ.get("INVITE_CODE", "elysianhorizon")

# Gmail 配置：从 GitHub Secrets 读取
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO = os.environ.get("GMAIL_TO", "")

# 状态文件
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# 开放状态下，每隔多少分钟重复提醒
REMIND_AFTER_MINUTES = int(
    os.environ.get("REMIND_AFTER_MINUTES", "30")
)

# 每隔多少小时发送一次监控心跳，0 表示关闭
HEARTBEAT_INTERVAL_HOURS = float(
    os.environ.get("HEARTBEAT_INTERVAL_HOURS", "24")
)

# Discord API
DISCORD_API_URL = (
    f"https://discord.com/api/v10/invites/"
    f"{INVITE_CODE}?with_counts=true"
)

REQUEST_HEADERS = {
    "User-Agent": "ElysianWatcher/1.0"
}


# ============================================================
# 时间处理
# ============================================================

def get_now():
    """获取当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def parse_time(value):
    """读取 ISO 格式时间。"""
    if not value:
        return None

    result = datetime.fromisoformat(value)

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result


# ============================================================
# 状态文件读写
# ============================================================

def load_state():
    """读取上一次监控状态。"""

    default_state = {
        "status": "paused",
        "open_since": None,
        "notified_open": False,
        "last_remind_at": None,
        "last_heartbeat": None,
    }

    if not os.path.exists(STATE_FILE):
        print("[状态] 尚无历史状态，使用初始状态。")
        return default_state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        for key, value in default_state.items():
            state.setdefault(key, value)

        return state

    except (json.JSONDecodeError, OSError) as error:
        print(f"[警告] 读取状态文件失败：{error}")
        return default_state


def save_state(state):
    """保存最新监控状态。"""

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(f"[状态] 已保存到 {STATE_FILE}")


# ============================================================
# Discord 邀请状态检测
# ============================================================

def check_invite_status():
    """
    返回：
    open   = 邀请开放
    paused = 邀请暂停或失效

    Discord 暂停邀请时，接口可能仍返回 HTTP 200。
    因此需要检查 guild.features 中是否存在 INVITES_DISABLED。
    """

    try:
        response = requests.get(
            DISCORD_API_URL,
            headers=REQUEST_HEADERS,
            timeout=15
        )

    except requests.RequestException as error:
        print(f"[警告] 请求 Discord API 失败：{error}")
        return "paused"

    print(f"[查询] Discord HTTP 状态码：{response.status_code}")

    if response.status_code != 200:
        print("[查询] 邀请不可用或已失效，判定为暂停。")
        return "paused"

    try:
        data = response.json()

    except ValueError:
        print("[警告] Discord 返回的内容不是合法 JSON。")
        return "paused"

    guild = data.get("guild")

    if data.get("code") != INVITE_CODE or not isinstance(guild, dict):
        print("[查询] 邀请数据异常，判定为暂停。")
        return "paused"

    features = guild.get("features", [])

    if "INVITES_DISABLED" in features:
        print("[查询] 检测到 INVITES_DISABLED，邀请已暂停。")
        return "paused"

    print("[查询] 邀请有效且未暂停，当前为开放状态。")
    return "open"


# ============================================================
# Gmail 邮件通知
# ============================================================

def send_notification(title, content=""):
    """
    使用 Gmail SMTP 发送邮件。

    GitHub Secrets 需要提供：
    GMAIL_ADDRESS
    GMAIL_APP_PASSWORD
    GMAIL_TO
    """

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not GMAIL_TO:
        print(
            "[错误] Gmail 配置不完整。"
            "请检查 GMAIL_ADDRESS、GMAIL_APP_PASSWORD、GMAIL_TO。"
        )
        return False

    # 支持多个收件人，使用逗号或分号分隔
    recipients = [
        address.strip()
        for address in GMAIL_TO.replace(";", ",").split(",")
        if address.strip()
    ]

    if not recipients:
        print("[错误] 没有设置有效的收件人邮箱。")
        return False

    message = EmailMessage()
    message["Subject"] = title
    message["From"] = GMAIL_ADDRESS
    message["To"] = ", ".join(recipients)
    message.set_content(content or title)

    # Gmail App Password 通常显示为带空格的字符组，
    # 这里自动去掉空格再登录。
    app_password = GMAIL_APP_PASSWORD.replace(" ", "")

    try:
        print(f"[邮件] 正在发送：{title}")

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465,
            timeout=30
        ) as server:

            server.login(
                GMAIL_ADDRESS,
                app_password
            )

            refused = server.send_message(message)

            if refused:
                print(f"[邮件] 部分收件人被拒绝：{refused}")
                return False

        print(f"[邮件] 发送成功：{title}")
        return True

    except (smtplib.SMTPException, OSError) as error:
        print(f"[邮件] 发送失败：{type(error).__name__}: {error}")
        return False


# ============================================================
# 主监控流程
# ============================================================

def main():

    now = get_now()

    # 1. 读取旧状态
    state = load_state()
    print(f"[状态] 上一次状态：{state['status']}")

    # 2. 查询 Discord
    current_status = check_invite_status()

    print(f"[状态] 当前邀请状态：{current_status}")

    # 3. 根据状态变化发送通知
    if current_status == "open":

        if state["status"] != "open":

            # 暂停 -> 开放
            print("[判断] 检测到邀请从暂停变为开放。")

            first_sent = send_notification(
                "🚨 Elysian Horizon 开放了！",
                (
                    "Discord 邀请现在已经开放！\n\n"
                    f"邀请链接：https://discord.gg/{INVITE_CODE}\n\n"
                    "请尽快查看并加入。"
                )
            )

            # 单独发送邀请链接，方便在邮件中复制
            if first_sent:
                send_notification(
                    f"https://discord.gg/{INVITE_CODE}",
                    f"Discord 邀请链接：\nhttps://discord.gg/{INVITE_CODE}"
                )

            state["status"] = "open"
            state["open_since"] = now.isoformat()
            state["notified_open"] = first_sent
            state["last_remind_at"] = now.isoformat()

        else:

            # 邀请持续开放
            last_remind_at = (
                state.get("last_remind_at")
                or state.get("open_since")
            )

            last_remind = parse_time(last_remind_at)

            if last_remind:
                elapsed_minutes = (
                    now - last_remind
                ).total_seconds() / 60

                print(
                    f"[判断] 距离上次开放提醒："
                    f"{elapsed_minutes:.1f} 分钟。"
                )

                if elapsed_minutes >= REMIND_AFTER_MINUTES:

                    print("[判断] 到达重复提醒时间。")

                    sent = send_notification(
                        "⚠️ Elysian Horizon 仍然开放中",
                        (
                            "Discord 邀请目前仍处于开放状态。\n\n"
                            f"邀请链接：https://discord.gg/{INVITE_CODE}\n\n"
                            f"这是每 {REMIND_AFTER_MINUTES} 分钟一次的重复提醒。"
                        )
                    )

                    if sent:
                        state["last_remind_at"] = now.isoformat()

    else:

        # 当前暂停
        if state["status"] != "paused":
            print("[判断] 邀请从开放变为暂停，重置开放记录。")
        else:
            print("[判断] 邀请仍然暂停。")

        state["status"] = "paused"
        state["open_since"] = None
        state["notified_open"] = False
        state["last_remind_at"] = None

    # 4. 每 24 小时发送一次心跳
    if HEARTBEAT_INTERVAL_HOURS > 0:

        last_heartbeat = parse_time(
            state.get("last_heartbeat")
        )

        if last_heartbeat is None:
            elapsed_hours = HEARTBEAT_INTERVAL_HOURS
        else:
            elapsed_hours = (
                now - last_heartbeat
            ).total_seconds() / 3600

        if elapsed_hours >= HEARTBEAT_INTERVAL_HOURS:

            status_cn = (
                "开放中 ✅"
                if state["status"] == "open"
                else "暂停中 ⏸️"
            )

            print("[心跳] 发送监控运行状态邮件。")

            sent = send_notification(
                "✅ Elysian 监控正常运行中",
                (
                    "Elysian Horizon 监控脚本运行正常。\n\n"
                    f"当前邀请状态：{status_cn}\n\n"
                    f"监控时间（UTC）：{now.isoformat()}\n\n"
                    f"这是每 {HEARTBEAT_INTERVAL_HOURS:g} 小时发送一次的例行心跳。"
                )
            )

            if sent:
                state["last_heartbeat"] = now.isoformat()

    # 5. 保存状态
    save_state(state)

    print("[完成] 本轮检查结束。")


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as error:
        print(f"[致命错误] 程序运行失败：{error}")
        sys.exit(1)
