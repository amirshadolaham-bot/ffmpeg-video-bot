#!/usr/bin/env python3
"""Video effects - Watermark, Subtitle Intro, Hardsub"""

import os
import asyncio
import logging
from typing import Callable, Tuple

from bot.ffmpeg.core import FFmpeg, run_ffmpeg_command

LOGGER = logging.getLogger(__name__)

# ---- Text watermark look (edit these lines to change the style) ----
# Text 1: the main text. Small, black on a white box, slightly bold.
WM_FONT_DIVISOR = 45          # font size = shorter side of video / this number (bigger number = smaller text)
WM_MIN_FONT = 14              # never smaller than this many pixels (unless the text would not fit the width)
WM_TEXT_COLOR = "black"       # text color
WM_BOX_COLOR = "white@1.0"    # box behind text: color@opacity
WM_BOX_PADDING = 6            # space between text and box edge in pixels
WM_BOLD = True                # True = bold font for text 1
WM_POS_TEXT = "top_left"      # where text 1 goes when you send two texts with |
# Text 2: the ID. Plain white text, no box, not bold.
WM_ID_COLOR = "white"
WM_ID_DIVISOR = 45
WM_POS_ID = "bottom_right"    # where text 2 goes when you send two texts with |
# ----------------------------------------------------------------------


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
    
    # Position mapping
    positions = {
        'top_left': 'x=12:y=12',
        'top_center': 'x=(w-text_w)/2:y=12',
        'top_right': 'x=w-text_w-12:y=12',
        'middle_left': 'x=12:y=(h-text_h)/2',
        'center': 'x=(w-text_w)/2:y=(h-text_h)/2',
        'middle_right': 'x=w-text_w-12:y=(h-text_h)/2',
        'bottom_left': 'x=12:y=h-text_h-12',
        'bottom_center': 'x=(w-text_w)/2:y=h-text_h-12',
        'bottom_right': 'x=w-text_w-12:y=h-text_h-12',
    }
    
    def _escape(t: str) -> str:
        return t.replace("\\", "\\\\").replace("'", "\u2019").replace(":", "\\:").replace("%", "\\%")

    color = font_color or WM_TEXT_COLOR

    bold_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    use_bold = WM_BOLD and os.path.exists(bold_font)

    def _fontsize(t: str, divisor: int, char_w: float) -> str:
        if font_size:
            return str(font_size)
        # Wanted size, but never wider than the video (longest line decides)
        n = max([len(line) for line in t.split("\n")] + [1])
        return f"'min(max({WM_MIN_FONT},min(w,h)/{divisor}),w*0.9/({char_w}*{n}))'"

    def _boxed(t: str, pos_str: str) -> str:
        font = f"fontfile={bold_font}:" if use_bold else ""
        return (
            f"drawtext=text='{_escape(t)}':"
            f"{font}"
            f"{pos_str}:"
            f"fontsize={_fontsize(t, WM_FONT_DIVISOR, 0.65 if use_bold else 0.6)}:"
            f"fontcolor={color}@{opacity}:"
            f"box=1:boxcolor={WM_BOX_COLOR}:boxborderw={WM_BOX_PADDING}"
        )

    def _plain(t: str, pos_str: str) -> str:
        return (
            f"drawtext=text='{_escape(t)}':"
            f"{pos_str}:"
            f"fontsize={_fontsize(t, WM_ID_DIVISOR, 0.6)}:"
            f"fontcolor={WM_ID_COLOR}@{opacity}:"
            f"borderw=1:bordercolor=black@0.7"
        )

    # Text with "|" -> two watermarks in one pass: text 1 (boxed) and text 2 (ID, plain)
    parts = [x.strip() for x in text.split('|') if x.strip()]
    if len(parts) >= 2:
        drawtext = ",".join([
            _boxed(parts[0], positions[WM_POS_TEXT]),
            _plain(parts[1], positions[WM_POS_ID]),
        ])
    else:
        pos = positions.get(position, positions['bottom_right'])
        drawtext = _boxed(parts[0] if parts else text, pos)
    
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
