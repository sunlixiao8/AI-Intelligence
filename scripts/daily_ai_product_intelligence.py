#!/usr/bin/env python3
"""Generate a daily AI product intelligence brief."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
from urllib.parse import urljoin, urlparse
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "ai-product-intelligence" / "SKILL.md"
DAILY_DIR = ROOT / "ai-product-intelligence" / "daily"
HISTORY_DIR = ROOT / "ai-product-intelligence" / "history"
PRODUCT_HISTORY_PATH = HISTORY_DIR / "products.json"
OPENAI_URL = "https://api.openai.com/v1/responses"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
SOURCE_CONFIGS = [
    {"name": "TrustMRR", "url": "https://trustmrr.com/", "weight": 6, "kind": "revenue"},
    {"name": "Toolify", "url": "https://www.toolify.ai/zh/", "weight": 4, "kind": "directory"},
    {"name": "There's An AI For That", "url": "https://theresanaiforthat.com/", "weight": 4, "kind": "directory"},
    {"name": "Product Hunt AI", "url": "https://www.producthunt.com/topics/artificial-intelligence", "weight": 4, "kind": "launch"},
    {"name": "Indie Hackers Products", "url": "https://www.indiehackers.com/products", "weight": 5, "kind": "founder"},
]
MIN_REPEAT_GAP_DAYS = 30
MAX_CANDIDATES_PER_SOURCE = 18
MAX_SELECTED_CANDIDATES = 10


@dataclass
class ProductCandidate:
    name: str
    url: str
    source_name: str
    source_url: str
    source_kind: str
    score: int
    reasons: list[str]
    seen_before: bool = False
    last_featured: str | None = None
    featured_count: int = 0


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def candidate_key(name: str, url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parsed.path.lower())
    if host and path and path not in {"", "/"}:
        return f"{host}{path}"
    if host:
        return host
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def clean_candidate_name(text: str, url: str, source_name: str) -> str:
    name = normalize_space(text)
    name = re.sub(r"^(for sale|sold|acquired|featured)\s+", "", name, flags=re.IGNORECASE)
    name = re.split(r"\s+(revenue|mrr|arr|price|multiple|profit)\s+", name, maxsplit=1, flags=re.IGNORECASE)[0]

    parsed = urlparse(url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if source_name == "TrustMRR" and len(path_parts) >= 2 and path_parts[0] == "startup":
        slug = path_parts[-1]
        if slug.endswith("-com"):
            return slug[:-4].replace("-", "") + ".com"
        return " ".join(part.upper() if part in {"ai", "gpt"} else part.capitalize() for part in slug.split("-"))
    if source_name == "There's An AI For That" and len(path_parts) >= 2 and path_parts[0] == "ai":
        slug = path_parts[1]
        return " ".join(part.upper() if part in {"ai", "gpt"} else part.capitalize() for part in slug.split("-"))

    return name[:90]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if len(text) >= 3:
            self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if tag == "a" and not self.skip_depth:
            attrs_dict = dict(attrs)
            href = attrs_dict.get("href")
            if href:
                self._href = urljoin(self.base_url, href)
                self._text_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if tag == "a" and self._href:
            text = normalize_space(" ".join(self._text_parts))
            if text:
                self.links.append({"text": text, "url": self._href})
            self._href = None
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self._href:
            text = normalize_space(data)
            if text:
                self._text_parts.append(text)


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {body[:500]}") from exc


def extract_response_text(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()

    parts: list[str] = []
    for item in response.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "\n\n".join(parts).strip()


def extract_chat_text(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def fetch_url_text(url: str, max_chars: int = 4000) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AI-Intelligence/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(800_000)
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception as exc:
        return f"[抓取失败] {url}: {exc}"

    html_text = raw.decode(charset, errors="replace")
    parser = TextExtractor()
    parser.feed(html_text)
    text = re.sub(r"\s+", " ", parser.text()).strip()
    if not text:
        return f"[无可读文本] {url}"
    return text[:max_chars]


def fetch_url_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; AI-Intelligence/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(1_200_000)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def is_probable_product_link(text: str, url: str, source_url: str) -> bool:
    text = normalize_space(text)
    if not (3 <= len(text) <= 90):
        return False
    lowered = text.lower()
    if lowered in {
        "sign in",
        "login",
        "submit",
        "pricing",
        "blog",
        "jobs",
        "advertise",
        "privacy",
        "terms",
        "contact",
        "newsletter",
        "next",
        "previous",
        "learn more",
        "view all",
        "more",
        "更多",
        "登录",
        "注册",
        "价格",
        "博客",
    }:
        return False
    if re.match(r"^(free|from|\\$|€|£|¥|\\+|free \\+)", lowered):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if any(part in parsed.path.lower() for part in ["/login", "/signin", "/privacy", "/terms", "/about", "/blog"]):
        return False
    source_host = urlparse(source_url).netloc.lower().removeprefix("www.")
    host = parsed.netloc.lower().removeprefix("www.")
    if host != source_host:
        return True
    return any(part in parsed.path.lower() for part in ["/product", "/products", "/tool", "/ai", "/startup", "/company"])


def score_candidate(text: str, url: str, source: dict) -> tuple[int, list[str]]:
    blob = f"{text} {url}".lower()
    score = int(source["weight"])
    reasons = [f"{source['name']} 来源权重 +{source['weight']}"]
    scoring_rules = [
        (["mrr", "arr", "revenue", "income", "sales", "profit", "$"], 5, "收入/销售线索"),
        (["pricing", "price", "paid", "subscription", "plan", "付费", "价格", "订阅"], 4, "付费意愿线索"),
        (["ai", "gpt", "llm", "agent", "automation", "生成", "智能", "自动化"], 3, "AI/自动化相关"),
        (["sales", "marketing", "crm", "support", "legal", "finance", "shopify", "chrome", "email", "客服", "营销", "销售", "电商"], 3, "具体业务场景"),
        (["launch", "featured", "popular", "trending", "reviews", "rating", "users", "增长", "热门"], 2, "热度/牵引力线索"),
        (["api", "extension", "plugin", "workflow", "dashboard", "report", "integration", "插件", "工作流"], 2, "可产品化工作流"),
    ]
    for keywords, points, reason in scoring_rules:
        if any(keyword in blob for keyword in keywords):
            score += points
            reasons.append(f"{reason} +{points}")
    return score, reasons


def load_product_history() -> dict:
    if not PRODUCT_HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(PRODUCT_HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def days_since(date_text: str | None, today: str) -> int | None:
    if not date_text:
        return None
    try:
        old = dt.date.fromisoformat(date_text)
        current = dt.date.fromisoformat(today)
    except ValueError:
        return None
    return (current - old).days


def collect_product_candidates(today: str, history: dict) -> tuple[list[ProductCandidate], str]:
    candidates: dict[str, ProductCandidate] = {}
    source_notes: list[str] = []
    for source in SOURCE_CONFIGS:
        url = str(source["url"])
        try:
            html_text = fetch_url_html(url)
        except Exception as exc:
            source_notes.append(f"来源：{url}\n状态：抓取失败：{exc}")
            continue

        parser = TextExtractor()
        parser.feed(html_text)
        page_text = normalize_space(parser.text())[:3500]
        source_notes.append(f"来源：{url}\n页面摘要：{page_text}")

        link_parser = LinkExtractor(url)
        link_parser.feed(html_text)
        per_source = 0
        for link in link_parser.links:
            if per_source >= MAX_CANDIDATES_PER_SOURCE:
                break
            product_url = link["url"].split("#", 1)[0]
            name = clean_candidate_name(link["text"], product_url, str(source["name"]))
            if not is_probable_product_link(name, product_url, url):
                continue

            score, reasons = score_candidate(name, product_url, source)
            key = candidate_key(name, product_url)
            record = history.get(key, {})
            last_featured = record.get("last_featured")
            featured_count = int(record.get("featured_count") or 0)
            gap = days_since(last_featured, today)
            if gap is not None and gap < MIN_REPEAT_GAP_DAYS:
                score -= 100
                reasons.append(f"{MIN_REPEAT_GAP_DAYS} 天内已推送，降权")
            elif featured_count:
                score -= min(featured_count * 2, 8)
                reasons.append("历史出现过，轻微降权")

            candidate = ProductCandidate(
                name=name,
                url=product_url,
                source_name=str(source["name"]),
                source_url=url,
                source_kind=str(source["kind"]),
                score=score,
                reasons=reasons,
                seen_before=bool(record),
                last_featured=last_featured,
                featured_count=featured_count,
            )
            if key not in candidates or candidate.score > candidates[key].score:
                candidates[key] = candidate
            per_source += 1

    ranked = sorted(candidates.values(), key=lambda item: item.score, reverse=True)
    return ranked, "\n\n---\n\n".join(source_notes)


def selected_candidates_digest(candidates: list[ProductCandidate]) -> str:
    if not candidates:
        return "未抽取到可靠候选产品。请基于来源摘要谨慎选择，并明确低置信度。"
    rows = []
    for index, candidate in enumerate(candidates[:MAX_SELECTED_CANDIDATES], start=1):
        rows.append(
            "\n".join(
                [
                    f"{index}. {candidate.name}",
                    f"   URL: {candidate.url}",
                    f"   来源: {candidate.source_name} ({candidate.source_kind})",
                    f"   分数: {candidate.score}",
                    f"   评分原因: {'; '.join(candidate.reasons[:5])}",
                    f"   历史: 上次推送 {candidate.last_featured or '无'}，累计 {candidate.featured_count} 次",
                ]
            )
        )
    return "\n".join(rows)


def update_product_history(today: str, candidates: list[ProductCandidate]) -> None:
    history = load_product_history()
    for candidate in candidates[:4]:
        key = candidate_key(candidate.name, candidate.url)
        record = history.get(key, {})
        history[key] = {
            "name": candidate.name,
            "url": candidate.url,
            "source_name": candidate.source_name,
            "source_url": candidate.source_url,
            "first_featured": record.get("first_featured") or today,
            "last_featured": today,
            "featured_count": int(record.get("featured_count") or 0) + 1,
            "last_score": candidate.score,
        }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect_source_digest() -> str:
    sections = []
    for url in SOURCE_URLS:
        text = fetch_url_text(url)
        sections.append(f"来源：{url}\n摘录：{text}")
    return "\n\n---\n\n".join(sections)


def build_prompt(today: str, skill_text: str, source_digest: str, candidate_digest: str) -> str:
    return f"""今天日期：{today}

