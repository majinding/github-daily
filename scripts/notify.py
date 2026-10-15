#!/usr/bin/env python3
"""把每日报告摘要推送到企业微信 / 钉钉群机器人。

环境变量:
  WECOM_WEBHOOK    企业微信群机器人 Webhook 完整地址
  DINGTALK_WEBHOOK 钉钉自定义机器人 Webhook 完整地址
  REPORT_URL       报告访问地址（缺省用 DEFAULT_REPORT_URL）

两个 Webhook 均未配置时打印提示并正常退出（不阻塞工作流）。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

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


def send_wecom(webhook: str, text: str) -> bool:
    resp = requests.post(
        webhook,
        json={"msgtype": "markdown", "markdown": {"content": text}},
        timeout=TIMEOUT,
    )
    ok = resp.status_code == 200 and resp.json().get("errcode") == 0
    print(f"企业微信推送: {'成功' if ok else '失败 -> ' + resp.text[:200]}")
    return ok


def send_dingtalk(webhook: str, title: str, text: str) -> bool:
    resp = requests.post(
        webhook,
        json={"msgtype": "markdown", "markdown": {"title": title, "text": text}},
        timeout=TIMEOUT,
    )
    ok = resp.status_code == 200 and resp.json().get("errcode") == 0
    print(f"钉钉推送: {'成功' if ok else '失败 -> ' + resp.text[:200]}")
    return ok


def main() -> int:
    wecom = os.environ.get("WECOM_WEBHOOK", "").strip()
    dingtalk = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    report_url = os.environ.get("REPORT_URL", "").strip() or DEFAULT_REPORT_URL

    if not wecom and not dingtalk:
        print("未配置 WECOM_WEBHOOK / DINGTALK_WEBHOOK，跳过推送")
        return 0

    data = load_latest()
    title, text = build_markdown(data, report_url)
    print(f"报告地址: {report_url}")

    results = []
    if wecom:
        results.append(send_wecom(wecom, text))
    if dingtalk:
        results.append(send_dingtalk(dingtalk, title, text))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
