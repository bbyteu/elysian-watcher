import os
import sys
import json
import smtplib
import requests

from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.header import Header


INVITE_CODE = "elysianhorizon"

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
GMAIL_TO = os.environ.get("GMAIL_TO", "")

STATE_FILE = "state.json"

REMIND_AFTER_MINUTES = 30
HEARTBEAT_INTERVAL_HOURS = 24


def get_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except (ValueError, TypeError):
        return None


def load_state():
    default_state = {
        "status": "paused",
        "open_since": None,
        "notified_open": False,
        "last_remind_at": None,
        "last_heartbeat": None
    }

    if not os.path.exists(STATE_FILE):
        return default_state

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            saved_state = json.load(file)

        default_state.update(saved_state)
        return default_state

    except (json.JSONDecodeError, OSError):
        print("[警告] state.json 读取失败，使用默认状态。")
        return default_state


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=4)

    print("[状态] 已保存到 state.json")


def send_notification(subject, body):
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD or not GMAIL_TO:
        print("[邮件错误] Gmail 环境变量未配置完整。")
        return False

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = GMAIL_ADDRESS
    message["To"] = GMAIL_TO

    try:
        print(f"[邮件] 正在发送：{subject}")

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(
                GMAIL_ADDRESS,
                [GMAIL_TO],
                message.as_string()
            )

        print(f"[邮件] 发送成功：{subject}")
        return True

    except Exception as error:
        print(f"[邮件错误] 发送失败：{error}")
        return False


def check_invite_status():
    url = f"https://discord.com/api/v10/invites/{INVITE_CODE}"

    try:
        response = requests.get(url, timeout=20)

        print(f"[查询] Discord HTTP 状态码：{response.status_code}")

        if response.status_code != 200:
            print("[查询] 邀请暂时无法访问。")
            return "paused"

        data = response.json()
        guild = data.get("guild", {})
        features = guild.get("features", [])

        if "INVITES_DISABLED" in features:
            print("[查询] 检测到 INVITES_DISABLED，邀请已暂停。")
            return "paused"

        print("[查询] 邀请当前可访问。")
        return "open"

    except Exception as error:
        print(f"[查询错误] {error}")
        return "paused"


def main():
    # 测试模式：只发送一封测试邮件，不执行监控
    if os.environ.get("TEST_EMAIL", "").lower() == "true":
        print("[测试] 开始测试 Gmail 邮件发送。")

        success = send_notification(
            "🧪 Elysian Horizon 邮件测试成功",
            (
                "这是一封 Gmail 测试邮件。\n\n"
                "如果你收到了这封邮件，说明：\n"
                "1. GitHub Secrets 配置成功。\n"
                "2. Gmail SMTP 登录成功。\n"
                "3. 邮件发送功能正常。\n\n"
                "Discord 邀请监控程序已经准备就绪。"
            )
        )

        if not success:
            print("[测试失败] Gmail 邮件发送失败。")
            sys.exit(1)

        print("[测试成功] 邮件已提交给 Gmail SMTP。")
        return

    now = get_now()
    state = load_state()

    print(f"[状态] 上一次状态：{state['status']}")

    current_status = check_invite_status()

    print(f"[状态] 当前邀请状态：{current_status}")

    if current_status == "open":

        if state["status"] != "open":
            print("[判断] 检测到邀请从暂停变为开放。")

            first_sent = send_notification(
                "🚨 Elysian Horizon 开放了！",
                (
                    "Discord 邀请现在已经开放！\n\n"
                    f"邀请链接：https://discord.gg/{INVITE_CODE}\n\n"
                    "请尽快查看并加入。"
                )
            )

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
        if state["status"] != "paused":
            print("[判断] 邀请从开放变为暂停，重置开放记录。")
        else:
            print("[判断] 邀请仍然暂停。")

        state["status"] = "paused"
        state["open_since"] = None
        state["notified_open"] = False
        state["last_remind_at"] = None

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

    save_state(state)

    print("[完成] 本轮检查结束。")


if __name__ == "__main__":
    main()
