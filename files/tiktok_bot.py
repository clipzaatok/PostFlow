"""
TikTok Auto-Poster with Google Sheets Dedup Log
=================================================

WHAT THIS DOES
1. Scans a video source (local folder and/or a Google Drive folder) for video files.
2. Computes a SHA-256 hash of each file (more reliable than filename for dedup,
   since files can get renamed but the content hash won't change).
3. Checks a Google Sheet to see if that hash has already been posted.
4. If not posted: uploads the video to TikTok, then logs the result (filename,
   hash, TikTok video id/link, timestamp, status) as a new row in the Sheet.

WHAT YOU NEED TO FILL IN BEFORE THIS WORKS
- TIKTOK: You must have an approved TikTok Developer app with the
  `video.publish` scope, plus a valid OAuth access token for the creator
  account you're posting to. See the docs: https://developers.tiktok.com/doc/content-posting-api-get-started
  Until your app passes audit, videos can only be posted as private/draft.
  The `upload_to_tiktok()` function below has the real endpoint wired in,
  but you need to supply TIKTOK_ACCESS_TOKEN.

- GOOGLE: You need a Google Cloud project with the Sheets API (and Drive API,
  if using the Drive source) enabled, and a service account JSON key file
  with edit access to your target Sheet (share the Sheet with the service
  account's email address).

INSTALL
    pip install google-api-python-client google-auth google-auth-oauthlib requests

RUN
    python tiktok_bot.py
"""

import hashlib
import io
import os
import time
import mimetypes
from datetime import datetime, timezone

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ----------------------------------------------------------------------------
# CONFIG — fill these in
# ----------------------------------------------------------------------------

# Path to your Google service account credentials JSON
GOOGLE_CREDENTIALS_PATH = "service_account.json"

# The Google Sheet used as the dedup/posting log
SHEET_ID = "YOUR_GOOGLE_SHEET_ID_HERE"          # from the sheet's URL
SHEET_TAB_NAME = "Posts"                         # tab/worksheet name

# Video sources — set either or both
LOCAL_VIDEO_FOLDER = "./videos"                  # local folder to scan, or None
DRIVE_FOLDER_ID = None                           # Google Drive folder ID, or None

# TikTok
TIKTOK_ACCESS_TOKEN = "YOUR_TIKTOK_ACCESS_TOKEN_HERE"
TIKTOK_UPLOAD_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"

# How TikTok posts should be visibility-set. TikTok requires you to explicitly
# state this. SELF_ONLY is the safe default until your app is fully audited.
TIKTOK_PRIVACY_LEVEL = "SELF_ONLY"  # options: PUBLIC_TO_EVERYONE, MUTUAL_FOLLOW_FRIENDS, SELF_ONLY

# Delay between posts, in seconds, to avoid hammering the API / looking spammy
SECONDS_BETWEEN_POSTS = 30

# ----------------------------------------------------------------------------
# GOOGLE AUTH HELPERS
# ----------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_google_creds():
    return service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )


def get_sheets_service():
    creds = get_google_creds()
    return build("sheets", "v4", credentials=creds)


def get_drive_service():
    creds = get_google_creds()
    return build("drive", "v3", credentials=creds)


# ----------------------------------------------------------------------------
# SHEET LOG (dedup source of truth)
# ----------------------------------------------------------------------------

def ensure_header(sheets_service):
    """Make sure the sheet has a header row; add one if the tab is empty."""
    range_ = f"{SHEET_TAB_NAME}!A1:E1"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=range_
    ).execute()
    if not result.get("values"):
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=range_,
            valueInputOption="RAW",
            body={"values": [["filename", "sha256_hash", "tiktok_video_id", "tiktok_link", "posted_at_utc"]]},
        ).execute()


def get_posted_hashes(sheets_service):
    """Return a set of all hashes already logged, so we can skip re-posting."""
    range_ = f"{SHEET_TAB_NAME}!A2:E"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=range_
    ).execute()
    rows = result.get("values", [])
    return {row[1] for row in rows if len(row) > 1}


def log_post(sheets_service, filename, file_hash, tiktok_video_id, tiktok_link):
    row = [[
        filename,
        file_hash,
        tiktok_video_id or "",
        tiktok_link or "",
        datetime.now(timezone.utc).isoformat(),
    ]]
    sheets_service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB_NAME}!A:E",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": row},
    ).execute()


# ----------------------------------------------------------------------------
# VIDEO SOURCES
# ----------------------------------------------------------------------------

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_local_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def list_local_videos(folder: str):
    """Yields (filename, local_filepath) for local video files."""
    if not folder or not os.path.isdir(folder):
        return
    for name in sorted(os.listdir(folder)):
        ext = os.path.splitext(name)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            yield name, os.path.join(folder, name)


