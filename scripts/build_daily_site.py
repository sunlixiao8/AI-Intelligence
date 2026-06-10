#!/usr/bin/env python3
"""Build a small static site for daily AI product intelligence briefs."""

from __future__ import annotations

import html
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[1]
DAILY_DIR = ROOT / "ai-product-intelligence" / "daily"
SITE_DIR = ROOT / "site"
SITE_DAILY_DIR = SITE_DIR / "daily"


def inline_markdown(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        escaped,
    )
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append("<ul>\n" + "\n".join(list_items) + "\n</ul>")
            list_items = []

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line:
            flush_list()
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_list()
            level = min(len(heading.group(1)), 4)
            blocks.append(f"<h{level}>{inline_markdown(heading.group(2))}</h{level}>")
            continue

        item = re.match(r"^\s*[-*]\s+(.+)$", line)
        if item:
            list_items.append(f"<li>{inline_markdown(item.group(1))}</li>")
            continue

        numbered = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if numbered:
            list_items.append(f"<li>{inline_markdown(numbered.group(1))}</li>")
            continue

        flush_list()
        blocks.append(f"<p>{inline_markdown(line)}</p>")

    flush_list()
    return "\n".join(blocks)


def page_template(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --text: #17202a;
      --muted: #667085;
      --line: #d9e2ec;
      --bg: #f7f9fb;
      --paper: #ffffff;
      --accent: #146c94;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      line-height: 1.75;
    }}
    main {{
      width: min(860px, 100%);
      margin: 0 auto;
      padding: 28px 18px 52px;
    }}
    article {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: clamp(20px, 5vw, 44px);
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.06);
    }}
    h1 {{ font-size: clamp(28px, 7vw, 42px); line-height: 1.2; margin: 0 0 24px; }}
    h2 {{ font-size: 24px; margin: 34px 0 12px; border-top: 1px solid var(--line); padding-top: 24px; }}
    h3 {{ font-size: 19px; margin: 26px 0 8px; color: #223548; }}
    h4 {{ font-size: 17px; margin: 22px 0 8px; }}
    p {{ margin: 10px 0; }}
    ul {{ padding-left: 22px; margin: 10px 0 16px; }}
    li {{ margin: 6px 0; }}
    a {{ color: var(--accent); word-break: break-word; }}
    code {{
      background: #eef4f7;
      border: 1px solid #d7e4ea;
      border-radius: 4px;
      padding: 1px 5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.92em;
    }}
    .back {{
      display: inline-block;
      margin-bottom: 16px;
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
    }}
    .index-list {{
      list-style: none;
      padding: 0;
    }}
    .index-list li {{
      border-bottom: 1px solid var(--line);
      padding: 14px 0;
    }}
  </style>
</head>
<body>
  <main>
    <article>
      {body}
    </article>
  </main>
</body>
</html>
"""


def build() -> None:
    SITE_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, str]] = []

    for md_path in sorted(DAILY_DIR.glob("*.md"), reverse=True):
        date = md_path.stem
        markdown = md_path.read_text(encoding="utf-8")
        first_heading = next((line.lstrip("# ").strip() for line in markdown.splitlines() if line.startswith("# ")), date)
        body = f'<a class="back" href="../index.html">返回列表</a>\n{markdown_to_html(markdown)}'
        html_path = SITE_DAILY_DIR / f"{date}.html"
        html_path.write_text(page_template(first_heading, body), encoding="utf-8")
        entries.append((date, first_heading))

    items = "\n".join(
        f'<li><a href="daily/{date}.html">{html.escape(title)}</a></li>' for date, title in entries
    )
    index_body = f"<h1>AI 产品商业情报</h1>\n<ul class=\"index-list\">\n{items}\n</ul>"
    (SITE_DIR / "index.html").write_text(page_template("AI 产品商业情报", index_body), encoding="utf-8")
    print(f"Built site at {SITE_DIR}")


if __name__ == "__main__":
    build()
