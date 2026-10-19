# GitHub 每日热榜

每天凌晨 01:00（北京时间）自动抓取 [GitHub Trending](https://github.com/trending?since=daily) 每日热榜，生成中文界面的 HTML 报告并部署到 GitHub Pages，同时推送到企业微信 / 钉钉群机器人。

**报告地址：<https://majinding.github.io/github-daily/>**

## 功能

- 抓取 Trending 每日热榜（有几个报几个，不强行凑数）
- 每个项目包含：排名、英文原文描述 + Agnes 3.0 Flash 自动中文翻译（描述为空时用 README 首段兜底）、总 Star、昨日新增 Star、主语言、Fork/Issue 数、License、Topic 标签
- HTML 报告含当日统计概览、语言分布、往期归档，支持深色模式，手机友好
- 每日原始数据存档于 `data/YYYY-MM-DD.json`，方便二次分析

## 目录结构

```
.github/workflows/github-daily.yml   定时工作流（UTC 17:00 = 北京 01:00，支持手动触发）
scripts/fetch_trending.py            抓取 Trending + GitHub API 补详情
scripts/render_report.py             渲染 HTML（今日页 / 归档页 / 归档索引）
scripts/notify.py                    企微 / 钉钉 Webhook 推送
templates/                           Jinja2 模板
github-daily/                        Pages 发布目录（index.html + archive/）
data/                                每日 JSON 原始数据
```

## 本地运行

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=<你的 GitHub Token>   # 可选，用于 API 补详情和 README 兜底
python scripts/fetch_trending.py
python scripts/render_report.py
REPORT_URL=http://localhost/python scripts/notify.py   # 可选
```

## 配置群机器人推送（可选）

仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：

| Secret 名 | 说明 |
|---|---|
| `WECOM_WEBHOOK` | 企业微信群机器人 Webhook 完整地址（不配则跳过企微推送） |
| `DINGTALK_WEBHOOK` | 钉钉自定义机器人 Webhook 完整地址（不配则跳过钉钉推送） |

机器人消息为 Top 10 摘要 + 完整报告链接。

## 配置中文翻译（可选）

报告会调用 [Agnes 3.0 Flash](https://agnes-ai.com/en/docs/agnes-30-flash) 把每个项目的英文描述翻译成中文，显示在英文原文下方。在 **Settings → Secrets and variables → Actions** 添加：

| Secret 名 | 说明 |
|---|---|
| `AGNES_API_KEY` | Agnes AI 的 API Key（Bearer 鉴权）。不配置则跳过翻译，报告仅显示英文原文 |

> Key 只存于加密 Secret，运行时注入环境变量，仓库访客与 PR 均看不到明文。

## 手动触发

Actions 页 → **GitHub Daily Trending** → **Run workflow**。

## 说明

- GitHub Actions 定时任务在流量高峰可能延迟几分钟到十几分钟执行
- 榜单项目数量以 GitHub Trending 实际返回为准（通常 25 个左右）
- 项目描述保留仓库英文原文，并由 Agnes 3.0 Flash 模型自动翻译为中文（译文附在原文下方）
