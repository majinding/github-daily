#!/usr/bin/env python3
"""把最新一期数据里每个项目的英文 description 翻译成中文（Agnes 3.0 Flash）。

流程：读取 data/ 下最新一期的 JSON -> 批量调用 Agnes Chat Completions API
翻译英文描述 -> 把译文写回 description_zh 字段。

- 批量翻译：多条描述编号后一次请求返回，大幅减少请求次数、降低限流概率
- 兜底降级：批量失败或某条漏翻时，自动对该条逐条补翻
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
BATCH_SIZE = max(1, int(os.environ.get("AGNES_BATCH_SIZE", "10")))

SYSTEM_PROMPT = (
    "你是专业的 GitHub 项目介绍翻译。把用户提供的英文仓库描述翻译成中文，"
    "要求：忠实原意、简洁自然、保留专有名词（项目名、技术名、工具名）不翻译。"
    "只输出中文译文本身，不要解释、不要引号、不要多余内容。"
)

BATCH_SYSTEM_PROMPT = (
    "你是专业的 GitHub 项目介绍翻译。用户会发来一组带编号的英文 GitHub 仓库描述。"
    "请逐条翻译成中文：忠实原意、简洁自然、保留专有名词（项目名、技术名、工具名）不翻译。"
    "严格按输入编号逐条输出，格式为「编号. 译文」，编号与输入一一对应，"
    "一条译文可以换行续写但必须归属同一编号，不要遗漏、不要合并、不要输出任何额外内容。"
)


def find_latest() -> Path:
    files = sorted(
        p for p in DATA_DIR.glob("20*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name)
    )
    if not files:
        sys.exit("data/ 目录下没有报告数据，请先运行 fetch_trending.py")
    return files[-1]


def parse_batch(content: str) -> dict[int, str]:
    """解析批量响应：按「编号.」切分，支持译文内部折行。返回 {编号: 译文}。"""
    pattern = re.compile(r"(?:^|\n)\s*(\d+)\s*[.、)]\s*")
    matches = list(pattern.finditer(content))
    result: dict[int, str] = {}
    for i, m in enumerate(matches):
        idx = int(m.group(1))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        text = content[start:end]
        text = re.sub(r"\s+", " ", text).strip().strip('"').strip("'")
        if text:
            result[idx] = text
    return result


def post_chat(messages: list[dict], max_tokens: int, timeout: int) -> requests.Response:
    return requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )


def translate_batch(texts: list[str], max_retries: int = 3) -> dict[int, str]:
    """批量翻译多条描述，一次请求完成。返回 {序号(从1开始): 译文}，失败返回 {}。"""
    if not texts or not API_KEY:
        return {}
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
    for attempt in range(max_retries):
        try:
            resp = post_chat(
                [
                    {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                    {"role": "user", "content": numbered},
                ],
                max_tokens=8192,
                timeout=120,
            )
            if resp.status_code == 429:
                wait = 3 * (2 ** attempt)
                print(f"  遇到速率限制，{wait}s 后重试（{attempt + 1}/{max_retries}）", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            choice = resp.json()["choices"][0]
            content = choice.get("message", {}).get("content") or ""
            if choice.get("finish_reason") == "length":
                print("  批量输出被截断，缺失条目将逐条补翻", file=sys.stderr)
            parsed = parse_batch(content)
            if not parsed:
                print("  批量响应无法解析，将逐条补翻", file=sys.stderr)
            return parsed
        except Exception as exc:  # noqa: BLE001 - 翻译失败不应中断整个报告
            if attempt < max_retries - 1:
                time.sleep(3)
                continue
            print(f"  批量翻译失败：{exc}，将逐条补翻", file=sys.stderr)
            return {}
    return {}


def translate(text: str, max_retries: int = 4) -> str:
    """单条翻译（兜底路径用）。"""
    if not text or not text.strip():
        return ""
    if not API_KEY:
        return ""
    for attempt in range(max_retries):
        try:
            resp = post_chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                max_tokens=256,
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
    # 收集待翻译项（已有译文或描述为空的跳过）
    pending = [
        (i, r)
        for i, r in enumerate(repos)
        if not r.get("description_zh") and (r.get("description") or "").strip()
    ]
    done = len(repos) - len(pending)
    print(f"翻译 {len(repos)} 个项目（模型 {MODEL}，每批最多 {BATCH_SIZE} 条）：待翻译 {len(pending)} 条")
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        batch_no = start // BATCH_SIZE + 1
        texts = [r.get("description") or "" for _, r in batch]
        print(f"  第 {batch_no} 批：一次请求翻译 {len(batch)} 条…")
        result = translate_batch(texts)
        for offset, (i, r) in enumerate(batch, 1):
            zh = (result.get(offset) or "").strip()
            if not zh:
                # 兜底：该条未在批量结果中，逐条补翻
                print(f"    第 {batch_no} 批第 {offset} 条缺失，逐条补翻…", file=sys.stderr)
                zh = translate(texts[offset - 1])
                if zh:
                    time.sleep(1.0)
            if zh:
                r["description_zh"] = zh
                done += 1
        if start + BATCH_SIZE < len(pending):
            time.sleep(1.0)  # 轻量限速，避免触发速率限制
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"翻译完成：成功 {done}/{len(repos)}，结果已写回 {path.name}")


if __name__ == "__main__":
    main()
