"""Markdown to High-Quality Image & Plaintext Hybrid Formatter."""

import html
import re
from typing import Optional


def format_markdown_to_clean_text(md_text: str) -> str:
    """纯文本降级转换器：将 Markdown 转为排版规整的 ASCII 文本。"""
    if not md_text or not isinstance(md_text, str):
        return ""

    text = md_text.strip()
    # 标题转换
    text = re.sub(r"^#{1,2}\s+(.+)$", r"【\1】", text, flags=re.MULTILINE)
    text = re.sub(r"^#{3,6}\s+(.+)$", r"◆ \1", text, flags=re.MULTILINE)

    # 代码块
    def _replace_code_block(match: re.Match) -> str:
        lang = match.group(1).strip() if match.group(1) else "代码"
        code = match.group(2).strip()
        lines = code.split("\n")
        indented_code = "\n".join("  " + l for l in lines)
        return f"\n┌── 📄 {lang} ─────────\n{indented_code}\n└──────────────────────\n"

    text = re.sub(r"```([a-zA-Z0-9_-]*)\n(.*?)```", _replace_code_block, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", r"「\1」", text)
    text = re.sub(r"\*\*([^\*\n]+)\*\*", r"【\1】", text)
    text = re.sub(r"\*([^\*\n]+)\*", r"\1", text)
    text = re.sub(r"__([^_\n]+)__", r"【\1】", text)
    text = re.sub(r"_([^_\n]+)_", r"\1", text)
    text = re.sub(r"\[([^\]\n]+)\]\((https?://[^\)\s]+)\)", r"\1 (\2)", text)
    text = re.sub(r"^[\*\-\+]\s+", r"• ", text, flags=re.MULTILINE)
    text = re.sub(r"^>\s*(.+)$", r"▎ \1", text, flags=re.MULTILINE)
    text = re.sub(r"^[-\*_]{3,}$", r"──────────────", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", r"\n\n", text)
    return text.strip()


def build_github_dark_html(md_text: str, title: str = "DeepSeek Harness 交付报告") -> str:
    """将 Markdown 转换为精美的 GitHub/Notion 深色高对比度 HTML 页面。"""
    safe_title = html.escape(title)
    
    # 基础 HTML 转义
    lines = md_text.split("\n")
    html_lines = []
    in_code_block = False
    code_lang = ""
    code_buffer = []

    for line in lines:
        if line.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line[3:].strip() or "text"
                code_buffer = []
            else:
                in_code_block = False
                escaped_code = html.escape("\n".join(code_buffer))
                html_lines.append(f'<div class="code-container"><div class="code-header"><span class="badge">{code_lang}</span></div><pre><code>{escaped_code}</code></pre></div>')
            continue

        if in_code_block:
            code_buffer.append(line)
            continue

        # 标题
        if line.startswith("# "):
            html_lines.append(f'<h1 class="h1">{html.escape(line[2:].strip())}</h1>')
        elif line.startswith("## "):
            html_lines.append(f'<h2 class="h2">{html.escape(line[3:].strip())}</h2>')
        elif line.startswith("### "):
            html_lines.append(f'<h3 class="h3">{html.escape(line[4:].strip())}</h3>')
        elif line.startswith("- ") or line.startswith("* "):
            content = html.escape(line[2:].strip())
            content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
            content = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', content)
            html_lines.append(f'<li class="li">{content}</li>')
        elif line.startswith("> "):
            content = html.escape(line[2:].strip())
            html_lines.append(f'<blockquote class="quote">{content}</blockquote>')
        elif line.strip() == "---" or line.strip() == "***":
            html_lines.append('<hr class="divider"/>')
        else:
            if not line.strip():
                html_lines.append('<div class="spacer"></div>')
            else:
                content = html.escape(line.strip())
                content = re.sub(r'`([^`]+)`', r'<code>\1</code>', content)
                content = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', content)
                html_lines.append(f'<p class="p">{content}</p>')

    body_content = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #0d1117;
    color: #e6edf3;
    padding: 24px;
    width: 800px;
    font-size: 14px;
    line-height: 1.6;
  }}
  .card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 28px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
  }}
  .header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #30363d;
    padding-bottom: 14px;
    margin-bottom: 20px;
  }}
  .logo {{
    font-size: 16px;
    font-weight: 700;
    color: #58a6ff;
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .tag {{
    font-size: 11px;
    background: #1f6feb22;
    color: #58a6ff;
    border: 1px solid #1f6feb55;
    padding: 2px 8px;
    border-radius: 20px;
  }}
  .h1 {{ font-size: 20px; color: #58a6ff; margin: 16px 0 10px 0; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
  .h2 {{ font-size: 17px; color: #79c0ff; margin: 14px 0 8px 0; }}
  .h3 {{ font-size: 15px; color: #d2a8ff; margin: 12px 0 6px 0; }}
  .p {{ margin: 6px 0; color: #c9d1d9; }}
  .li {{ margin-left: 20px; margin-bottom: 4px; color: #c9d1d9; }}
  .quote {{
    border-left: 4px solid #388bfd;
    padding: 6px 12px;
    color: #8b949e;
    background: #0d111755;
    margin: 10px 0;
    border-radius: 0 6px 6px 0;
  }}
  .code-container {{
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    margin: 12px 0;
    overflow: hidden;
  }}
  .code-header {{
    background: #161b22;
    padding: 4px 12px;
    border-bottom: 1px solid #30363d;
  }}
  .badge {{
    font-size: 11px;
    color: #8b949e;
    text-transform: uppercase;
    font-family: ui-monospace, monospace;
  }}
  pre {{
    padding: 12px 14px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12.5px;
    line-height: 1.5;
    color: #7ee787;
  }}
  code {{
    background: #21262d;
    color: #f0883e;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: ui-monospace, monospace;
    font-size: 12px;
  }}
  .divider {{ border: 0; border-top: 1px solid #30363d; margin: 16px 0; }}
  .spacer {{ height: 8px; }}
  .footer {{
    margin-top: 24px;
    text-align: right;
    font-size: 11px;
    color: #8b949e;
  }}
</style>
</head>
<body>
<div class="card">
  <div class="header">
    <div class="logo">🐋 DS娘 x DeepSeek Harness</div>
    <div class="tag">智能体执行交付</div>
  </div>
  {body_content}
  <div class="footer">DeepSeek Harness Agent • Powered by MaiBot</div>
</div>
</body>
</html>"""
