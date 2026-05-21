"""
scraper.py - Downloads latest IShowSpeed Short and uploads to Dropbox
"""
import os
import subprocess
import tempfile
import dropbox
from dropbox.files import WriteMode

DROPBOX_TOKEN  = os.environ["DROPBOX_TOKEN"]
DROPBOX_KEY    = "v9hm7aofsntq40a"
DROPBOX_SECRET = "wllr5eqoopyvb5e"
RAW_FOLDER     = "/raw_videos"
HISTORY_FILE   = "history.txt"
TARGET_URL     = "https://www.youtube.com/@IShowSpeed/shorts"

def get_dropbox_client():
    return dropbox.Dropbox(
        oauth2_refresh_token=DROPBOX_TOKEN,
        app_key=DROPBOX_KEY,
        app_secret=DROPBOX_SECRET
    )

def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            return set(line.strip() for line in f.readlines())
    return set()

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        f.write("\n".join(history))

def get_latest_video_id():
    result = subprocess.run([
        "yt-dlp",
        "--flat-playlist",
        "--playlist-end", "5",
        "--print", "id",
        TARGET_URL
    ], capture_output=True, text=True)
    ids = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
    return ids

def download_video(video_id, output_path):
    url = f"https://www.youtube.com/shorts/{video_id}"
    result = subprocess.run([
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "--merge-output-format", "mp4",
        "-o", output_path,
        url
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr}")

def upload_to_dropbox(dbx, local_path, filename):
    dropbox_path = f"{RAW_FOLDER}/{filename}"
    with open(local_path, "rb") as f:
        dbx.files_upload(f.read(), dropbox_path, mode=WriteMode.overwrite)
    print(f"Uploaded to {dropbox_path}")

def main():
    dbx = get_dropbox_client()
    history = load_history()
    video_ids = get_latest_video_id()

    for video_id in video_ids:
        if video_id in history:
            print(f"Already downloaded: {video_id}")
            continue

        print(f"Downloading: {video_id}")
        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, f"{video_id}.mp4")
            try:
                download_video(video_id, output_path)
                upload_to_dropbox(dbx, output_path, f"{video_id}.mp4")
                history.add(video_id)
                save_history(history)
                print(f"Done: {video_id}")
                break  # Only process one new video per run
            except Exception as e:
                print(f"Error: {e}")
                continue

if __name__ == "__main__":
    main()
