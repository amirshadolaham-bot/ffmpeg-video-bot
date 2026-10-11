#!/usr/bin/env python3
"""Video effects - Watermark, Subtitle Intro, Hardsub"""

import os
import asyncio
import logging
from typing import Callable, Tuple

from bot.ffmpeg.core import FFmpeg, run_ffmpeg_command

LOGGER = logging.getLogger(__name__)

# ---- Text watermark look (edit these lines to change the style) ----
# Text 1: the main text. Same look as the Premiere design:
#   Vazirmatn Bold, black text with a thin white outline, on a white box (75% opaque), stuck to the corner.
WM_FONT_RATIO = 0.0272        # font size as a fraction of the video's SHORTER side (bigger = bigger text)
WM_BAR_RATIO = 0.0364           # height of the white bar as a fraction of the shorter side
WM_STROKE_RATIO = 0.167       # outline thickness as a fraction of the font size (0 = no outline)
WM_MIN_FONT = 10              # never smaller than this many pixels
WM_TEXT_COLOR = "black"       # text color
WM_STROKE_COLOR = "white"     # outline color
WM_BOX_COLOR = "white@1.0"   # box behind text: color@opacity
WM_MARGIN = 0                 # distance of the bar from the video edge, in pixels (0 = touching the corner)
WM_BOLD = True                # True = use the bold font
WM_LINE_EM = 1.12             # (technical) height of one text line in font sizes, for Vazirmatn
WM_CHAR_EM = 0.49             # (technical) average width of one character in font sizes, for Vazirmatn
WM_POS_TEXT = "top_left"      # where text 1 goes when you send two texts with |
# Text 2: the ID. Plain white text, no box, not bold.
WM_ID_COLOR = "white"
WM_ID_DIVISOR = 43
WM_POS_ID = "bottom_right"    # where text 2 goes when you send two texts with |

# "Cover mode" (used only when you send two texts with |):
#   text 1 -> full-width white bar at the top of the video, text centered inside it
#   text 2 -> bottom-left, on a blurred + slightly darkened patch
WM_COVER_MODE = False
WM_TOPBAR_Y = 0.0             # top of the white bar, as a fraction of video height (0 = very top)
WM_TOPBAR_H = 0.085           # height of the white bar, as a fraction of video height
WM_PATCH_W = 0.26             # width of the bottom-left blurred patch, as a fraction of video width
WM_PATCH_H = 0.085            # height of the blurred patch, as a fraction of video height
WM_PATCH_MARGIN = 0.012       # distance of the patch from the bottom edge (fraction of height)
WM_PATCH_BLUR = 10            # blur strength (bigger = more blurred)
WM_PATCH_DARK = 0.30          # 0 = no darkening, 0.5 = darker patch (helps white text stay readable)
# ----------------------------------------------------------------------

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WM_FONT_FILE = os.path.join(_BASE_DIR, "fonts", "Vazirmatn-Bold.ttf")
WM_FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


