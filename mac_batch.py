"""
mac_batch.py - Monthly batch processor for Mac
Downloads IShowSpeed Shorts, edits them, uploads to Dropbox /edited_shorts/
"""
import os
import sys
import json
import logging
import random
import subprocess
import tempfile
import time
import dropbox
from dropbox.files import WriteMode
import whisper_timestamped as whisper
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
from groq import Groq

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DROPBOX_TOKEN  = os.environ.get("DROPBOX_TOKEN", "")
DROPBOX_KEY    = "v9hm7aofsntq40a"
DROPBOX_SECRET = "wllr5eqoopyvb5e"
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
EDITED_FOLDER  = "/edited_shorts"
HISTORY_FILE   = os.path.expanduser("~/.mac_batch_history.txt")
COOKIES_FILE   = os.path.expanduser("~/Downloads/www.youtube.com_cookies.txt")
TARGET_URL     = "https://www.youtube.com/@IShowSpeed/shorts"
BATCH_SIZE     = 30
THREADS        = 2

HYPE_WORDS = ["SHEESH","LETS GO","W","BUSSIN","RIZZ","NOWAY","GOATED","SIGMA","GIGACHAD","FR FR"]
FONT_SIZE = 95
FONT_PATHS = [
    "/Library/Fonts/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    os.path.expanduser("~/Library/Fonts/Impact.ttf"),
    "/Library/Fonts/Arial Bold.ttf",
]

def get_dropbox_client():
    return dropbox.Dropbox(DROPBOX_TOKEN)

def upload_file(dbx, local_path, dropbox_path):
    logging.info(f"Uploading to {dropbox_path} ...")
    with open(local_path, "rb") as f:
        dbx.files_upload(f.read(), dropbox_path, mode=WriteMode.overwrite)

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f.readlines())
    return set()

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        f.write("\n".join(history))

def get_video_ids(limit=60):
    cmd = ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit), "--print", "id", TARGET_URL]
    if os.path.exists(COOKIES_FILE):
        cmd += ["--cookies", COOKIES_FILE]
    result = subprocess.run(cmd, capture_output=True, text=True)
    ids = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    logging.info(f"Found {len(ids)} videos")
    return ids

def download_video(video_id, output_path):
    url = f"https://www.youtube.com/shorts/{video_id}"
    cmd = ["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]", "--merge-output-format", "mp4", "-o", output_path, url]
    if os.path.exists(COOKIES_FILE):
        cmd += ["--cookies", COOKIES_FILE]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr}")

def edit_video(input_path, output_path):
    temp_edited = input_path.replace(".mp4", "_temp.mp4")
    cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,hflip", "-filter:v", "setpts=PTS/1.03", "-af", "atempo=1.03", "-c:v", "libx264", "-crf", "23", "-c:a", "aac", "-t", "55", temp_edited]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    cmd2 = ["ffmpeg", "-y", "-i", temp_edited, "-af", "silenceremove=start_periods=1:start_silence=0.3:start_threshold=-40dB:stop_periods=-1:stop_silence=0.3:stop_threshold=-40dB", "-t", "55", "-c:v", "copy", output_path]
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
    audio  = whisper.load_audio(video_path)
    model  = whisper.load_model("small")
    result = whisper.transcribe(model, audio, language="en")
    words  = []
    for seg in result["segments"]:
        for w in seg.get("words", []):
            words.append({"word": w["text"].strip().upper(), "start": w["start"], "end": w["end"]})
    return words

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
        clip  = (ImageClip(frame, ismask=False).set_start(wd["start"]).set_end(wd["end"]).set_position(("center", sub_y)))
        clips.append(clip)
        if i > 0 and i % hype_step == 0:
            hw    = random.choice(HYPE_WORDS)
            hf    = make_hype_frame(hw, video.w)
            hclip = (ImageClip(hf, ismask=False).set_start(wd["start"]).set_end(min(wd["start"] + 0.6, wd["end"])).set_position(("center", int(video.h * 0.15))))
            clips.append(hclip)
    final = CompositeVideoClip([video] + clips)
    final.write_videofile(output_path, fps=30, codec="libx264", audio_codec="aac", preset="fast", threads=THREADS, ffmpeg_params=["-pix_fmt", "yuv420p", "-b:a", "192k", "-ar", "44100", "-profile:v", "main"])
    return words

def get_full_transcript(words):
    return " ".join(w["word"] for w in words)[:1000]

def generate_metadata(transcript, video_name):
    client = Groq(api_key=GROQ_API_KEY)
    prompt = f"""You are a YouTube Shorts expert for IShowSpeed content.
Transcript: "{transcript}"
Video: "{video_name}"
Return ONLY valid JSON:
{{
  "title": "... #shorts",
  "description": "... #shorts #ishowspeed",
  "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10","tag11","tag12","tag13","tag14","tag15"]
}}"""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def main():
    if not DROPBOX_TOKEN:
        print("ERROR: DROPBOX_TOKEN not set")
        sys.exit(1)
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)

    dbx     = get_dropbox_client()
    history = load_history()

    logging.info("Fetching video list from IShowSpeed...")
    all_ids = get_video_ids(limit=60)
    new_ids = [vid for vid in all_ids if vid not in history][:BATCH_SIZE]

    if not new_ids:
        logging.info("No new videos. Done.")
        sys.exit(0)

    logging.info(f"Processing {len(new_ids)} videos")
    success = 0

    for i, video_id in enumerate(new_ids):
        logging.info(f"Video {i+1}/{len(new_ids)}: {video_id}")
        with tempfile.TemporaryDirectory() as tmp:
            raw_path    = os.path.join(tmp, "raw.mp4")
            edited_path = os.path.join(tmp, "edited.mp4")
            final_path  = os.path.join(tmp, f"{video_id}_edited.mp4")
            json_path   = os.path.join(tmp, f"{video_id}_metadata.json")
            try:
                download_video(video_id, raw_path)
                edit_video(raw_path, edited_path)
                words = add_speed_captions(edited_path, final_path)
                transcript = get_full_transcript(words)
                metadata = generate_metadata(transcript, video_id)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)
                upload_file(dbx, final_path, f"{EDITED_FOLDER}/{video_id}_edited.mp4")
                upload_file(dbx, json_path,  f"{EDITED_FOLDER}/{video_id}_metadata.json")
                history.add(video_id)
                save_history(history)
                success += 1
                logging.info(f"Done: {metadata['title']}")
                if i < len(new_ids) - 1:
                    time.sleep(30)
            except Exception as e:
                logging.error(f"Failed {video_id}: {e}")
                continue

    logging.info(f"Batch done! {success}/{len(new_ids)} uploaded.")

if __name__ == "__main__":
    main()
