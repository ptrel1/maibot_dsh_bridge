"""High-Performance, Retina HD Markdown Card Image Renderer using Pillow with True CJK Fonts."""

import base64
import io
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


def _load_cjk_fonts() -> Tuple[Any, Any, Any, Any, Any, Any, Any]:
    """智能查找系统中可用的真实中文字体（避免回退到默认点阵字体导致方块口口口）。"""
    font_candidates = [
        "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
        "/usr/local/share/fonts/华/华康圆体W7.ttf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/adobe-source-han-sans/SourceHanSansCN-Regular.otf",
    ]
    
    valid_font = None
    for cand in font_candidates:
        if os.path.exists(cand):
            valid_font = cand
            break

    # 视网膜 Retina 2x 高清字号
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


def render_markdown_to_card_image(
    md_text: str,
    title: str = "DeepSeek Harness 交付报告",
    stats_meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """使用 Pillow 原生离线渲染 Retina 2x 超清 GitHub/VSCode 深色卡片长图。"""
    try:
        # Retina 2x 画布宽度（1300px 宽度，超高清呈现）
        width = 1300
        padding = 48
        line_spacing = 10

        f_title, f_h1, f_h2, f_body, f_code, f_small, f_badge = _load_cjk_fonts()

        # 调色盘 (GitHub Dark 风格)
        c_bg = (13, 17, 23)           # #0d1117
        c_card = (22, 27, 34)         # #161b22
        c_border = (48, 54, 61)       # #30363d
        c_text = (230, 237, 243)      # #e6edf3
        c_muted = (139, 148, 158)     # #8b949e
        c_primary = (88, 166, 255)    # #58a6ff
        c_code_bg = (13, 17, 23)      # #0d1117
        c_code_text = (126, 231, 135) # #7ee787
        c_quote_bar = (56, 139, 253)  # #388bfd
        c_badge_bg = (33, 38, 45)     # #21262d

        # 文本解析
        raw_lines = md_text.split("\n")
        render_items: List[Tuple[str, str, Any]] = []
        in_code = False

        for rline in raw_lines:
            sline = rline.rstrip()
            if sline.startswith("```"):
                in_code = not in_code
                render_items.append(("code_fence" if in_code else "code_end", sline[3:].strip(), f_small))
                continue

            if in_code:
                render_items.append(("code_line", sline, f_code))
                continue

            if sline.startswith("# "):
                render_items.append(("h1", sline[2:].strip(), f_h1))
            elif sline.startswith("## "):
                render_items.append(("h2", sline[3:].strip(), f_h2))
            elif sline.startswith("### "):
                render_items.append(("h3", sline[4:].strip(), f_h2))
            elif sline.startswith("> "):
                render_items.append(("quote", sline[2:].strip(), f_body))
            elif sline.startswith("- ") or sline.startswith("* "):
                render_items.append(("list", "• " + sline[2:].strip(), f_body))
            elif sline.strip() == "---" or sline.strip() == "***":
                render_items.append(("divider", "", f_body))
            elif not sline.strip():
                render_items.append(("spacer", "", f_body))
            else:
                render_items.append(("text", sline.strip(), f_body))

        # 计算总高度 (2x 比例计算)
        y_cursor = padding + 90
        for itype, itext, ifont in render_items:
            if itype == "spacer":
                y_cursor += 16
            elif itype == "divider":
                y_cursor += 28
            elif itype in ("h1", "h2", "h3"):
                y_cursor += 46
            elif itype in ("code_fence", "code_end"):
                y_cursor += 30
            elif itype == "code_line":
                y_cursor += 34
            else:
                line_len = len(itext)
                chars_per_line = 50
                wrapped_rows = max(1, (line_len + chars_per_line - 1) // chars_per_line)
                y_cursor += wrapped_rows * 36 + line_spacing

        stats_extra_h = 70 if stats_meta else 20
        total_h = max(y_cursor + padding + 60 + stats_extra_h, 450)

        # 创建 2x 高清画布
        img = Image.new("RGB", (width, total_h), c_bg)
        draw = ImageDraw.Draw(img)

        # 绘制主卡片底色与边框
        draw.rounded_rectangle([padding // 2, padding // 2, width - padding // 2, total_h - padding // 2], radius=16, fill=c_card, outline=c_border, width=2)

        # 头部专属 Logo
        draw.text((padding + 12, padding + 12), "🐋 DS娘 x DeepSeek Harness", font=f_title, fill=c_primary)
        draw.text((width - padding - 180, padding + 20), "智能体执行交付", font=f_small, fill=c_muted)
        draw.line([padding + 12, padding + 66, width - padding - 12, padding + 66], fill=c_border, width=2)

        # 逐项渲染正文
        y = padding + 90
        for itype, itext, ifont in render_items:
            if itype == "spacer":
                y += 16
            elif itype == "divider":
                draw.line([padding + 12, y + 12, width - padding - 12, y + 12], fill=c_border, width=2)
                y += 28
            elif itype == "h1":
                draw.text((padding + 12, y), itext, font=f_h1, fill=c_primary)
                y += 46
            elif itype in ("h2", "h3"):
                draw.text((padding + 12, y), itext, font=f_h2, fill=(121, 192, 255))
                y += 40
            elif itype == "code_fence":
                draw.rounded_rectangle([padding + 6, y, width - padding - 6, y + 30], radius=6, fill=(30, 36, 44))
                draw.text((padding + 18, y + 4), f"📄 {itext or 'CODE'}", font=f_small, fill=c_muted)
                y += 36
            elif itype == "code_end":
                y += 12
            elif itype == "code_line":
                draw.rectangle([padding + 6, y, width - padding - 6, y + 34], fill=c_code_bg)
                draw.text((padding + 22, y + 4), itext, font=f_code, fill=c_code_text)
                y += 34
            elif itype == "quote":
                draw.line([padding + 12, y, padding + 12, y + 30], fill=c_quote_bar, width=4)
                draw.text((padding + 26, y), itext, font=f_body, fill=c_muted)
                y += 36
            elif itype == "list":
                draw.text((padding + 16, y), itext, font=f_body, fill=c_text)
                y += 36
            else:
                chars_per_line = 50
                chunks = [itext[i:i + chars_per_line] for i in range(0, len(itext), chars_per_line)]
                for chunk in chunks:
                    draw.text((padding + 12, y), chunk, font=f_body, fill=c_text)
                    y += 36
                y += line_spacing

        # 底部状态栏（Retina 2x 徽章）
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
                bw = len(badge_text) * 16 + 24
                draw.rounded_rectangle([bx, footer_y, bx + bw, footer_y + 34], radius=17, fill=c_badge_bg, outline=c_border, width=1)
                draw.text((bx + 12, footer_y + 5), badge_text, font=f_badge, fill=c_primary if "⚡" in badge_text else c_text)
                bx += bw + 12

            footer_y += 42

        # 底部署名
        draw.text((padding + 12, total_h - padding + 10), "DeepSeek Harness Agent • Powered by MaiBot", font=f_small, fill=c_muted)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    except Exception:
        return None
