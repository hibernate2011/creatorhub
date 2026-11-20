"""TikTok Web URLs, reference parsing and page-owned API paths.

CreatorHub deliberately lets the logged-in TikTok page create signatures such as
X-Bogus.  These constants are matching/navigation anchors, not direct unsigned API
clients.
"""

from dataclasses import dataclass
import re
from urllib.parse import quote, unquote, urlsplit

HOME_URL = "https://www.tiktok.com/"
SEARCH_VIDEO_API = "/api/search/item/full/"
SEARCH_USER_API = "/api/search/user/full/"
COMMENT_LIST_API = "/api/comment/list/"
COMMENT_REPLY_API = "/api/comment/list/reply/"
COMMENT_PUBLISH_HINTS = ("/api/comment/publish/", "/api/comment/create/")


def video_search_url(keyword: str) -> str:
    return f"https://www.tiktok.com/search/video?q={quote(str(keyword or '').strip())}"


def user_search_url(keyword: str) -> str:
    return f"https://www.tiktok.com/search/user?q={quote(str(keyword or '').strip())}"


@dataclass(frozen=True)
class TikTokVideoRef:
    aweme_id: str
    author_unique_id: str
    url: str


_VIDEO_PATH_RE = re.compile(r"/@([^/?#]+)/video/(\d+)", re.I)
_PROFILE_PATH_RE = re.compile(r"/@([^/?#]+)", re.I)
_VIDEO_ID_RE = re.compile(r"^\d{1,30}$")
_UNIQUE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


def video_url(aweme_id: str, author: str) -> str:
    """Build a canonical TikTok video URL; never invent an author placeholder."""
    item_id = str(aweme_id or "").strip()
    unique_id = unquote(str(author or "").strip().lstrip("@"))
    if not _VIDEO_ID_RE.fullmatch(item_id):
        raise ValueError("invalid TikTok video id")
    if not unique_id:
        raise ValueError("TikTok author unique_id is required")
    return f"https://www.tiktok.com/@{quote(unique_id, safe='._-')}/video/{item_id}"


def player_url(aweme_id: str) -> str:
    item_id = str(aweme_id or "").strip()
    return f"https://www.tiktok.com/player/v1/{item_id}" if _VIDEO_ID_RE.fullmatch(item_id) else ""


def parse_video_reference(value: str, author_unique_id: str = "") -> TikTokVideoRef | None:
    """Parse a standard URL/share text, or an ID paired with author_unique_id."""
    text = str(value or "").strip()
    author = str(author_unique_id or "").strip().lstrip("@")
    candidates = _URL_RE.findall(text) or ([text] if text.startswith("http") else [])
    for candidate in candidates:
        try:
            path = unquote(urlsplit(candidate.rstrip(".,，。)）]】")).path)
        except ValueError:
            continue
        matched = _VIDEO_PATH_RE.search(path)
        if matched:
            unique_id, item_id = matched.group(1), matched.group(2)
            return TikTokVideoRef(item_id, unique_id, video_url(item_id, unique_id))
    if _VIDEO_ID_RE.fullmatch(text) and author:
        return TikTokVideoRef(text, author, video_url(text, author))
    return None


def parse_profile_reference(value: str) -> str:
    """Return a TikTok unique_id from a profile/video URL or @name."""
    text = str(value or "").strip()
    candidates = _URL_RE.findall(text) or ([text] if text.startswith("http") else [])
    for candidate in candidates:
        try:
            path = unquote(urlsplit(candidate.rstrip(".,，。)）]】")).path)
        except ValueError:
            continue
        matched = _PROFILE_PATH_RE.search(path)
        if matched:
            unique_id = unquote(matched.group(1)).strip().lstrip("@")
            if _UNIQUE_ID_RE.fullmatch(unique_id):
                return unique_id
    direct = text.lstrip("@")
    if _UNIQUE_ID_RE.fullmatch(direct) and not direct.isdigit():
        return direct
    return ""


def profile_url(unique_id: str) -> str:
    return f"https://www.tiktok.com/@{quote(str(unique_id or '').strip().lstrip('@'), safe='._-')}"
