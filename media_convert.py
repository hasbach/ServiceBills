"""Media conversions for the WhatsApp inbox.

- Voice: admin-recorded audio (webm/opus from Chrome/Firefox, mp4/aac from
  Safari) -> OGG/Opus, the format WhatsApp plays as a voice note; inbound
  voice notes -> mp3 so Safari/iOS admins can play them back.
- Stickers: any image -> 512x512 transparent webp <= 100 KB.

ffmpeg is a system binary (installed in the Dockerfile). Always invoked with
an argument list (never a shell), a timeout, and private temp files.
"""
import io
import os
import shutil
import subprocess
import tempfile

from PIL import Image

VOICE_MAX_BYTES = 16 * 1024 * 1024
STICKER_SOURCE_MAX_BYTES = 5 * 1024 * 1024
STICKER_SIZE = 512
STICKER_STATIC_MAX = 100 * 1024
STICKER_ANIMATED_MAX = 500 * 1024
FFMPEG_TIMEOUT_SECONDS = 60


class ConversionError(Exception):
    pass


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _ffmpeg(data, out_suffix, args):
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ConversionError("ffmpeg is not installed on this server")
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "input.bin")
        dst = os.path.join(tmp, "output" + out_suffix)
        with open(src, "wb") as f:
            f.write(data)
        try:
            result = subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", src, *args, dst],
                capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise ConversionError("ffmpeg timed out")
        if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) == 0:
            raise ConversionError(result.stderr.decode("utf-8", errors="replace")[-300:] or "conversion failed")
        with open(dst, "rb") as f:
            return f.read()


def to_ogg_opus(data):
    return _ffmpeg(data, ".ogg", ["-vn", "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000"])


def to_mp3(data):
    return _ffmpeg(data, ".mp3", ["-vn", "-c:a", "libmp3lame", "-b:a", "64k"])


def to_sticker_webp(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ConversionError("file is not a readable image")
    animated = bool(getattr(img, "is_animated", False))
    if img.format == "WEBP" and img.size == (STICKER_SIZE, STICKER_SIZE):
        limit = STICKER_ANIMATED_MAX if animated else STICKER_STATIC_MAX
        if len(data) <= limit:
            return data
    if animated:
        raise ConversionError("animated stickers must already be 512x512 webp under 500 KB")
    img = img.convert("RGBA")
    img.thumbnail((STICKER_SIZE, STICKER_SIZE), Image.LANCZOS)
    canvas = Image.new("RGBA", (STICKER_SIZE, STICKER_SIZE), (0, 0, 0, 0))
    canvas.paste(img, ((STICKER_SIZE - img.width) // 2, (STICKER_SIZE - img.height) // 2), img)
    for quality in (90, 80, 70, 60, 50, 40, 30):
        buf = io.BytesIO()
        canvas.save(buf, "WEBP", quality=quality, method=6)
        out = buf.getvalue()
        if len(out) <= STICKER_STATIC_MAX:
            return out
    raise ConversionError("sticker is still over 100 KB after compression")
