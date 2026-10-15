#!/usr/bin/env python3
"""抓取 GitHub Trending 每日热榜，并用 GitHub API 补充项目详情。

数据来源:
  1. https://github.com/trending?since=daily  —— 榜单排名与 "stars today"
  2. GitHub REST API /repos/{owner}/{repo}    —— 精确 star 数、topics 等
  3. GitHub REST API README                   —— 描述为空时的兜底

输出: data/YYYY-MM-DD.json（按北京时间计日）与 data/latest.json
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TRENDING_URL = "https://github.com/trending?since=daily"
API_BASE = "https://api.github.com"
MAX_WORKERS = 5

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
PAGE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# GitHub linguist 常见语言主题色，未知语言回退灰色
LANGUAGE_COLORS = {
    "python": "#3572A5", "javascript": "#f1e05a", "typescript": "#3178c6",
    "go": "#00ADD8", "rust": "#dea584", "c": "#555555", "c++": "#f34b7d",
    "java": "#b07219", "kotlin": "#A97BFF", "ruby": "#701516",
    "php": "#4F5D95", "swift": "#F05138", "shell": "#89e051",
    "html": "#e34c26", "css": "#563d7c", "vue": "#41b883", "scala": "#c22d40",
    "dart": "#00B4AB", "lua": "#000080", "perl": "#0298c3", "haskell": "#5e5086",
    "elixir": "#6e4a7e", "erlang": "#B83998", "clojure": "#db5855",
    "julia": "#a270ba", "r": "#198CE7", "matlab": "#e16737",
    "objective-c": "#438eff", "assembly": "#6E4C13", "jupyter notebook": "#DA5B0B",
    "markdown": "#083fa1", "tex": "#3D6117", "makefile": "#427819",
    "dockerfile": "#384d54", "powershell": "#012456", "zig": "#ec915c",
    "nim": "#ffc200", "crystal": "#000100", "ocaml": "#3be133",
    "f#": "#b845fc", "c#": "#178600", "groovy": "#4298b8", "solidity": "#AA6746",
}


def beijing_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def parse_count(text: str | None) -> int | None:
    """把 '12.3k' / '1,234' 这类计数解析成整数。"""
    if not text:
        return None
    t = text.strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([kKmM]?)", t)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return int(num)


def api_headers(token: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_trending_page(session: requests.Session) -> list[dict]:
    """解析 Trending 页面，返回基础榜单信息。"""
    resp = session.get(TRENDING_URL, headers=PAGE_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    rows = soup.select("article.Box-row")
    if not rows:
        raise RuntimeError("Trending 页面解析失败：未找到 article.Box-row（页面结构可能已变化）")

    items: list[dict] = []
    for row in rows:
        link = row.select_one("h2 a")
        if not link:
            continue
        href = (link.get("href") or "").strip("/")
        if href.count("/") != 1:
            continue
        owner, name = href.split("/")

        desc_el = row.select_one("p")
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        lang_el = row.select_one('[itemprop="programmingLanguage"]')
        language = lang_el.get_text(strip=True) if lang_el else ""

        stars = forks = None
        for a in row.select("a"):
            a_href = a.get("href") or ""
            if a_href.endswith("/stargazers"):
                stars = parse_count(a.get_text())
            elif a_href.endswith("/forks") or "/network/members" in a_href:
                forks = parse_count(a.get_text())

        stars_today = None
        for span in row.select("span"):
            txt = span.get_text(" ", strip=True)
            m = re.search(r"([\d.,]+|[kK mM\d.]+)\s+stars?\s+today", txt)
            if m:
                stars_today = parse_count(m.group(1))
                break

        built_by = len({a.get("href") for a in row.select('a[data-hovercard-type="user"]')})

        items.append({
            "full_name": f"{owner}/{name}",
            "url": f"https://github.com/{owner}/{name}",
            "description": description,
            "description_source": "github" if description else "",
            "language": language,
            "stars": stars or 0,
            "stars_today": stars_today,
            "forks": forks or 0,
            "built_by": built_by,
        })
    return items


def enrich_repo(session: requests.Session, item: dict, token: str) -> dict:
    """用 REST API 补充单个仓库的详情；描述为空时用 README 兜底。"""
    full = item["full_name"]
    try:
        resp = session.get(
            f"{API_BASE}/repos/{full}",
            headers=api_headers(token),
            timeout=30,
        )
        if resp.status_code == 200:
            repo = resp.json()
            item["description"] = (repo.get("description") or item["description"] or "").strip()
            item["language"] = repo.get("language") or item["language"]
            item["stars"] = repo.get("stargazers_count", item["stars"])
            item["forks"] = repo.get("forks_count", item["forks"])
            item["open_issues"] = repo.get("open_issues_count", 0)
            item["topics"] = repo.get("topics") or []
            item["homepage"] = repo.get("homepage") or ""
            lic = repo.get("license") or {}
            spdx = lic.get("spdx_id") or ""
            item["license"] = "" if spdx in ("", "NOASSERTION") else spdx
            item["created_at"] = repo.get("created_at") or ""
            item["pushed_at"] = repo.get("pushed_at") or ""
            if item["description"]:
                item["description_source"] = "github"
        else:
            item["api_error"] = f"HTTP {resp.status_code}"
    except Exception as exc:  # 网络抖动等，保留页面解析结果
        item["api_error"] = str(exc)

    if not item.get("description"):
        snippet = fetch_readme_snippet(session, full, token)
        if snippet:
            item["description"] = snippet
            item["description_source"] = "readme"

    if not item.get("description"):
        item["description_source"] = "none"
    return item


def fetch_readme_snippet(session: requests.Session, full: str, token: str, max_len: int = 300) -> str:
    """读取 README 原文，清理 Markdown 噪音后截取第一段有效文本。"""
    try:
        resp = session.get(
            f"{API_BASE}/repos/{full}/readme",
            headers={**api_headers(token), "Accept": "application/vnd.github.raw"},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""
        text = resp.text
        text = re.sub(r"```.*?```", " ", text, flags=re.S)          # 代码块
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)          # HTML 注释
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)            # 图片/徽章
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)         # 链接保留文字
        text = re.sub(r"^\s*#{1,6}\s*.*$", " ", text, flags=re.M)    # 标题行
        text = re.sub(r"[*_>|`]", " ", text)                         # 强调/引用/表格符号
        text = re.sub(r"<[^>]+>", " ", text)                         # 残留 HTML 标签
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        if len(text) > max_len:
            text = text[:max_len].rsplit(" ", 1)[0] + " ..."
        return text
    except Exception:
        return ""


def main() -> None:
    token = os_environ_token()
    session = requests.Session()

    print(f"[1/3] 抓取 Trending 页面: {TRENDING_URL}")
    items = fetch_trending_page(session)
    print(f"      解析到 {len(items)} 个项目")

    print(f"[2/3] 调用 GitHub API 补充详情（并发 {MAX_WORKERS}）...")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(enrich_repo, session, it, token): it for it in items}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                item = futures[future]
                item["api_error"] = str(exc)

    seen: set[str] = set()
    repos: list[dict] = []
    for idx, item in enumerate(items, start=1):
        if item["full_name"] in seen:
            continue
        seen.add(item["full_name"])
        item["rank"] = idx
        item["language_color"] = LANGUAGE_COLORS.get((item.get("language") or "").lower(), "#8b949e")
        repos.append(item)

    now = beijing_now()
    payload = {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S") + " (UTC+8)",
        "source": TRENDING_URL,
        "repo_count": len(repos),
        "repos": repos,
    }

    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / f"{payload['date']}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA_DIR / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[3/3] 已写入 {out.relative_to(ROOT)} 和 data/latest.json")

    print("\n榜单预览:")
    for r in repos[:5]:
        print(f"  #{r['rank']:<2} {r['full_name']:<40} star={r['stars']:<8} "
              f"today={r['stars_today']} lang={r.get('language') or '-'}")
    if len(repos) > 5:
        print(f"  ... 其余 {len(repos) - 5} 个见 JSON")


def os_environ_token() -> str:
    import os
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


if __name__ == "__main__":
    sys.exit(main())
