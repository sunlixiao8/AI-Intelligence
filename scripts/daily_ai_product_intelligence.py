#!/usr/bin/env python3
"""Generate a daily AI product intelligence brief."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo


ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "ai-product-intelligence" / "SKILL.md"
DAILY_DIR = ROOT / "ai-product-intelligence" / "daily"
OPENAI_URL = "https://api.openai.com/v1/responses"


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


def build_prompt(today: str, skill_text: str) -> str:
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

Skill 内容：

{skill_text}
"""


def generate_brief(today: str, *, dry_run: bool = False) -> str:
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    prompt = build_prompt(today, skill_text)

    if dry_run:
        return f"# AI 产品商业情报｜{today}\n\n这是 dry-run 测试内容，用于验证 GitHub Actions 和 PushPlus 推送链路。"

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY environment variable.")

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
    raise SystemExit(main())
