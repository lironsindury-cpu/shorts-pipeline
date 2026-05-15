"""
main.py - GitHub Actions Video Editor
Flow: Dropbox raw_videos/ -> Edit (iShowSpeed style) -> Dropbox edited_shorts/
"""

import os
import sys
import logging
import random
import subprocess
import shutil
import tempfile

import dropbox
from dropbox.files import WriteMode
import whisper_timestamped as whisper
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ── Dropbox config ──────────────────────────────────────────────────────────
DROPBOX_TOKEN   = os.environ["DROPBOX_TOKEN"]
RAW_FOLDER      = "/raw_videos"
EDITED_FOLDER   = "/edited_shorts"

# ── iShowSpeed caption config ────────────────────────────────────────────────
SPEED_COLORS = [
    (255, 255,   0, 255),   # yellow  (main)
    (255, 255, 255, 255),   # white
    (255,  50,  50, 255),   # red
]
HYPE_WORDS = [
    "SHEESH","LETS GO","W","BUSSIN","RIZZ",
    "NOWAY","GOATED","SIGMA","GIGACHAD","FR FR",
]
FONT_SIZE = 95
FONT_PATHS = [
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


# ═══════════════════════════════════════════════════════════════════════════════
# DROPBOX HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_dropbox_client():
    return dropbox.Dropbox(DROPBOX_TOKEN)


def list_raw_videos(dbx):
    """Return a list of file metadata objects in raw_videos/."""
    result = dbx.files_list_folder(RAW_FOLDER)
    files = [e for e in result.entries if isinstance(e, dropbox.files.FileMetadata)]
    return files


def download_file(dbx, dropbox_path: str, local_path: str):
    logging.info(f"Downloading {dropbox_path} ...")
    with open(local_path, "wb") as f:
        _, response = dbx.files_download(dropbox_path)
        f.write(response.content)
    logging.info(f"Downloaded to {local_path}")


def upload_file(dbx, local_path: str, dropbox_path: str):
    logging.info(f"Uploading to {dropbox_path} ...")
    with open(local_path, "rb") as f:
        dbx.files_upload(f.read(), dropbox_path, mode=WriteMode.overwrite)
    logging.info("Upload complete.")


def delete_file(dbx, dropbox_path: str):
    dbx.files_delete_v2(dropbox_path)
    logging.info(f"Deleted {dropbox_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# VIDEO PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def convert_to_portrait(input_path: str, output_path: str):
    """Convert video to 9:16 (1080x1920) with blurred background."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "split[bg][fg];"
        "[bg]scale=1080:1920,boxblur=20:20[blurred];"
        "[blurred][fg]overlay=(W-w)/2:(H-h)/2",
        "-c:v", "libx264", "-crf", "23",
        "-c:a", "aac", "-t", "60",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")


def get_font(size=FONT_SIZE):
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_word_frame(word_text: str, video_w: int, color, scale=1.0):
    font = get_font(int(FONT_SIZE * scale))
    img  = Image.new("RGBA", (video_w, 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bbox   = draw.textbbox((0, 0), word_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (video_w - text_w) // 2
    y = (260 - text_h) // 2

    # thick black outline
    r = max(4, int(FONT_SIZE * scale * 0.05))
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                draw.text((x + dx, y + dy), word_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), word_text, font=font, fill=color)
    return np.array(img)


def make_hype_frame(hype_word: str, video_w: int):
    font = get_font(int(FONT_SIZE * 1.4))
    img  = Image.new("RGBA", (video_w, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = (255, 255, 0, 255)

    bbox   = draw.textbbox((0, 0), hype_word, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (video_w - text_w) // 2
    y = (300 - text_h) // 2

    for dx in range(-6, 7):
        for dy in range(-6, 7):
            if dx * dx + dy * dy <= 36:
                draw.text((x + dx, y + dy), hype_word, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), hype_word, font=font, fill=color)
    return np.array(img)


def transcribe(video_path: str):
    logging.info("Transcribing with Whisper ...")
    audio  = whisper.load_audio(video_path)
    model  = whisper.load_model("base")
    result = whisper.transcribe(model, audio, language="en")
    words  = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            words.append({
                "word":  w["text"].strip().upper(),
                "start": w["start"],
                "end":   w["end"],
            })
    return words


def add_speed_captions(input_path: str, output_path: str):
    words = transcribe(input_path)
    video = VideoFileClip(input_path)
    clips = []

    bad = set("♫♪[]")
    words = [w for w in words if not any(c in w["word"] for c in bad)]

    sub_y     = video.h - 350
    hype_step = max(8, len(words) // 4)

    for i, wd in enumerate(words):
        duration = wd["end"] - wd["start"]

        if i % 7 == 0:
            color = (255, 50, 50, 255)
        elif i % 3 == 0:
            color = (255, 255, 255, 255)
        else:
            color = (255, 255, 0, 255)

        scale = 1.2 if duration < 0.3 else 1.0
        frame = make_word_frame(wd["word"], video.w, color, scale)
        clip  = (ImageClip(frame, ismask=False)
                 .set_start(wd["start"])
                 .set_end(wd["end"])
                 .set_position(("center", sub_y)))
        clips.append(clip)

        # hype overlay
        if i > 0 and i % hype_step == 0:
            hw    = random.choice(HYPE_WORDS)
            hf    = make_hype_frame(hw, video.w)
            hclip = (ImageClip(hf, ismask=False)
                     .set_start(wd["start"])
                     .set_end(min(wd["start"] + 0.6, wd["end"]))
                     .set_position(("center", int(video.h * 0.15))))
            clips.append(hclip)

    logging.info(f"Compositing {len(clips)} caption clips ...")
    final = CompositeVideoClip([video] + clips)
    final.write_videofile(output_path, codec="libx264", audio_codec="aac")
    logging.info(f"Saved: {output_path}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    dbx   = get_dropbox_client()
    files = list_raw_videos(dbx)

    if not files:
        logging.info("No raw videos found in Dropbox. Exiting.")
        sys.exit(0)

    target = files[0]
    logging.info(f"Processing: {target.name}")

    with tempfile.TemporaryDirectory() as tmp:
        raw_path      = os.path.join(tmp, "raw.mp4")
        portrait_path = os.path.join(tmp, "portrait.mp4")
        final_path    = os.path.join(tmp, f"short_{target.name}")

        # 1. Download
        download_file(dbx, target.path_lower, raw_path)

        # 2. Convert to 9:16 portrait
        convert_to_portrait(raw_path, portrait_path)

        # 3. Add Speed-style captions
        add_speed_captions(portrait_path, final_path)

        # 4. Upload to edited_shorts/
        upload_file(dbx, final_path, f"{EDITED_FOLDER}/{target.name}")

        # 5. Delete raw file
        delete_file(dbx, target.path_lower)

    logging.info("Done!")


if __name__ == "__main__":
    main()