def list_drive_videos(drive_service, folder_id: str):
    """Yields (filename, drive_file_id) for video files in a Drive folder."""
    if not folder_id:
        return
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        resp = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            ext = os.path.splitext(f["name"])[1].lower()
            if ext in VIDEO_EXTENSIONS or (f.get("mimeType", "").startswith("video/")):
                yield f["name"], f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def download_drive_file_to_temp(drive_service, file_id: str, dest_path: str):
    request = drive_service.files().get_media(fileId=file_id)
    with io.FileIO(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return dest_path


# ----------------------------------------------------------------------------
# TIKTOK UPLOAD
# ----------------------------------------------------------------------------

def upload_to_tiktok(video_path: str):
    """
    Uploads a local video file to TikTok using the Content Posting API's
    FILE_UPLOAD source (init -> PUT bytes -> poll status).
    Returns (tiktok_video_id, tiktok_link) on success, raises on failure.

    Docs: https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
    """
    file_size = os.path.getsize(video_path)

    init_payload = {
        "post_info": {
            "privacy_level": TIKTOK_PRIVACY_LEVEL,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }

    headers = {
        "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
        "Content-Type": "application/json; charset=UTF-8",
    }

    init_resp = requests.post(TIKTOK_UPLOAD_INIT_URL, json=init_payload, headers=headers)
    init_resp.raise_for_status()
    init_data = init_resp.json()

    if init_data.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init failed: {init_data}")

    upload_url = init_data["data"]["upload_url"]
    publish_id = init_data["data"]["publish_id"]

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    put_headers = {
        "Content-Type": mimetypes.guess_type(video_path)[0] or "video/mp4",
        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
    }
    put_resp = requests.put(upload_url, data=video_bytes, headers=put_headers)
    put_resp.raise_for_status()

    # Poll for publish status
    status_url = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
    for _ in range(20):
        time.sleep(3)
        status_resp = requests.post(
            status_url, json={"publish_id": publish_id}, headers=headers
        )
        status_resp.raise_for_status()
        status_data = status_resp.json().get("data", {})
        status = status_data.get("status")
        if status == "PUBLISH_COMPLETE":
            video_id = status_data.get("publicaly_available_post_id", [None])[0] if status_data.get("publicaly_available_post_id") else None
            link = f"https://www.tiktok.com/@me/video/{video_id}" if video_id else None
            return video_id, link
        if status == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {status_data}")

    raise TimeoutError("TikTok publish status did not complete in time")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    sheets_service = get_sheets_service()
    ensure_header(sheets_service)
    already_posted = get_posted_hashes(sheets_service)
    print(f"Loaded {len(already_posted)} previously-posted hashes from the sheet.")

    drive_service = get_drive_service() if DRIVE_FOLDER_ID else None

    candidates = []  # list of (filename, local_path, cleanup_needed)

    for name, path in list_local_videos(LOCAL_VIDEO_FOLDER):
        candidates.append((name, path, False))

    if drive_service and DRIVE_FOLDER_ID:
        tmp_dir = "./_tiktok_bot_tmp"
        os.makedirs(tmp_dir, exist_ok=True)
        for name, file_id in list_drive_videos(drive_service, DRIVE_FOLDER_ID):
            local_tmp_path = os.path.join(tmp_dir, name)
            print(f"Downloading from Drive: {name}")
            download_drive_file_to_temp(drive_service, file_id, local_tmp_path)
            candidates.append((name, local_tmp_path, True))

    if not candidates:
        print("No video files found in configured source(s).")
        return

    for name, path, is_temp in candidates:
        file_hash = hash_local_file(path)

        if file_hash in already_posted:
            print(f"SKIP (already posted): {name}")
            if is_temp:
                os.remove(path)
            continue

        print(f"Posting: {name} ...")
        try:
            video_id, link = upload_to_tiktok(path)
            log_post(sheets_service, name, file_hash, video_id, link)
            already_posted.add(file_hash)
            print(f"  -> posted. video_id={video_id} link={link}")
        except Exception as e:
            print(f"  -> FAILED to post {name}: {e}")
            # Log the failure too, without a hash match, so you can see it in the sheet
            log_post(sheets_service, name, file_hash, "ERROR", str(e))
        finally:
            if is_temp:
                os.remove(path)

        time.sleep(SECONDS_BETWEEN_POSTS)


if __name__ == "__main__":
    main()
