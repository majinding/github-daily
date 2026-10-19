#!/usr/bin/env python3
"""把 data/*.json 渲染成静态 HTML 报告。

输出:
  github-daily/index.html            最新一期报告
  github-daily/archive/YYYY-MM-DD.html  每期归档页
  github-daily/archive/index.html    归档索引
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "templates"
SITE_DIR = ROOT / "github-daily"
ARCHIVE_DIR = SITE_DIR / "archive"


def thousands(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def load_latest() -> dict:
    files = sorted(
        p for p in DATA_DIR.glob("20*.json") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name)
    )
    if not files:
        sys.exit("data/ 目录下没有报告数据，请先运行 fetch_trending.py")
    return json.loads(files[-1].read_text(encoding="utf-8"))


def build_lang_stats(repos: list[dict], top_n: int = 8) -> list[dict]:
    counter: dict[str, int] = {}
    colors: dict[str, str] = {}
    for r in repos:
        lang = r.get("language") or "未知"
        counter[lang] = counter.get(lang, 0) + 1
        colors[lang] = r.get("language_color", "#8b949e")
    total = sum(counter.values()) or 1
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    stats = [
        {"name": name, "count": count, "color": colors[name], "pct": round(count * 100 / total, 1)}
        for name, count in top
    ]
    return stats


def load_archive_list() -> list[dict]:
    """扫描归档目录与数据文件，生成往期列表（最新在前），含每期榜首。"""
    meta: dict[str, dict] = {}
    for p in DATA_DIR.glob("20*.json"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", p.name):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                repos = d.get("repos", [])
                meta[p.stem] = {
                    "count": d.get("repo_count", len(repos)),
                    "top_repo": repos[0]["full_name"] if repos else "",
                }
            except Exception:
                pass
    archive = []
    if ARCHIVE_DIR.is_dir():
        for p in ARCHIVE_DIR.glob("20*.html"):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", p.name):
                m = meta.get(p.stem, {})
                archive.append(
                    {
                        "date": p.stem,
                        "href": f"archive/{p.name}",
                        "count": m.get("count", 0),
                        "top_repo": m.get("top_repo", ""),
                    }
                )
    return sorted(archive, key=lambda x: x["date"], reverse=True)


def main() -> None:
    data = load_latest()
    repos = data.get("repos", [])
    if not repos:
        sys.exit("最新一期数据没有任何项目")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml")),
    )
    env.filters["thousands"] = thousands

    archive = load_archive_list()
    total_today = sum(r.get("stars_today") or 0 for r in repos)

    base_ctx = {
        "report_date": data["date"],
        "generated_at": data.get("generated_at", ""),
        "source_url": data.get("source", "https://github.com/trending?since=daily"),
        "repos": repos,
        "repo_count": len(repos),
        "total_stars_today": total_today,
        "lang_stats": build_lang_stats(repos),
        "top_repo_name": repos[0]["full_name"],
        "archive": [a for a in archive if a["date"] != data["date"]],
    }

    SITE_DIR.mkdir(exist_ok=True)
    ARCHIVE_DIR.mkdir(exist_ok=True)

    # 1) 最新一期（站点首页）
    ctx = {**base_ctx, "base_path": "."}
    html = env.get_template("report.html.j2").render(**ctx)
    (SITE_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"已生成 {(SITE_DIR / 'index.html').relative_to(ROOT)}")

    # 2) 本期归档页
    ctx = {**base_ctx, "base_path": ".."}
    html = env.get_template("report.html.j2").render(**ctx)
    (ARCHIVE_DIR / f"{data['date']}.html").write_text(html, encoding="utf-8")
    print(f"已生成 {(ARCHIVE_DIR / (data['date'] + '.html')).relative_to(ROOT)}")

    # 3) 归档索引（含本期）
    archive_all = load_archive_list()
    html = env.get_template("archive_index.html.j2").render(
        archive=archive_all, base_path="."
    )
    (ARCHIVE_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"已生成 {(ARCHIVE_DIR / 'index.html').relative_to(ROOT)}")
    print(f"共渲染 {len(repos)} 个项目，归档 {len(archive_all)} 期")


if __name__ == "__main__":
    sys.exit(main())
