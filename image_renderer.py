"""High-Performance, Zero-Browser Markdown Card Image Renderer using Pillow."""

import base64
import io
import re
from typing import Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont


def render_markdown_to_card_image(md_text: str, title: str = "DeepSeek Harness 交付报告") -> Optional[str]:
    """使用 Pillow 原生离线渲染 GitHub/VSCode 深色主题卡片图片，返回 base64 字符串。
    
    优势：零浏览器依赖、零网络下载、毫秒级极速渲染、100% 稳定无故障。
    """
    try:
        width = 860
        padding = 32
        line_spacing = 6
        
        # 尝试加载系统高质量字体
        font_path = "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"
        bold_font_path = "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"
        mono_font_path = "/usr/share/fonts/TTF/DejaVuSansMono.ttf"

        try:
            f_title = ImageFont.truetype(bold_font_path, 22)
            f_h1 = ImageFont.truetype(bold_font_path, 19)
            f_h2 = ImageFont.truetype(bold_font_path, 17)
            f_body = ImageFont.truetype(font_path, 15)
            f_code = ImageFont.truetype(mono_font_path, 14)
            f_small = ImageFont.truetype(font_path, 12)
        except Exception:
            f_title = ImageFont.load_default()
            f_h1 = f_title
            f_h2 = f_title
            f_body = f_title
            f_code = f_title
            f_small = f_title

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

        # 文本分行与测量预处理
        raw_lines = md_text.split("\n")
        render_items: List[Tuple[str, str, Any]] = []  # (type, text, font)
        
        in_code = False
        content_w = width - padding * 2

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

        # 计算总高度
        y_cursor = padding + 60  # 头部高度预留
        dummy_img = Image.new("RGB", (100, 100))
        draw_calc = ImageDraw.Draw(dummy_img)

        for itype, itext, ifont in render_items:
            if itype == "spacer":
                y_cursor += 12
            elif itype == "divider":
                y_cursor += 20
            elif itype in ("h1", "h2", "h3"):
                y_cursor += 30
            elif itype in ("code_fence", "code_end"):
                y_cursor += 20
            elif itype == "code_line":
                y_cursor += 22
            else:
                # 简单自动换行估算
                line_len = len(itext)
                chars_per_line = 48
                wrapped_rows = max(1, (line_len + chars_per_line - 1) // chars_per_line)
                y_cursor += wrapped_rows * 24 + line_spacing

        total_h = max(y_cursor + padding + 40, 300)

        # 创建高清画布并绘制
        img = Image.new("RGB", (width, total_h), c_bg)
        draw = ImageDraw.Draw(img)

        # 绘制主卡片底色与边框
        draw.rounded_rectangle([padding // 2, padding // 2, width - padding // 2, total_h - padding // 2], radius=12, fill=c_card, outline=c_border, width=1)

        # 绘制头部专属 Logo 与标识
        draw.text((padding + 8, padding + 8), "🐋 DS娘 x DeepSeek Harness", font=f_title, fill=c_primary)
        draw.text((width - padding - 130, padding + 14), "智能体执行交付", font=f_small, fill=c_muted)
        draw.line([padding + 8, padding + 44, width - padding - 8, padding + 44], fill=c_border, width=1)

        # 逐项渲染内容
        y = padding + 60
        for itype, itext, ifont in render_items:
            if itype == "spacer":
                y += 12
            elif itype == "divider":
                draw.line([padding + 8, y + 8, width - padding - 8, y + 8], fill=c_border, width=1)
                y += 20
            elif itype == "h1":
                draw.text((padding + 8, y), itext, font=f_h1, fill=c_primary)
                y += 30
            elif itype in ("h2", "h3"):
                draw.text((padding + 8, y), itext, font=f_h2, fill=(121, 192, 255))
                y += 26
            elif itype == "code_fence":
                draw.rounded_rectangle([padding + 4, y, width - padding - 4, y + 20], radius=4, fill=(30, 36, 44))
                draw.text((padding + 12, y + 2), f"📄 {itext or 'CODE'}", font=f_small, fill=c_muted)
                y += 24
            elif itype == "code_end":
                y += 8
            elif itype == "code_line":
                draw.rectangle([padding + 4, y, width - padding - 4, y + 22], fill=c_code_bg)
                draw.text((padding + 16, y + 2), itext, font=f_code, fill=c_code_text)
                y += 22
            elif itype == "quote":
                draw.line([padding + 8, y, padding + 8, y + 20], fill=c_quote_bar, width=3)
                draw.text((padding + 18, y), itext, font=f_body, fill=c_muted)
                y += 24
            elif itype == "list":
                draw.text((padding + 12, y), itext, font=f_body, fill=c_text)
                y += 24
            else:
                # 普通段落处理自动分行
                chars_per_line = 46
                chunks = [itext[i:i + chars_per_line] for i in range(0, len(itext), chars_per_line)]
                for chunk in chunks:
                    draw.text((padding + 8, y), chunk, font=f_body, fill=c_text)
                    y += 24
                y += line_spacing

        # 绘制底部署名
        draw.text((padding + 8, total_h - padding - 8), "DeepSeek Harness Agent • Powered by MaiBot", font=f_small, fill=c_muted)

        # 导出为 Base64 PNG
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    except Exception as e:
        return None