请根据下面的 skill 生成一份中文简体《AI 产品商业情报》深度简报。

额外要求：
- 面向刚开始学习 AI 产品开发的个人创业者。
- 阅读时长控制在约 20 分钟。
- 优先选择 AI 产品；非 AI 产品只有在商业模式或场景切入值得学习时才纳入。
- 每个产品必须附来源链接。
- 收入可以估算，但必须明确区分官方收入、推测收入和未知收入，并标注置信度。
- 技术部分重点说明：如果我来开发，会遇到什么大问题。
- 最后给出今日模式、建造雷达、用户访谈问题。
- 优先从“候选产品池”中选择 3-4 个分数最高且不重复的产品。如果候选信息不足，可以使用来源摘要补充，但必须说明证据不足。
- 不要重复分析近期已经推送过的产品，除非候选池显示它有新的收入、融资、热度或产品变化线索。
- 选择产品时优先级：收入/MRR/ARR 线索 > 清楚定价 > 真实用户评价/热度 > 具体业务场景 > 个人创业者可复刻小切口。

可优先参考这些来源并使用网页搜索补充：
- https://trustmrr.com/
- https://www.toolify.ai/zh/
- https://theresanaiforthat.com/
- Product Hunt、Indie Hackers、X/Twitter 创始人公开信息、应用商店、Chrome Web Store、Shopify App Store、G2/Capterra、产品官网定价页等。

