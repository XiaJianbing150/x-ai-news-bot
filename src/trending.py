"""GitHub Trending 详情增强: 给每个仓库补上"用户视角"的三点解读。

数据来源:
1. GitHub REST API (repo 元数据 / 最新 release / 热门 open issue)
2. raw.githubusercontent.com 的 README 摘要
3. DeepSeek 综合以上信息,生成 功能 / 开发者价值 / 稳定性 三点分析

稳定性参考: 优先走 GITHUB_TOKEN (CI 里用 github.token),本地没有 token 时
匿名访问,限速 60 次/小时,5 个仓库约 20 次请求,单日多次运行建议配 token。
"""
import os
import re
import requests
from datetime import datetime, timezone

from .config import DEEPSEEK_API_URL, DEEPSEEK_MODEL, REQUEST_TIMEOUT

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"
_UA = "Mozilla/5.0 (compatible; x-ai-news-bot/1.0; +https://github.com/)"


def _gh_headers():
    h = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _gh_get(url: str):
    try:
        r = requests.get(url, headers=_gh_headers(), timeout=REQUEST_TIMEOUT)
        return r
    except Exception as e:
        print(f"  [trending] GET {url} 异常: {e!r}")
        return None


def _fetch_repo_meta(repo: dict) -> dict:
    r = _gh_get(f"{_API}/repos/{repo['owner']}/{repo['name']}")
    if not r or r.status_code != 200:
        return {}
    d = r.json()
    return {
        "stars": d.get("stargazers_count", 0),
        "forks": d.get("forks_count", 0),
        "open_issues": d.get("open_issues_count", 0),
        "pushed_at": (d.get("pushed_at") or "")[:10],
        "created_at": (d.get("created_at") or "")[:10],
        "license": (d.get("license") or {}).get("spdx_id", ""),
        "homepage": d.get("homepage") or "",
    }


def _fetch_latest_release(repo: dict) -> dict:
    r = _gh_get(f"{_API}/repos/{repo['owner']}/{repo['name']}/releases/latest")
    if not r or r.status_code != 200:
        return {}
    d = r.json()
    return {
        "tag": d.get("tag_name", ""),
        "published_at": (d.get("published_at") or "")[:10],
    }


def _fetch_top_issues(repo: dict, n: int = 5) -> list:
    url = (f"{_API}/repos/{repo['owner']}/{repo['name']}/issues"
           f"?state=open&sort=comments&direction=desc&per_page={n}")
    r = _gh_get(url)
    if not r or r.status_code != 200:
        return []
    out = []
    for it in r.json():
        if it.get("pull_request"):  # issues 端点会混入 PR,过滤
            continue
        out.append(it.get("title", "").strip())
        if len(out) >= n:
            break
    return out


def _fetch_readme_snippet(repo: dict, limit: int = 1000) -> str:
    for branch in ("HEAD", "main", "master"):
        url = f"{_RAW}/{repo['owner']}/{repo['name']}/{branch}/README.md"
        try:
            r = requests.get(url, headers={"User-Agent": _UA}, timeout=REQUEST_TIMEOUT)
        except Exception:
            continue
        if r.ok and r.text.strip():
            text = re.sub(r"\s+", " ", r.text).strip()
            return text[:limit]
    return ""


def _build_context(repos: list) -> str:
    parts = []
    for i, r in enumerate(repos, 1):
        meta = r.get("_gh", {})
        issues = r.get("_issues", [])
        rel = r.get("_release", {})
        readme = r.get("_readme", "")
        lines = [
            f"[{i}] {r['owner']}/{r['name']}",
            f"官方简介: {r.get('desc','')}",
            f"主语言: {r.get('lang','')} | ⭐ {meta.get('stars',0)} | fork {meta.get('forks',0)}"
            f" | 开放 issue {meta.get('open_issues',0)}",
            f"最近推送: {meta.get('pushed_at','?')} | 创建于: {meta.get('created_at','?')}"
            f" | 许可证: {meta.get('license','?')}",
        ]
        if rel:
            lines.append(f"最新版本: {rel.get('tag','?')} (发布于 {rel.get('published_at','?')})")
        if issues:
            lines.append("热门 open issue: " + " | ".join(issues[:3]))
        if readme:
            lines.append(f"README 摘要: {readme}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _call_deepseek(prompt: str, max_tokens: int = 2048) -> str:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("  [trending] 无 DEEPSEEK_API_KEY,跳过详情增强")
        return ""
    for model in ("deepseek-v4-flash", DEEPSEEK_MODEL):
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": False,
            # 显式关闭 thinking,避免推理独白污染正文 (与早报同根因)
            "thinking": {"type": "disabled"},
        }
        try:
            r = requests.post(
                DEEPSEEK_API_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=120,
            )
            if r.status_code == 400 and "thinking" in r.text:
                body.pop("thinking")
                r = requests.post(
                    DEEPSEEK_API_URL,
                    json=body,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    timeout=120,
                )
            if not r.ok:
                print(f"  [trending] model={model} HTTP {r.status_code}: {r.text[:200]}")
                continue
            msg = (r.json().get("choices") or [{}])[0].get("message", {})
            content = (msg.get("content") or msg.get("reasoning_content") or "").strip()
            if content:
                print(f"  [trending] 详情生成使用 model={model}")
                return content
            print(f"  [trending] model={model} 返回空 content,尝试下一个")
        except Exception as e:
            print(f"  [trending] model={model} 异常(尝试下一个): {e!r}")
    return ""


