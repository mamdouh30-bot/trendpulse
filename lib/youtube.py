"""YouTube Data API v3 uploader using OAuth refresh token from .env.

Quota note: videos.insert costs 1600 units; the free daily default is 10000
so ~6 uploads/day are possible — more than enough for a 12h schedule.
"""
import sys
import time
from pathlib import Path

from .utils import load_env

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _get_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    env = load_env(PROJECT_ROOT / ".env")
    required = ["YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise RuntimeError(f"missing YouTube credentials in .env: {missing}")
    creds = Credentials(
        token=None,
        refresh_token=env["YT_REFRESH_TOKEN"],
        client_id=env["YT_CLIENT_ID"],
        client_secret=env["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    return build("youtube", "v3", credentials=creds)


def upload(video_path: Path, thumb_path, meta, cfg, logger):
    from googleapiclient.http import MediaFileUpload

    try:
        service = _get_service()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"upload unavailable: {str(e)[:160]}")
        return {"uploaded": False, "reason": str(e)[:300]}

    privacy = cfg["upload"]["privacy"]
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta.get("description", "")[:4900],
            "tags": meta.get("tags", [])[:22],
            "categoryId": cfg["upload"]["category_id"],
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    try:
        req = service.videos().insert(part="snippet,status", body=body,
                                      media_body=media, fields="id,status")
        response, retries, done = None, 0, False
        while not done:
            status, response = req.next_chunk(num_retries=3)
            if response:
                done = True
            elif status is None:
                retries += 1
                if retries > 200:
                    raise RuntimeError("upload stalled")
                time.sleep(5)
        vid = response.get("id")
        actual_privacy = (response.get("status") or {}).get("privacyStatus")
        if actual_privacy and actual_privacy != privacy:
            logger.warning(
                f"YouTube forced privacy {privacy} -> {actual_privacy} "
                f"(unaudited API project restriction)")
        logger.info(f"UPLOADED video id={vid} privacy={actual_privacy}")

        if thumb_path and Path(thumb_path).exists():
            try:
                service.thumbnails().set(
                    videoId=vid,
                    media_body=MediaFileUpload(str(thumb_path))).execute()
                logger.info("thumbnail set")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"thumbnail upload failed: {str(e)[:140]}")
        return {"uploaded": True, "video_id": vid,
                "url": f"https://youtube.com/watch?v={vid}",
                "privacy": actual_privacy or privacy}
    except Exception as e:  # noqa: BLE001
        logger.error(f"upload failed: {str(e)[:300]}")
        return {"uploaded": False, "reason": str(e)[:300]}


if __name__ == "__main__":
    print("import this module from agent.py; not meant to run standalone")
    sys.exit(0)
