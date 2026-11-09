#!/usr/bin/env python3
"""把每日报告摘要推送到微信插件（企微兼容）/ 钉钉 / 飞书群机器人。

环境变量:
  WECOM_WEBHOOK    微信插件 / 企微兼容 Webhook 完整地址
  WECOM_MSG_TYPE   企微通道消息格式：text（默认，纯文本）或 markdown
  DINGTALK_WEBHOOK 钉钉自定义机器人 Webhook 完整地址
  DINGTALK_SECRET  钉钉机器人加签密钥（安全设置选择「加签」时必填，可选）
  FEISHU_WEBHOOK   飞书自定义机器人 Webhook 完整地址
  FEISHU_SECRET    飞书机器人签名密钥（启用「签名校验」时必填，可选）
  REPORT_URL       报告访问地址（缺省用 DEFAULT_REPORT_URL）

三个 Webhook 均未配置时打印提示并正常退出（不阻塞工作流）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_REPORT_URL = "https://majinding.github.io/github-daily/"
TOP_N = 10
TIMEOUT = 15


def load_latest() -> dict:
    files = sorted(
        p for p in DATA_DIR.glob("20*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name)
    )
    if not files:
        sys.exit("data/ 目录下没有报告数据，请先运行 fetch_trending.py")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def build_markdown(data: dict, report_url: str) -> tuple[str, str]:
    """返回 (标题, Markdown 正文)。"""
    repos = data.get("repos", [])
    total_today = sum(r.get("stars_today") or 0 for r in repos)
    title = f"GitHub 每日热榜 · {data['date']}"

    lines = [
        f"**GitHub 每日热榜 · {data['date']}**",
        "",
        f"今日上榜 **{len(repos)}** 个项目，合计新增 Star **{fmt(total_today)}**",
        "",
    ]
    for r in repos[:TOP_N]:
        desc = (r.get("description") or "").strip() or "（无描述）"
        if len(desc) > 60:
            desc = desc[:60].rsplit(" ", 1)[0] + " ..."
        today = f" (+{fmt(r['stars_today'])} today)" if r.get("stars_today") else ""
        lines.append(f"**{r['rank']}.** [{r['full_name']}]({r['url']})")
        lines.append(f"　★ {fmt(r['stars'])}{today} · {desc}")
    if len(repos) > TOP_N:
        lines.append("")
        lines.append(f"... 其余 {len(repos) - TOP_N} 个项目见完整报告")
    lines += ["", f"> [查看完整报告]({report_url})"]
    return title, "\n".join(lines)


def to_plain_text(text: str) -> str:
    """把 Markdown 摘要降级为纯文本（微信插件不支持 Markdown）。"""
    lines = []
    for line in text.splitlines():
        if line.startswith("> "):
            line = re.sub(r"^>\s*\[(.+?)\]\((.+?)\)$", r"\1: \2", line)
        else:
            line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        lines.append(re.sub(r"\*\*(.+?)\*\*", r"\1", line))
    return "\n".join(lines)


def send_wecom(webhook: str, text: str, msg_type: str = "text") -> bool:
    if msg_type == "markdown":
        payload = {"msgtype": "markdown", "markdown": {"content": text}}
    else:
        payload = {"msgtype": "text", "text": {"content": to_plain_text(text)}}
    resp = requests.post(webhook, json=payload, timeout=TIMEOUT)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    ok = resp.status_code == 200 and (
        body.get("errcode") == 0
        or body.get("code") == 0
        or body.get("StatusCode") == 0
        or not body
    )
    print(f"微信插件推送: {'成功' if ok else '失败 -> ' + resp.text[:200]}")
    return ok


def dingtalk_sign(secret: str) -> tuple[str, str]:
    """生成钉钉机器人「加签」所需的 timestamp（毫秒）/ sign。

    与飞书不同：钉钉以 secret 为 HMAC key，待签内容为「timestamp + 换行 + secret」，
    且 sign 需 URL 编码后拼进 Webhook 查询参数。
    """
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return timestamp, quote_plus(base64.b64encode(digest).decode("utf-8"))


def send_dingtalk(webhook: str, title: str, text: str, secret: str = "") -> bool:
    url = webhook
    if secret:
        timestamp, sign = dingtalk_sign(secret)
        sep = "&" if "?" in webhook else "?"
        url = f"{webhook}{sep}timestamp={timestamp}&sign={sign}"
    resp = requests.post(
        url,
        json={"msgtype": "markdown", "markdown": {"title": title, "text": text}},
        timeout=TIMEOUT,
    )
    ok = resp.status_code == 200 and resp.json().get("errcode") == 0
    print(f"钉钉推送: {'成功' if ok else '失败 -> ' + resp.text[:200]}")
    return ok


def to_lark_md(text: str) -> str:
    """飞书 lark_md 不支持 Markdown 引用块，去掉行首的 '> '。"""
    return "\n".join(line[2:] if line.startswith("> ") else line for line in text.splitlines())


def feishu_sign(secret: str) -> tuple[str, str]:
    """生成飞书机器人「签名校验」所需的 timestamp / sign。"""
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return timestamp, base64.b64encode(digest).decode("utf-8")


def send_feishu(webhook: str, title: str, text: str, secret: str = "") -> bool:
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": to_lark_md(text)}}],
        },
    }
    if secret:
        payload["timestamp"], payload["sign"] = feishu_sign(secret)
    resp = requests.post(webhook, json=payload, timeout=TIMEOUT)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    ok = resp.status_code == 200 and (body.get("code") == 0 or body.get("StatusCode") == 0)
    print(f"飞书推送: {'成功' if ok else '失败 -> ' + resp.text[:200]}")
    return ok


def main() -> int:
    wecom = os.environ.get("WECOM_WEBHOOK", "").strip()
    wecom_msg_type = os.environ.get("WECOM_MSG_TYPE", "text").strip().lower()
    dingtalk = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    dingtalk_secret = os.environ.get("DINGTALK_SECRET", "").strip()
    feishu = os.environ.get("FEISHU_WEBHOOK", "").strip()
    feishu_secret = os.environ.get("FEISHU_SECRET", "").strip()
    report_url = os.environ.get("REPORT_URL", "").strip() or DEFAULT_REPORT_URL

    if not wecom and not dingtalk and not feishu:
        print("未配置 WECOM_WEBHOOK / DINGTALK_WEBHOOK / FEISHU_WEBHOOK，跳过推送")
        return 0

    data = load_latest()
    title, text = build_markdown(data, report_url)
    print(f"报告地址: {report_url}")

    results = []
    if wecom:
        results.append(send_wecom(wecom, text, wecom_msg_type))
    if dingtalk:
        results.append(send_dingtalk(dingtalk, title, text, dingtalk_secret))
    if feishu:
        results.append(send_feishu(feishu, title, text, feishu_secret))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