_PROMPT = """下面是 GitHub {period} Trending 的 {n} 个项目,每个附上了官方简介、star/fork、
开放 issue 数、最近推送时间、最新版本、热门 open issue 和 README 摘要。

请站在「想用这些项目的开发者」的角度,为每个项目写中文三点解读:
1. 🔍 功能: 这是什么项目、解决什么问题,让完全没听过的人也能看懂
2. 🎯 开发者价值: 什么场景/什么人会用,用了能带来什么效果,上手成本高不高
3. ⚖️ 稳定性: 结合 star 数、最近提交活跃度、版本节奏、开放 issue 数量与热门 issue 内容,
   客观判断项目是否成熟稳定、有没有已知的坑或严重问题

要求:
- 每条 30~60 字,说人话,不堆砌术语
- 字段名必须严格用「功能」「开发者价值」「稳定性」,前面不要加 emoji 或 **
- 严格按下面的格式输出,每个项目一段,编号对齐,不要输出任何其它内容
- 不确定的信息(比如 issue 标题无法判断是否严重)就写"未见明显大坑",不要编造

输出格式示例(必须完全照抄这个格式):
1. **freestylefly/awesome-gpt-image-2**
- 功能: xxx
- 开发者价值: xxx
- 稳定性: xxx

2. **tt-a1i/archify**
- 功能: xxx
- 开发者价值: xxx
- 稳定性: xxx

{context}
"""

_FIELD_ALIAS = {
    "功能": "func", "用途": "func", "介绍": "func",
    "开发者价值": "dev", "价值": "dev", "对开发者": "dev",
    "稳定性": "stable", "是否稳定": "stable", "风险": "stable",
}


def _parse_enrich(content: str, repos: list) -> int:
    cur = -1
    for raw in content.split("\n"):
        line = raw.strip().replace("\ufe0f", "")  # 去掉变体选择符 (⚖️ = U+2696+U+FE0F)
        if not line:
            continue
        # 编号行: 1. **owner/name** / **1. owner/name** / [1] owner/name / 1️⃣ owner/name
        m = re.match(
            r"^(?:\*\*)?[\[【]?(\d+)[\]】]?[.．、\)]?\s*(?:\*\*)?([\w\-_.]+/[\w\-_.]+)",
            line,
        )
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(repos):
                cur = idx
            continue
        # 字段行: 容忍 -/•/*、emoji、** 等前缀和变体字段名
        m = re.match(
            r"^[-•*\s]*[🔍🎯⚖️✨💡📌✅❗]?\s*(?:\*\*)?"
            r"(功能|用途|介绍|开发者价值|价值|对开发者|稳定性|是否稳定|风险)"
            r"(?:\*\*)?[：:]\s*(.+)$",
            line,
        )
        if m and cur >= 0:
            field = _FIELD_ALIAS.get(m.group(1))
            if field:
                repos[cur].setdefault("enrich", {})[field] = m.group(2).strip()
    return sum(1 for r in repos if r.get("enrich", {}).get("func"))


def enrich_trending(repos: list, period: str = "本周") -> list:
    """给 trending 仓库补 GitHub 元数据 + DeepSeek 三点解读。
    任何一步失败都静默降级: 返回原列表 (调用方已有 desc_zh 兜底)。
    """
    if not repos:
        return repos
    try:
        for r in repos:
            r["_gh"] = _fetch_repo_meta(r)
            r["_release"] = _fetch_latest_release(r)
            r["_issues"] = _fetch_top_issues(r)
            r["_readme"] = _fetch_readme_snippet(r)
        context = _build_context(repos)
        if not context:
            print("  [trending] 没有任何仓库元数据,跳过详情增强")
            return repos
        content = _call_deepseek(
            _PROMPT.format(period=period, n=len(repos), context=context)
        )
        if not content:
            print("  [trending] DeepSeek 详情生成失败,fallback 翻译简介")
            return repos
        done = _parse_enrich(content, repos)
        if done == 0:
            print(f"  [trending] ⚠️ 详情解析 0 条,返回原文前 800 字符供诊断:\n{content[:800]}")
        print(f"  [trending] 详情增强 -> {done}/{len(repos)} 条")
    except Exception as e:
        print(f"  [trending] 详情增强整体失败(忽略): {e!r}")
    finally:
        # 清掉内部临时字段,避免泄漏进归档/邮件
        for r in repos:
            for k in ("_gh", "_release", "_issues", "_readme"):
                r.pop(k, None)
    return repos


def days_since(iso_date: str) -> int:
    """ISO 日期距今多少天,解析失败返回 -1。"""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except (ValueError, TypeError):
        return -1