本次自动抓取到的公开来源摘要如下。请优先从这些摘要里挑选产品和证据；如果证据不足，必须标注“低置信度”或“未知”，不要编造收入。

{source_digest}

本次候选产品池如下。候选分数只是机器初筛，请你结合商业判断二次筛选：

{candidate_digest}

Skill 内容：

{skill_text}
"""


def generate_brief(today: str, *, dry_run: bool = False) -> str:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")

    if dry_run:
        return f"# AI 产品商业情报｜{today}\n\n这是 dry-run 测试内容，用于验证 GitHub Actions、GitHub Pages 和微信推送链路。"

    history = load_product_history()
    candidates, source_digest = collect_product_candidates(today, history)
    selected_candidates = candidates[:MAX_SELECTED_CANDIDATES]
    prompt = build_prompt(today, skill_text, source_digest, selected_candidates_digest(selected_candidates))

    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek_key:
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是严谨的中文 AI 产品商业情报分析师。必须区分事实、估算和未知，不要编造收入或来源。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "stream": False,
        }
        response = post_json(
            DEEPSEEK_URL,
            payload,
            headers={"Authorization": f"Bearer {deepseek_key}"},
        )
        text = extract_chat_text(response)
        if not text:
            raise RuntimeError(f"DeepSeek response did not contain text: {json.dumps(response)[:1000]}")
        update_product_history(today, selected_candidates)
        return text

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY or OPENAI_API_KEY environment variable.")

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    payload = {
        "model": model,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "temperature": 0.4,
    }
    response = post_json(
        OPENAI_URL,
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    text = extract_response_text(response)
    if not text:
        raise RuntimeError(f"OpenAI response did not contain text: {json.dumps(response)[:1000]}")
    update_product_history(today, selected_candidates)
    return text


def sanitize_error(message: str) -> str:
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[REDACTED]", message)
    message = re.sub(r"(Bearer\\s+)[A-Za-z0-9._-]+", r"\\1[REDACTED]", message)
    return message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Generate test content without calling OpenAI.")
    args = parser.parse_args()

    today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    brief = generate_brief(today, dry_run=args.dry_run)

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DAILY_DIR / f"{today}.md"
    output_path.write_text(brief.rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")

    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = sanitize_error(str(exc))
        print(f"::error title=Generate brief failed::{message}")
        raise
