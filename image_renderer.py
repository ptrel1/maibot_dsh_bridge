"""High-Performance, Retina HD Markdown Card Image Renderer with Full Markdown Syntax Parsing (Bold, Code, Table, Lists, Quotes) & Color Emojis."""

import base64
import io
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


# =========================================================================
# 原生彩色 Emoji 渲染引擎（基于 NotoColorEmoji 动态提取与贴图合成）
# =========================================================================

EMOJI_PATTERN = re.compile(
    r"([\U00010000-\U0010ffff]|[\u2600-\u27ff]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\ufe0f]|⚠️|⚡|🐋|🐾|🌊|✨|🫧|🐬|🦈|⏱️|⌛|💡|🛡️|🧠|📄|🔌|🎉|🛑|🧹|📊|🔗)"
)

_emoji_font_cache = None
_emoji_sprite_cache: Dict[Tuple[str, int], Image.Image] = {}


def _get_emoji_font():
    global _emoji_font_cache
    if _emoji_font_cache is None:
        emoji_paths = [
            "/usr/share/fonts/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/google-noto-color-emoji-fonts/NotoColorEmoji.ttf",
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        ]
        for p in emoji_paths:
            if os.path.exists(p):
                try:
                    _emoji_font_cache = ImageFont.truetype(p, 109)
                    break
                except Exception:
                    pass
    return _emoji_font_cache


def get_rendered_emoji_sprite(char: str, target_size: int = 24) -> Optional[Image.Image]:
    """将单个 Emoji 字符渲染为高质量带有透明通道的彩色位图贴图（带内存缓存）。"""
    cache_key = (char, target_size)
    if cache_key in _emoji_sprite_cache:
        return _emoji_sprite_cache[cache_key]

    f_emoji = _get_emoji_font()
    if not f_emoji:
        return None

    try:
        raw_img = Image.new("RGBA", (140, 140), (0, 0, 0, 0))
        draw = ImageDraw.Draw(raw_img)
        draw.text((10, 0), char, font=f_emoji, embedded_color=True)
        bbox = raw_img.getbbox()
        if bbox:
            cropped = raw_img.crop(bbox)
            cropped.thumbnail((target_size, target_size), Image.Resampling.LANCZOS)
            _emoji_sprite_cache[cache_key] = cropped
            return cropped
    except Exception:
        pass
    return None


