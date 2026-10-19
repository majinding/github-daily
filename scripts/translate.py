#!/usr/bin/env python3
"""把最新一期数据里每个项目的英文 description 翻译成中文（Agnes 3.0 Flash）。

流程：读取 data/ 下最新一期的 JSON -> 逐条调用 Agnes Chat Completions API
翻译英文描述 -> 把译文写回 description_zh 字段。

- API Key 从环境变量 AGNES_API_KEY 读取（GitHub Actions 通过 Secret 注入，明文不入库）
- 未配置 key、请求失败、或描述为空的条目自动跳过，保留英文原文
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
API_URL = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1/chat/completions").rstrip("/")
MODEL = os.environ.get("AGNES_MODEL", "agnes-3.0-flash")
API_KEY = (os.environ.get("AGNES_API_KEY") or "").strip()

SYSTEM_PROMPT = (
    "你是专业的 GitHub 项目介绍翻译。把用户提供的英文仓库描述翻译成中文，"
    "要求：忠实原意、简洁自然、保留专有名词（项目名、技术名、工具名）不翻译。"
    "只输出中文译文本身，不要解释、不要引号、不要多余内容。"
)


def find_latest() -> Path:
    files = sorted(
        p for p in DATA_DIR.glob("20*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name)
    )
    if not files:
        sys.exit("data/ 目录下没有报告数据，请先运行 fetch_trending.py")
    return files[-1]


def translate(text: str, max_retries: int = 4) -> str:
    if not text or not text.strip():
        return ""
    if not API_KEY:
        return ""
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = 3 * (2 ** attempt)
                print(f"  遇到速率限制，{wait}s 后重试（{attempt + 1}/{max_retries}）", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content.strip().strip('"').strip("'")
        except Exception as exc:  # noqa: BLE001 - 翻译失败不应中断整个报告
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            print(f"  翻译失败（跳过该项）：{exc}", file=sys.stderr)
            return ""
    return ""


def main() -> None:
    path = find_latest()
    data = json.loads(path.read_text(encoding="utf-8"))
    repos = data.get("repos", [])
    if not API_KEY:
        print("未检测到 AGNES_API_KEY，跳过翻译（保留英文原文）")
        return
    print(f"翻译 {len(repos)} 个项目的描述（模型 {MODEL}）…")
    ok = 0
    for r in repos:
        desc = r.get("description") or ""
        if r.get("description_zh") or not desc.strip():
            ok += 1
            continue
        zh = translate(desc)
        if zh:
            r["description_zh"] = zh
            ok += 1
        time.sleep(1.0)  # 轻量限速，避免触发速率限制
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"翻译完成：成功 {ok}/{len(repos)}，结果已写回 {path.name}")


if __name__ == "__main__":
    main()
