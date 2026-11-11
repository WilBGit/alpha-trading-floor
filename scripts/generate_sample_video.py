#!/usr/bin/env python3
"""Improved sample video with natural pacing, breathing pauses, and slower delivery.
Fixes: rushed speech, no breaths, choppy chunk transitions.
"""

import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

SAMPLE_SCRIPT = """
SPY is holding above the 570 level. Buyers have a shot at a continuation move if we see a clean break of 572.

The interesting part? QQQ is showing relative strength today. Not seeing full panic yet in the broader market.

I'm watching the 580 area on QQQ as the next key resistance. Momentum is there, but confirmation isn't.

Trade the chart. Not the noise. That's the level I'm watching on the 5-minute.

Beginner takeaway: Wait for the break and retest before entering. Risk is defined below the recent low.
""".strip()


def normalize_text(text: str) -> str:
    text = re.sub(r"[#@$]", "", text)
    text = re.sub(r"[𐀀-􏿿]", "", text)
    text = re.sub(r"[\W_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def chunk_text_with_breaths(text: str, max_chars: int = 350) -> list[str]:
    """Sentence chunks + explicit breath pause marker for natural human rhythm."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current += (" " if current else "") + sent
        else:
            if current:
                chunks.append(current + " [[slnc 300]]")  # breath pause
            current = sent
    if current:
        chunks.append(current + " [[slnc 300]]")
    return chunks


def synthesize_natural(text: str, output_path: Path, voice: str = "Evan (Enhanced)", speed: float = 160.0) -> Path:
    """Slower speed + breath markers + post limiter for human-like delivery."""
    text = normalize_text(text)
    chunks = chunk_text_with_breaths(text, max_chars=350)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        aiff_files = []
        for i, chunk in enumerate(chunks):
            aiff = Path(tmp) / f"chunk_{i}.aiff"
            cmd = ["say", "-v", voice, "-r", str(speed), "-o", str(aiff), chunk]
            subprocess.run(cmd, check=True, capture_output=True)
            aiff_files.append(str(aiff))

        list_file = Path(tmp) / "list.txt"
        list_file.write_text("\n".join(f"file '{f}'" for f in aiff_files))

        # Add gentle crossfade between chunks for smoother breathing transitions
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-af", "acompressor,alimiter=limit=-3dB,afade=t=in:st=0:d=0.15",
            "-c:a", "libmp3lame", "-b:a", "192k", str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)

    return output_path


def main():
    out_dir = Path("videos/sample")
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = out_dir / f"sample_{ts}.mp3"
    video_path = out_dir / f"sample_{ts}.mp4"

    print("Synthesizing with slower pace (145 WPM) + breath pauses [[slnc 300]] + smoother transitions...")
    synthesize_natural(SAMPLE_SCRIPT, audio_path, speed=145.0)
    print(f"Audio ready: {audio_path}")

    # Simple title card
    title_card = out_dir / f"title_{ts}.png"
    os.system(
        f'ffmpeg -y -f lavfi -i "color=c=#0d1117:s=1920x1080:d=5" '
        f'-vf "drawtext=fontfile=/System/Library/Fonts/Helvetica.ttc:text=\'ALPHA TRADING FLOOR\':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2,'
        f'drawtext=fontfile=/System/Library/Fonts/Helvetica.ttc:text=\'Natural Voice Sample (145 WPM + Breaths)\':fontcolor=#58a6ff:fontsize=32:x=(w-text_w)/2:y=h/2+80" '
        f'-frames:v 1 {title_card} 2>/dev/null'
    )

    print("Composing final MP4...")
    cmd = (
        f'ffmpeg -y -loop 1 -i {title_card} -i {audio_path} '
        f'-c:v libx264 -preset fast -tune stillimage -c:a aac -b:a 192k '
        f'-shortest -pix_fmt yuv420p -vf "scale=1920:1080" {video_path} 2>/dev/null'
    )
    os.system(cmd)

    print(f"\n✅ Improved sample video: {video_path}")
    print("Key fixes applied: 145 WPM, explicit breathing pauses, chunk crossfade.")


if __name__ == "__main__":
    main()
