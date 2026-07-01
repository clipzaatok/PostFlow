# Setup Guide — TikTok Auto-Poster

## 1. Get TikTok API access (do this first — nothing posts without it)

1. Go to https://developers.tiktok.com/ and create a developer account + app.
2. Request the **Content Posting API** product and the `video.publish` scope.
3. Complete TikTok's app review. Until it's approved, your app can only post
   videos as **private/self-only** — that's fine for testing.
4. Set up the OAuth flow to get a user access token for the TikTok account
   you want to post from. (This part is a one-time browser-based login flow —
   happy to write that script too once you're at this step.)
5. Put the resulting access token into `TIKTOK_ACCESS_TOKEN` in `tiktok_bot.py`.

Docs: https://developers.tiktok.com/doc/content-posting-api-get-started

## 2. Set up Google Sheets + Drive access

1. In Google Cloud Console, create a project (or use an existing one).
2. Enable the **Google Sheets API** (and **Google Drive API** if you're using
   a Drive folder as your video source).
3. Create a **Service Account**, and download its JSON key file. Save it as
   `service_account.json` next to `tiktok_bot.py`.
4. Create your tracking Google Sheet. Share it with the service account's
   email address (found in the JSON key file, looks like
   `something@your-project.iam.gserviceaccount.com`) with **Editor** access.
5. Copy the Sheet ID from its URL:
   `https://docs.google.com/spreadsheets/d/SHEET_ID_IS_HERE/edit`
   and put it into `SHEET_ID` in `tiktok_bot.py`.
6. If using a Drive folder for videos, share that folder with the same
   service account email, and put the folder's ID into `DRIVE_FOLDER_ID`.
   (Folder ID is the last part of its URL.)

## 3. Install dependencies

```
pip install google-api-python-client google-auth google-auth-oauthlib requests
```

## 4. Configure the script

Open `tiktok_bot.py` and fill in the CONFIG section at the top:
- `GOOGLE_CREDENTIALS_PATH`
- `SHEET_ID`
- `LOCAL_VIDEO_FOLDER` and/or `DRIVE_FOLDER_ID`
- `TIKTOK_ACCESS_TOKEN`

## 5. Run it

```
python tiktok_bot.py
```

It will scan your video source(s), skip anything whose hash is already in
the sheet, upload everything new to TikTok, and log the result.

## Notes

- **Dedup is by file content hash (SHA-256), not filename** — so renaming a
  file won't cause a duplicate post, but re-encoding/re-exporting the exact
  same clip will produce a new hash and be treated as new. That's usually
  what you want.
- **Failed uploads are still logged** (with `ERROR` in place of a video ID)
  so you can see what went wrong in the sheet rather than silently retrying
  forever.
- **Access tokens expire.** TikTok tokens typically need refreshing — you'll
  want a refresh-token flow for a long-running bot. Ask me and I'll add it.
- Run this on a schedule (cron, Task Scheduler, or a cloud function) rather
  than leaving it running in a loop — cleaner and easier to debug.
