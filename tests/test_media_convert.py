import io
import math
import struct
import wave

import pytest
from PIL import Image

import media_convert

needs_ffmpeg = pytest.mark.skipif(not media_convert.ffmpeg_available(), reason="ffmpeg not installed")


def _png(w, h, color=(255, 0, 0, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def _wav_bytes(seconds=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(16000 * seconds))
        w.writeframes(frames)
    return buf.getvalue()


def test_png_becomes_512_webp_under_100kb():
    out = media_convert.to_sticker_webp(_png(800, 400))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP"
    assert img.size == (512, 512)
    assert len(out) <= 100 * 1024


def test_ready_webp_passes_through_unchanged():
    buf = io.BytesIO()
    Image.new("RGBA", (512, 512), (0, 255, 0, 255)).save(buf, "WEBP")
    data = buf.getvalue()
    assert media_convert.to_sticker_webp(data) == data


def test_non_image_raises():
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_sticker_webp(b"not an image")


@needs_ffmpeg
def test_to_ogg_opus_produces_ogg():
    out = media_convert.to_ogg_opus(_wav_bytes())
    assert out[:4] == b"OggS"


@needs_ffmpeg
def test_to_mp3_produces_mp3():
    out = media_convert.to_mp3(_wav_bytes())
    assert out[:3] == b"ID3" or out[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


@needs_ffmpeg
def test_garbage_audio_raises():
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_ogg_opus(b"garbage")


def test_missing_ffmpeg_raises_conversion_error(monkeypatch):
    monkeypatch.setattr(media_convert.shutil, "which", lambda name: None)
    with pytest.raises(media_convert.ConversionError):
        media_convert.to_ogg_opus(b"x")
