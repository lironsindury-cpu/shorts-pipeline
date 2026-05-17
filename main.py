"""
main.py - GitHub Actions Video Editor
Flow: Dropbox raw_videos/ -> Edit (iShowSpeed style) -> Dropbox edited_shorts/
"""

import os
import sys
import json
import logging
import random
import subprocess
import tempfile

import dropbox
from dropbox.files import WriteMode
import whisper_timestamped as whisper
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from groq import Groq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DROPBOX_TOKEN  = os.environ["DROPBOX_TOKEN"]
DROPBOX_KEY    = "v9hm7aofsntq40a"
DROPBOX_SECRET = "wllr5eqoopyvb5e"
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
RAW_FOLDER     = "/raw_videos"
EDITED_FOLDER  = "/edited_shorts"

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


def get_dropbox_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DROPBOX_TOKEN,
        app_key=DROPBOX_KEY,
        app_secret=DROPBOX_SECRET
    )

def list_raw_videos(dbx):
    result = dbx.files_list_folder(RAW_FOLDER)
    return [e for e in result.entries if isinstance(e, dropbox.files.FileMetadata)]

def download_file(dbx, dropbox_path, local_path):
    logging.info(f"Downloading {dropbox_path} ...")
    with open(local_path, "wb") as f:
        _, response = dbx.files_download(dropbox_path)
        f.write(response.content)

def upload_file(dbx, local_path, dropbox_path):
    logging.info(f"Uploading to {dropbox_path} ...")
    with open(local_path, "rb") as f:
        dbx.files_upload(f.read(), dropbox_path, mode=WriteMode.overwrite)

def delete_file(dbx, dropbox_path):
    dbx.files_delete_v2(dropbox_path)
    logging.info(f"Deleted {dropbox_path}")


def edit_video(input_path, output_path):
    temp_edited = input_path.replace(".mp4", "_temp_edited.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,hflip",
        "-filter:v", "setpts=PTS/1.03",
        "-af", "atempo=1.03",
        "-c:v", "libx264", "-crf", "23",
        "-c:a", "aac", "-t", "60",
        temp_edited
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg edit failed:\n{result.stderr}")

    cmd2 = [
        "ffmpeg", "-y", "-i", temp_edited,
        "-af",
        "silenceremove=start_periods=1:start_silence=0.3:start_threshold=-40dB"
        ":stop_periods=-1:stop_silence=0.3:stop_threshold=-40dB",
        "-c:v", "copy",
        output_path
    ]
    result2 = subprocess.run(cmd2, capture_output=True, text=True)
    if result2.returncode != 0:
        import shutil
        shutil.copy(temp_edited, output_path)
    os.remove(temp_edited)


def get_font(size=FONT_SIZE):
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()

def make_word_frame(word_text, video_w, color, scale=1.0):
    font = get_font(int(FONT_SIZE * scale))
    img  = Image.new("RGBA", (video_w, 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    bbox   = draw.textbbox((0, 0), word_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (video_w - text_w) // 2
    y = (260 - text_h) // 2
    r = max(4, int(FONT_SIZE * scale * 0.05))
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                draw.text((x + dx, y + dy), word_text, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), word_text, font=font, fill=color)
    return np.array(img)

def make_hype_frame(hype_word, video_w):
    font  = get_font(int(FONT_SIZE * 1.4))
    img   = Image.new("RGBA", (video_w, 300), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
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

def transcribe(video_path):
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

def get_full_transcript(words):
    return " ".join(w["word"] for w in words)[:1000]

def add_speed_captions(input_path, output_path):
    words = transcribe(input_path)
    video = VideoFileClip(input_path)
    clips = []
    bad   = set("♫♪[]")
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
    return words


def generate_metadata(transcript, video_name):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""You are a professional YouTube Shorts growth strategist.
Based on this video transcript: "{transcript}"
And video file name: "{video_name}"

Generate for maximum viral reach on YouTube Shorts:
1. A short punchy viral TITLE (max 60 characters, hype energy)
2. A compelling DESCRIPTION (2-3 sentences, energetic tone, ends with hashtags)
3. Exactly 15 YouTube TAGS (mix of broad and niche, English)

Return ONLY valid JSON, no markdown, no explanation:
{{
  "title": "...",
  "description": "... #shorts #viral #fyp",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12", "tag13", "tag14", "tag15"]
}}"""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    metadata = json.loads(raw)
    logging.info(f"Metadata generated: {metadata['title']}")
    return metadata


def main():
    dbx   = get_dropbox_client()
    files = list_raw_videos(dbx)

    if not files:
        logging.info("No raw videos found in Dropbox. Exiting.")
        sys.exit(0)

    target = files[0]
    logging.info(f"Processing: {target.name}")

    with tempfile.TemporaryDirectory() as tmp:
        raw_path    = os.path.join(tmp, "raw.mp4")
        edited_path = os.path.join(tmp, "edited.mp4")
        final_path  = os.path.join(tmp, f"short_{target.name}")
        json_path   = os.path.join(tmp, f"{target.name}_metadata.json")

        download_file(dbx, target.path_lower, raw_path)
        edit_video(raw_path, edited_path)
        words = add_speed_captions(edited_path, final_path)
        transcript = get_full_transcript(words)

        metadata = generate_metadata(transcript, target.name)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        upload_file(dbx, final_path, f"{EDITED_FOLDER}/short_{target.name}")
        upload_file(dbx, json_path,  f"{EDITED_FOLDER}/{target.name}_metadata.json")
        delete_file(dbx, target.path_lower)

    logging.info("Done!")


if __name__ == "__main__":
    main()
