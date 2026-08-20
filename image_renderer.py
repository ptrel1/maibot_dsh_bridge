"""High-Performance, Retina HD Markdown Card Image Renderer with Bundled DFPYuanW7 (华康圆体) & True Color Emoji Engine."""

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
                    # NotoColorEmoji 必须以 109 原始尺寸加载 CBDT/CBLC 位图
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


def draw_mixed_cjk_emoji_text(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: Tuple[int, int, int],
    emoji_size: int = 24,
) -> int:
    """在指定坐标混合绘制华康圆体中文与原生彩色 Emoji。返回绘制的文本总宽度。"""
    x, y = xy
    # 拆分普通文本与 Emoji 片段
    tokens = EMOJI_PATTERN.split(text)
    
    for token in tokens:
        if not token:
            continue
        
        # 判定是否为 Emoji
        if EMOJI_PATTERN.fullmatch(token):
            sprite = get_rendered_emoji_sprite(token, target_size=emoji_size)
            if sprite:
                # 垂直居中贴图
                sprite_w, sprite_h = sprite.size
                offset_y = y + max(0, (emoji_size - sprite_h) // 2)
                canvas.paste(sprite, (x, offset_y), sprite)
                x += sprite_w + 4
                continue

        # 普通 CJK / 英文绘制
        draw.text((x, y), token, font=font, fill=fill)
        try:
            bbox = font.getbbox(token)
            token_w = bbox[2] - bbox[0]
        except Exception:
            token_w = len(token) * (font.size // 2)
        x += token_w

    return x - xy[0]


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


def render_markdown_to_card_image(
    md_text: str,
    title: str = "DeepSeek Harness 交付报告",
    stats_meta: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """使用 Pillow 原生离线渲染 Retina 2x 超清深色卡片长图（华康圆体 + 完整彩色 Emoji 混合排版）。"""
    try:
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

        # 计算总高度
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

        # 创建 RGBA 画布以便高精度 alpha 混合 Emoji 贴图
        img = Image.new("RGBA", (width, total_h), (*c_bg, 255))
        draw = ImageDraw.Draw(img)

        # 绘制主卡片底色与边框
        draw.rounded_rectangle([padding // 2, padding // 2, width - padding // 2, total_h - padding // 2], radius=16, fill=(*c_card, 255), outline=(*c_border, 255), width=2)

        # 头部专属 Logo（带彩色 🐋 鲸鱼 Emoji）
        draw_mixed_cjk_emoji_text(img, draw, (padding + 12, padding + 12), "🐋 DS娘 x DeepSeek Harness", f_title, c_primary, emoji_size=36)
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
                draw_mixed_cjk_emoji_text(img, draw, (padding + 12, y), itext, f_h1, c_primary, emoji_size=30)
                y += 46
            elif itype in ("h2", "h3"):
                draw_mixed_cjk_emoji_text(img, draw, (padding + 12, y), itext, f_h2, (121, 192, 255), emoji_size=26)
                y += 40
            elif itype == "code_fence":
                draw.rounded_rectangle([padding + 6, y, width - padding - 6, y + 30], radius=6, fill=(30, 36, 44, 255))
                draw_mixed_cjk_emoji_text(img, draw, (padding + 18, y + 4), f"📄 {itext or 'CODE'}", f_small, c_muted, emoji_size=18)
                y += 36
            elif itype == "code_end":
                y += 12
            elif itype == "code_line":
                draw.rectangle([padding + 6, y, width - padding - 6, y + 34], fill=(*c_code_bg, 255))
                draw.text((padding + 22, y + 4), itext, font=f_code, fill=c_code_text)
                y += 34
            elif itype == "quote":
                draw.line([padding + 12, y, padding + 12, y + 30], fill=c_quote_bar, width=4)
                draw_mixed_cjk_emoji_text(img, draw, (padding + 26, y), itext, f_body, c_muted, emoji_size=22)
                y += 36
            elif itype == "list":
                draw_mixed_cjk_emoji_text(img, draw, (padding + 16, y), itext, f_body, c_text, emoji_size=22)
                y += 36
            else:
                chars_per_line = 50
                chunks = [itext[i:i + chars_per_line] for i in range(0, len(itext), chars_per_line)]
                for chunk in chunks:
                    draw_mixed_cjk_emoji_text(img, draw, (padding + 12, y), chunk, f_body, c_text, emoji_size=22)
                    y += 36
                y += line_spacing

        # 底部状态栏（带彩色 ⚡ ⏱️ 🛡️ 徽章）
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
                draw_mixed_cjk_emoji_text(img, draw, (bx + 12, footer_y + 5), badge_text, f_badge, c_primary if "⚡" in badge_text else c_text, emoji_size=18)
                bx += bw + 12

            footer_y += 42

        # 底部署名
        draw.text((padding + 12, total_h - padding + 10), "DeepSeek Harness Agent • Powered by MaiBot", font=f_small, fill=c_muted)

        # 转为 RGB PNG 导出
        rgb_img = img.convert("RGB")
        buf = io.BytesIO()
        rgb_img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    except Exception:
        return None