async def _probe_size(path: str):
    """Return (width, height) of the first video stream, or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        w, h = out.decode().strip().split('\n')[0].split('x')[:2]
        return int(w), int(h)
    except Exception:
        return None


async def add_image_watermark(
    input_file: str,
    watermark_image: str,
    output: str,
    position: str = 'bottom_right',
    opacity: float = 0.7,
    scale: float = 0.15,
    progress_callback: Callable = None,
    duration: float = None
) -> Tuple[bool, str]:
    """Add image watermark to video"""
    
    # Position mapping (x:y)
    positions = {
        'top_left': '10:10',
        'top_center': '(W-w)/2:10',
        'top_right': 'W-w-10:10',
        'middle_left': '10:(H-h)/2',
        'center': '(W-w)/2:(H-h)/2',
        'middle_right': 'W-w-10:(H-h)/2',
        'bottom_left': '10:H-h-10',
        'bottom_center': '(W-w)/2:H-h-10',
        'bottom_right': 'W-w-10:H-h-10',
    }
    
    pos = positions.get(position, 'W-w-10:H-h-10')
    
    # Scale watermark relative to video
    filter_complex = (
        f"[1:v]scale=iw*{scale}:-1,format=rgba,"
        f"colorchannelmixer=aa={opacity}[wm];"
        f"[0:v][wm]overlay={pos}"
    )
    
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', input_file,
        '-i', watermark_image,
        '-filter_complex', filter_complex,
        '-c:a', 'copy',
        output
    ]
    
    success, result = await run_ffmpeg_command(cmd, progress_callback, duration)
    return success, result if not success else output


async def add_text_watermark(
    input_file: str,
    text: str,
    output: str,
    position: str = 'bottom_right',
    font_size: int = 0,
    font_color: str = '',
    opacity: float = 1.0,
    progress_callback: Callable = None,
    duration: float = None
) -> Tuple[bool, str]:
    """Add text watermark to video"""
    
    # Position of a box/text inside the video, `m` pixels away from the edges
    def _pos(key: str, m: int) -> str:
        table = {
            'top_left': f'x={m}:y={m}',
            'top_center': f'x=(w-text_w)/2:y={m}',
            'top_right': f'x=w-text_w-{m}:y={m}',
            'middle_left': f'x={m}:y=(h-text_h)/2',
            'center': 'x=(w-text_w)/2:y=(h-text_h)/2',
            'middle_right': f'x=w-text_w-{m}:y=(h-text_h)/2',
            'bottom_left': f'x={m}:y=h-text_h-{m}',
            'bottom_center': f'x=(w-text_w)/2:y=h-text_h-{m}',
            'bottom_right': f'x=w-text_w-{m}:y=h-text_h-{m}',
        }
        return table.get(key, table['bottom_right'])

    def _rtl(t: str) -> str:
        """If the text has Persian/Arabic letters, make the whole line read right-to-left,
        so the first word is on the right and an English ID typed after it ends up on the left."""
        if not any('\u0600' <= ch <= '\u06ff' or '\u0750' <= ch <= '\u077f' or '\ufb50' <= ch <= '\ufdff' or '\ufe70' <= ch <= '\ufeff' for ch in t):
            return t
        import re
        # Keep every English token (like @my_id or t.me/chan) in its own left-to-right box,
        # so the "@" stays at the start of the ID.
        latin = re.compile(r'[@#A-Za-z0-9][@#A-Za-z0-9_.\-/]*')
        def _line(line: str) -> str:
            line = latin.sub(lambda m: "\u202a" + m.group(0) + "\u202c", line.strip())
            return "\u202b" + line + "\u202c"
        return "\n".join(_line(line) for line in t.split("\n"))

    def _escape(t: str) -> str:
        t = _rtl(t)
        return t.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")

    color = font_color or WM_TEXT_COLOR

    bold_font = WM_FONT_FILE if os.path.exists(WM_FONT_FILE) else (WM_FONT_FALLBACK if os.path.exists(WM_FONT_FALLBACK) else None)
    use_bold = WM_BOLD and bold_font is not None
    dims = await _probe_size(input_file)   # (width, height) or None

    def _fontsize(t: str, divisor: int, char_w: float) -> str:
        if font_size:
            return str(font_size)
        # Wanted size, but never wider than the video (longest line decides)
        n = max([len(line) for line in t.split("\n")] + [1])
        return f"'min(max({WM_MIN_FONT},min(w,h)/{divisor}),w*0.9/({char_w}*{n}))'"

    def _boxed(t: str, key: str) -> str:
        """Text 1: Vazirmatn Bold, black, thin white outline, white 75% box, sizes relative to the video."""
        n = max([len(line) for line in t.split("\n")] + [1])
        font = f"fontfile={bold_font}:" if use_bold else ""
        if dims:
            vw, vh = dims
            short = min(vw, vh)
            fs = float(font_size) if font_size else max(WM_MIN_FONT, short * WM_FONT_RATIO)
            if not font_size:
                # shrink long texts so the bar never goes outside the video
                fs = min(fs, (vw - 2 * WM_MARGIN) * 0.96 / (WM_CHAR_EM * n + 0.6))
            fs = max(8, int(round(fs)))
            pad = max(0, int(round((short * WM_BAR_RATIO - fs * WM_LINE_EM) / 2)))
            stroke = int(round(fs * WM_STROKE_RATIO))
            size_part = f"fontsize={fs}:"
        else:
            pad, stroke = 4, 2
            size_part = f"fontsize='{font_size or 'min(w,h)*' + str(WM_FONT_RATIO)}':"
        outline = f"borderw={stroke}:bordercolor={WM_STROKE_COLOR}:" if stroke > 0 else ""
        return (
            f"drawtext=text='{_escape(t)}':"
            f"{font}"
            f"{_pos(key, WM_MARGIN + pad)}:"
            f"{size_part}"
            f"fontcolor={color}@{opacity}:"
            f"{outline}"
            f"box=1:boxcolor={WM_BOX_COLOR}:boxborderw={pad}"
        )

    def _plain(t: str, key: str) -> str:
        return (
            f"drawtext=text='{_escape(t)}':"
            f"{_pos(key, 12)}:"
            f"fontsize={_fontsize(t, WM_ID_DIVISOR, 0.6)}:"
            f"fontcolor={WM_ID_COLOR}@{opacity}:"
            f"borderw=2:bordercolor=black@0.95:shadowcolor=black@0.6:shadowx=1:shadowy=1"
        )

    # Text with "|" -> two watermarks in one pass: text 1 (boxed) and text 2 (ID, plain)
    # Text with "|" -> two boxed tags: text 1 top-left, text 2 bottom-left (or bottom-right via the Position menu)
    parts = [x.strip() for x in text.split('|') if x.strip()]

    if len(parts) >= 2 and WM_COVER_MODE:
        n1 = max([len(l) for l in parts[0].split("\n")] + [1])
        n2 = max([len(l) for l in parts[1].split("\n")] + [1])
        bold = f"fontfile={bold_font}:" if use_bold else ""
        graph = (
            "[0:v]split=2[a][b];"
            f"[b]crop=w=iw*{WM_PATCH_W}:h=ih*{WM_PATCH_H}:x=0:y=ih-ih*{WM_PATCH_H}-ih*{WM_PATCH_MARGIN},"
            f"boxblur=luma_radius={WM_PATCH_BLUR}:luma_power=3:chroma_radius={WM_PATCH_BLUR}:chroma_power=3,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{WM_PATCH_DARK}:t=fill[patch];"
            f"[a][patch]overlay=x=0:y=H-h-H*{WM_PATCH_MARGIN},"
            f"drawbox=x=0:y=ih*{WM_TOPBAR_Y}:w=iw:h=ih*{WM_TOPBAR_H}:color=white@1.0:t=fill,"
            f"drawtext=text='{_escape(parts[0])}':{bold}"
            f"x=(w-text_w)/2:y=h*{WM_TOPBAR_Y}+(h*{WM_TOPBAR_H}-text_h)/2:"
            f"fontsize='min(h*{WM_TOPBAR_H}*0.5,w*0.92/({0.65 if use_bold else 0.6}*{n1}))':"
            f"fontcolor={color}@1.0,"
            f"drawtext=text='{_escape(parts[1])}':"
            f"x=(w*{WM_PATCH_W}-text_w)/2:y=h-h*{WM_PATCH_MARGIN}-h*{WM_PATCH_H}/2-text_h/2:"
            f"fontsize='min(h*{WM_PATCH_H}*0.4,w*{WM_PATCH_W}*0.9/(0.6*{n2}))':"
            f"fontcolor={WM_ID_COLOR}@1.0:borderw=1:bordercolor=black@0.7[v]"
        )
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-nostats',
            '-i', input_file,
            '-filter_complex', graph,
            '-map', '[v]', '-map', '0:a?',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
            '-c:a', 'copy',
            output
        ]
        success, result = await run_ffmpeg_command(cmd, progress_callback, duration)
        return success, result if not success else output

    if len(parts) >= 2:
        pos2 = position if position in ('bottom_left', 'bottom_right') else 'bottom_left'
        drawtext = ",".join([
            _boxed(parts[0], 'top_left'),
            _plain(parts[1], pos2),
        ])
    else:
        drawtext = _boxed(parts[0] if parts else text, 'top_left')

    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-nostats',
        '-i', input_file,
        '-vf', drawtext,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
        '-c:a', 'copy',
        output
    ]
    
    success, result = await run_ffmpeg_command(cmd, progress_callback, duration)
    return success, result if not success else output


async def burn_subtitles(
    input_file: str,
    subtitle_file: str,
    output: str,
    progress_callback: Callable = None,
    duration: float = None
) -> Tuple[bool, str]:
    """Burn subtitles into video (hardsub)"""
    
    ffmpeg = FFmpeg(input_file, output)
    
    # Handle different subtitle formats
    sub_ext = os.path.splitext(subtitle_file)[1].lower()
    
    # Escape file path for filter
    escaped_sub = subtitle_file.replace('\\', '/').replace(':', '\\:')
    
    if sub_ext in ['.ass', '.ssa']:
        subtitle_filter = f"ass='{escaped_sub}'"
    else:
        subtitle_filter = f"subtitles='{escaped_sub}'"
    
    cmd = [
        '-vf', subtitle_filter,
        '-c:v', 'libx264',
        '-crf', '23',
        '-preset', 'medium',
        '-c:a', 'copy'
    ]
    
    success, error = await ffmpeg.run_ffmpeg(cmd, progress_callback, duration)
    
    if not success:
        return False, error
    
    return True, output


async def burn_embedded_subtitles(
    input_file: str,
    output: str,
    subtitle_index: int = 0,
    progress_callback: Callable = None,
    duration: float = None
) -> Tuple[bool, str]:
    """Burn embedded subtitles from video itself"""
    
    ffmpeg = FFmpeg(input_file, output)
    
    cmd = [
        '-filter_complex', f"[0:v][0:s:{subtitle_index}]overlay",
        '-c:v', 'libx264',
        '-crf', '23',
        '-preset', 'medium',
        '-c:a', 'copy'
    ]
    
    success, error = await ffmpeg.run_ffmpeg(cmd, progress_callback, duration)
    
    if not success:
        return False, error
    
    return True, output


async def add_subtitle_intro(
    input_file: str,
    output: str,
    intro_text: str,
    duration: float = 3.0,
    font_size: int = 48,
    font_color: str = 'white',
    progress_callback: Callable = None,
    video_duration: float = None
) -> Tuple[bool, str]:
    """Add text intro at the beginning of video"""
    
    escaped_text = intro_text.replace("'", "\\'").replace(":", "\\:")
    
    # Show intro text for specified duration, then hide
    drawtext = (
        f"drawtext=text='{escaped_text}':"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"fontsize={font_size}:"
        f"fontcolor={font_color}:"
        f"shadowcolor=black@0.5:shadowx=2:shadowy=2:"
        f"enable='lt(t,{duration})'"
    )
    
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', input_file,
        '-vf', drawtext,
        '-c:a', 'copy',
        output
    ]
    
    success, result = await run_ffmpeg_command(cmd, progress_callback, video_duration)
    return success, result if not success else output


async def add_video_overlay(
    main_video: str,
    overlay_video: str,
    output: str,
    position: str = 'bottom_right',
    scale: float = 0.25,
    progress_callback: Callable = None,
    duration: float = None
) -> Tuple[bool, str]:
    """Add picture-in-picture video overlay"""
    
    positions = {
        'top_left': '10:10',
        'top_right': 'W-w-10:10',
        'bottom_left': '10:H-h-10',
        'bottom_right': 'W-w-10:H-h-10',
    }
    
    pos = positions.get(position, 'W-w-10:H-h-10')
    
    filter_complex = (
        f"[1:v]scale=iw*{scale}:-1[pip];"
        f"[0:v][pip]overlay={pos}"
    )
    
    cmd = [
        'ffmpeg', '-y', '-hide_banner',
        '-i', main_video,
        '-i', overlay_video,
        '-filter_complex', filter_complex,
        '-c:a', 'copy',
        output
    ]
    
    success, result = await run_ffmpeg_command(cmd, progress_callback, duration)
    return success, result if not success else output
