#!/usr/bin/env python3
"""Generate a daily AI product intelligence brief."""

from __future__ import annotations

import argparse
import datetime as dt
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
OPENAI_URL = "https://api.openai.com/v1/responses"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
SOURCE_URLS = [
    "https://trustmrr.com/",
    "https://www.toolify.ai/zh/",
    "https://theresanaiforthat.com/",
    "https://www.producthunt.com/topics/artificial-intelligence",
    "https://www.indiehackers.com/products",
]


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


def collect_source_digest() -> str:
    sections = []
    for url in SOURCE_URLS:
        text = fetch_url_text(url)
        sections.append(f"来源：{url}\n摘录：{text}")
    return "\n\n---\n\n".join(sections)


def build_prompt(today: str, skill_text: str, source_digest: str) -> str:
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

可优先参考这些来源并使用网页搜索补充：
- https://trustmrr.com/
- https://www.toolify.ai/zh/
- https://theresanaiforthat.com/
- Product Hunt、Indie Hackers、X/Twitter 创始人公开信息、应用商店、Chrome Web Store、Shopify App Store、G2/Capterra、产品官网定价页等。

本次自动抓取到的公开来源摘要如下。请优先从这些摘要里挑选产品和证据；如果证据不足，必须标注“低置信度”或“未知”，不要编造收入。

{source_digest}

Skill 内容：

{skill_text}
"""


def generate_brief(today: str, *, dry_run: bool = False) -> str:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")

    if dry_run:
        return f"# AI 产品商业情报｜{today}\n\n这是 dry-run 测试内容，用于验证 GitHub Actions、GitHub Pages 和微信推送链路。"

    source_digest = collect_source_digest()
    prompt = build_prompt(today, skill_text, source_digest)

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