def _load_cjk_fonts() -> Tuple[Any, Any, Any, Any, Any, Any, Any]:
    """优先加载插件内置的【华康圆体 W7】（DFPYuanW7.ttf）。"""
    pkg_font = str(Path(__file__).resolve().parent / "assets" / "fonts" / "DFPYuanW7.ttf")
    
    font_candidates = [
        pkg_font,
        "/usr/local/share/fonts/华/华康圆体W7.ttf",
        "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    
    valid_font = None
    for cand in font_candidates:
        if os.path.exists(cand):
            valid_font = cand
            break

    if valid_font:
        try:
            f_title = ImageFont.truetype(valid_font, 36)
            f_h1 = ImageFont.truetype(valid_font, 30)
            f_h2 = ImageFont.truetype(valid_font, 26)
            f_body = ImageFont.truetype(valid_font, 22)
            f_code = ImageFont.truetype(valid_font, 20)
            f_small = ImageFont.truetype(valid_font, 18)
            f_badge = ImageFont.truetype(valid_font, 18)
            return f_title, f_h1, f_h2, f_body, f_code, f_small, f_badge
        except Exception:
            pass

    default_f = ImageFont.load_default()
    return default_f, default_f, default_f, default_f, default_f, default_f, default_f


# =========================================================================
# 富文本片段（Span）与行内 Markdown 解析器
# =========================================================================

class FormattedSpan:
    def __init__(self, text: str, span_type: str = "text"):
        self.text = text
        self.span_type = span_type  # "text", "bold", "inline_code", "emoji"


def parse_inline_markdown(line_text: str) -> List[FormattedSpan]:
    """解析单行内的 Markdown 标记（如 **加粗**、`行内代码`），生成结构化 Span。"""
    # 正则提取加粗和行内代码
    pattern = re.compile(r"(\*\*[^\*\n]+\*\*|`[^`\n]+`)")
    parts = pattern.split(line_text)
    spans = []

    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            spans.append(FormattedSpan(part[2:-2], span_type="bold"))
        elif part.startswith("`") and part.endswith("`") and len(part) >= 2:
            spans.append(FormattedSpan(part[1:-1], span_type="inline_code"))
        else:
            spans.append(FormattedSpan(part, span_type="text"))

    return spans


def draw_styled_markdown_line(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    spans: List[FormattedSpan],
    f_body: Any,
    f_h2: Any,
    f_code: Any,
    c_text: Tuple[int, int, int],
    c_bold: Tuple[int, int, int],
    c_code_text: Tuple[int, int, int],
    c_code_bg: Tuple[int, int, int],
    max_w: int,
    emoji_size: int = 22,
) -> int:
    """按行内格式（加粗高亮、代码胶囊底块、彩色Emoji）绘制富文本，支持自动流式排版折行。"""
    x_start, y = xy
    x = x_start
    line_h = 36

    for span in spans:
        # 拆分 emoji
        raw_tokens = EMOJI_PATTERN.split(span.text)
        
        font = f_h2 if span.span_type == "bold" else (f_code if span.span_type == "inline_code" else f_body)
        fill = c_bold if span.span_type == "bold" else (c_code_text if span.span_type == "inline_code" else c_text)

        for token in raw_tokens:
            if not token:
                continue

            if EMOJI_PATTERN.fullmatch(token):
                sprite = get_rendered_emoji_sprite(token, target_size=emoji_size)
                if sprite:
                    if x + emoji_size > max_w:
                        x = x_start
                        y += line_h
                    sprite_w, sprite_h = sprite.size
                    offset_y = y + max(0, (emoji_size - sprite_h) // 2)
                    canvas.paste(sprite, (x, offset_y), sprite)
                    x += sprite_w + 6
                    continue

            # 处理文字绘制
            for char in token:
                try:
                    bbox = font.getbbox(char)
                    char_w = bbox[2] - bbox[0]
                except Exception:
                    char_w = font.size // 2

                if x + char_w > max_w:
                    x = x_start
                    y += line_h

                if span.span_type == "inline_code":
                    draw.rounded_rectangle([x - 2, y + 2, x + char_w + 4, y + font.size + 4], radius=4, fill=(48, 54, 61, 255))

                draw.text((x, y), char, font=font, fill=fill)
                x += char_w + (4 if span.span_type == "inline_code" else 0)

    return y + line_h


def render_markdown_to_card_image(
    md_text: str,
    title: str = "DeepSeek Harness 交付报告",
    stats_meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """使用 Pillow 原生离线渲染 Retina 2x 超清深色卡片长图（完整 Markdown 语法渲染：加粗高亮、代码块高亮、表格、引用）。"""
    try:
        width = 1300
        padding = 48
        line_spacing = 10

        f_title, f_h1, f_h2, f_body, f_code, f_small, f_badge = _load_cjk_fonts()

        # 调色盘 (GitHub Dark 极客风)
        c_bg = (13, 17, 23)           # #0d1117
        c_card = (22, 27, 34)         # #161b22
        c_border = (48, 54, 61)       # #30363d
        c_text = (230, 237, 243)      # #e6edf3
        c_muted = (139, 148, 158)     # #8b949e
        c_primary = (88, 166, 255)    # #58a6ff
        c_bold = (255, 255, 255)      # 纯白醒目加粗
        c_code_bg = (13, 17, 23)      # #0d1117
        c_code_text = (126, 231, 135) # #7ee787 (绿色高亮)
        c_quote_bar = (56, 139, 253)  # #388bfd
        c_badge_bg = (33, 38, 45)     # #21262d

        raw_lines = md_text.split("\n")
        render_blocks: List[Dict[str, Any]] = []
        in_code = False
        code_buffer: List[str] = []
        code_lang = ""

        # 第一遍扫描：结构化提取 Markdown 语法块
        for rline in raw_lines:
            sline = rline.rstrip()

            if sline.startswith("```"):
                if not in_code:
                    in_code = True
                    code_lang = sline[3:].strip() or "CODE"
                    code_buffer = []
                else:
                    in_code = False
                    render_blocks.append({"type": "code_block", "lang": code_lang, "lines": list(code_buffer)})
                continue

            if in_code:
                code_buffer.append(sline)
                continue

            # Markdown 表格行 (例如 | a | b |)
            if sline.startswith("|") and sline.endswith("|"):
                # 忽略表头分割行 |---|---|
                if re.match(r"^\|[\s\-:|]+\|$", sline):
                    continue
                cells = [c.strip() for c in sline.strip("|").split("|")]
                render_blocks.append({"type": "table_row", "cells": cells})
                continue

            if sline.startswith("# "):
                render_blocks.append({"type": "h1", "spans": parse_inline_markdown(sline[2:].strip())})
            elif sline.startswith("## "):
                render_blocks.append({"type": "h2", "spans": parse_inline_markdown(sline[3:].strip())})
            elif sline.startswith("### "):
                render_blocks.append({"type": "h3", "spans": parse_inline_markdown(sline[4:].strip())})
            elif sline.startswith("> "):
                render_blocks.append({"type": "quote", "spans": parse_inline_markdown(sline[2:].strip())})
            elif sline.startswith("- ") or sline.startswith("* "):
                render_blocks.append({"type": "list", "spans": parse_inline_markdown(sline[2:].strip())})
            elif sline.strip() in ("---", "***"):
                render_blocks.append({"type": "divider"})
            elif not sline.strip():
                render_blocks.append({"type": "spacer"})
            else:
                render_blocks.append({"type": "paragraph", "spans": parse_inline_markdown(sline)})

        # 估算总高度
        y_cursor = padding + 90
        for block in render_blocks:
            btype = block["type"]
            if btype == "spacer":
                y_cursor += 16
            elif btype == "divider":
                y_cursor += 28
            elif btype in ("h1", "h2", "h3"):
                y_cursor += 50
            elif btype == "code_block":
                y_cursor += 40 + len(block["lines"]) * 32 + 20
            elif btype == "table_row":
                y_cursor += 42
            elif btype in ("quote", "list", "paragraph"):
                y_cursor += 40 + line_spacing

        stats_extra_h = 70 if stats_meta else 20
        total_h = max(y_cursor + padding + 60 + stats_extra_h, 450)

        # 创建 RGBA 画布
        img = Image.new("RGBA", (width, total_h), (*c_bg, 255))
        draw = ImageDraw.Draw(img)

        # 绘制主卡片底色与外发光边框
        draw.rounded_rectangle([padding // 2, padding // 2, width - padding // 2, total_h - padding // 2], radius=16, fill=(*c_card, 255), outline=(*c_border, 255), width=2)

        # 头部专属 Logo 徽标
        draw.text((padding + 12, padding + 12), "🐋 DS娘 x DeepSeek Harness", font=f_title, fill=c_primary)
        draw.text((width - padding - 180, padding + 20), "智能体执行交付", font=f_small, fill=c_muted)
        draw.line([padding + 12, padding + 66, width - padding - 12, padding + 66], fill=c_border, width=2)

        # 逐块进行富文本语法渲染
        y = padding + 90
        max_content_w = width - padding - 20

        for block in render_blocks:
            btype = block["type"]

            if btype == "spacer":
                y += 16
            elif btype == "divider":
                draw.line([padding + 12, y + 12, width - padding - 12, y + 12], fill=c_border, width=2)
                y += 28
            elif btype == "h1":
                y = draw_styled_markdown_line(img, draw, (padding + 12, y), block["spans"], f_h1, f_title, f_code, c_primary, c_bold, c_code_text, c_code_bg, max_content_w, emoji_size=30)
                y += 8
            elif btype in ("h2", "h3"):
                y = draw_styled_markdown_line(img, draw, (padding + 12, y), block["spans"], f_h2, f_h1, f_code, (121, 192, 255), c_bold, c_code_text, c_code_bg, max_content_w, emoji_size=26)
                y += 6
            elif btype == "code_block":
                # 绘制代码容器背景框
                lines = block["lines"]
                box_h = 36 + len(lines) * 32 + 16
                draw.rounded_rectangle([padding + 6, y, width - padding - 6, y + box_h], radius=8, fill=(13, 17, 23, 255), outline=c_border, width=1)
                draw.rounded_rectangle([padding + 6, y, width - padding - 6, y + 32], radius=8, fill=(30, 36, 44, 255))
                draw.text((padding + 18, y + 6), f"📄 {block['lang'].upper()}", font=f_small, fill=c_muted)
                
                # 绘制高亮代码行
                code_y = y + 42
                for cline in lines:
                    draw.text((padding + 22, code_y), cline, font=f_code, fill=c_code_text)
                    code_y += 32
                y += box_h + 14

            elif btype == "table_row":
                # 绘制表格单元格
                cells = block["cells"]
                col_w = (width - padding * 2 - 24) // max(len(cells), 1)
                draw.rectangle([padding + 6, y, width - padding - 6, y + 38], fill=(30, 36, 44, 180), outline=c_border, width=1)
                cx = padding + 16
                for cell in cells:
                    cell_spans = parse_inline_markdown(cell)
                    draw_styled_markdown_line(img, draw, (cx, y + 6), cell_spans, f_body, f_h2, f_code, c_text, c_bold, c_code_text, c_code_bg, cx + col_w - 10, emoji_size=20)
                    cx += col_w
                y += 40

            elif btype == "quote":
                draw.line([padding + 12, y, padding + 12, y + 34], fill=c_quote_bar, width=4)
                y = draw_styled_markdown_line(img, draw, (padding + 26, y), block["spans"], f_body, f_h2, f_code, c_muted, c_bold, c_code_text, c_code_bg, max_content_w, emoji_size=22)
            elif btype == "list":
                draw.text((padding + 16, y), "•", font=f_body, fill=c_primary)
                y = draw_styled_markdown_line(img, draw, (padding + 34, y), block["spans"], f_body, f_h2, f_code, c_text, c_bold, c_code_text, c_code_bg, max_content_w, emoji_size=22)
            else:
                y = draw_styled_markdown_line(img, draw, (padding + 12, y), block["spans"], f_body, f_h2, f_code, c_text, c_bold, c_code_text, c_code_bg, max_content_w, emoji_size=22)

        # 底部状态栏徽章
        footer_y = total_h - padding - 54
        if stats_meta:
            draw.line([padding + 12, footer_y - 12, width - padding - 12, footer_y - 12], fill=c_border, width=2)
            badges = []
            if stats_meta.get("model"):
                badges.append(f"⚡ {stats_meta['model']}")
            if stats_meta.get("elapsed"):
                badges.append(f"⏱️ 耗时 {stats_meta['elapsed']}")
            if stats_meta.get("mode"):
                badges.append(f"🔌 {stats_meta['mode'].upper()}")
            badges.append("🛡️ 沙盒全放行")
            badges.append("🧠 D老师模式")

            bx = padding + 12
            for badge_text in badges:
                bw = len(badge_text) * 16 + 28
                draw.rounded_rectangle([bx, footer_y, bx + bw, footer_y + 34], radius=17, fill=(*c_badge_bg, 255), outline=(*c_border, 255), width=1)
                draw.text((bx + 12, footer_y + 5), badge_text, font=f_badge, fill=c_primary if "⚡" in badge_text else c_text)
                bx += bw + 12

            footer_y += 42

        draw.text((padding + 12, total_h - padding + 10), "DeepSeek Harness Agent • Powered by MaiBot", font=f_small, fill=c_muted)

        rgb_img = img.convert("RGB")
        buf = io.BytesIO()
        rgb_img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    except Exception:
        return None
