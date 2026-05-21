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
    result = dbx.files_list_folder(RAW_FOLDER, recursive=True)
    return [
        e for e in result.entries
        if isinstance(e, dropbox.files.FileMetadata)
        and e.name.lower().endswith('.mp4')
    ]
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
