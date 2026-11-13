"""Normalize TikTok Web search, comment and user payloads."""

from __future__ import annotations

import time
import unicodedata
from typing import Optional

from ..douyin.extract import Aweme, MediaItem
from .urls import video_url, profile_url, parse_profile_reference


def _first(data: dict, *keys, default=None):
    if not isinstance(data, dict):
        return default
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return default


def _int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def tiktok_display_text(value) -> str:
    """Keep user-facing TikTok text only when it has visible characters."""
    text = str(value or "").strip()
    visible = "".join(
        char for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("C")
    )
    return text if visible else ""


def _url(value) -> str:
    if isinstance(value, str):
        return value if value.startswith("http") else ""
    if not isinstance(value, dict):
        return ""
    values = _first(value, "urlList", "url_list", default=[]) or []
    if isinstance(values, str):
        values = [values]
    return next((str(item) for item in values if str(item).startswith("http")), "")


def _item(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return {}
    value = raw.get("item") or raw.get("aweme_info") or raw.get("itemStruct") or raw
    return value if isinstance(value, dict) else {}


def parse_tiktok_aweme(raw: dict, quality: str = "highest") -> Optional[Aweme]:
    item = _item(raw)
    aweme_id = str(_first(item, "id", "aweme_id", "itemId", default="") or "")
    if not aweme_id:
        return None
    author = item.get("author") or {}
    author_unique_id = str(_first(author, "uniqueId", "unique_id", default="") or "")
    stats = item.get("stats") or item.get("statistics") or {}
    video = item.get("video") or {}
    create_time = _int(_first(item, "createTime", "create_time", default=0))
    if create_time > 10_000_000_000:
        create_time //= 1000
    if not create_time and aweme_id.isdigit():
        # TikTok 64 位作品 ID 的高 32 位通常是 Unix 秒时间戳。主页 DOM
        # 卡片经常省略 createTime，用该稳定字段补全，避免首扫建立基线后
        # 下一轮把全部旧作品误判成新增。
        candidate = int(aweme_id) >> 32
        if 1_450_000_000 <= candidate <= int(time.time()) + 86400:
            create_time = candidate
    nickname = tiktok_display_text(
        _first(author, "nickname", "uniqueId", "unique_id", default=""))
    aweme = Aweme(
        aweme_id=aweme_id,
        desc=tiktok_display_text(
            _first(item, "desc", "description", default="")),
        create_time=create_time,
        author_name=nickname or (f"@{author_unique_id}" if author_unique_id else ""),
        media_type="video",
    )
    aweme.platform = "tiktok"
    aweme.author_unique_id = author_unique_id
    if author_unique_id:
        aweme.source_url = video_url(aweme_id, author_unique_id)
    aweme.like_count = _int(_first(stats, "diggCount", "digg_count", default=0))
    aweme.comment_count = _int(_first(stats, "commentCount", "comment_count", default=0))
    duration = _int(_first(video, "duration", default=0))
    aweme.duration = duration // 1000 if duration > 10000 else duration
    aweme.cover = _url(_first(video, "cover", "dynamicCover", "originCover", default={}))
    aweme.avatar = _url(_first(author, "avatarThumb", "avatarLarger", "avatar_thumb", default={}))

    addresses = []
    bitrates = video.get("bitrateInfo") or video.get("bit_rate") or []
    for row in bitrates if isinstance(bitrates, list) else []:
        address = row.get("PlayAddr") or row.get("playAddr") or row.get("play_addr") or {}
        url = _url(address)
        if url:
            addresses.append((_int(_first(row, "Bitrate", "bitrate", "bit_rate", default=0)), url))
    direct = _url(_first(video, "playAddr", "play_addr", "downloadAddr", "download_addr", default={}))
    if addresses:
        addresses.sort(reverse=True)
        chosen = addresses[-1][1] if quality == "lowest" else addresses[0][1]
    else:
        chosen = direct
    if chosen:
        aweme.medias.append(MediaItem(url=chosen, kind="video", ext="mp4", index=0))
        aweme.quality_label = quality or "highest"
    # TikTok 搜索页有时只渲染作品卡片和规范链接，不把可下载地址放进
    # hydration。采集未勾选下载时，作品元数据仍然有价值；下载任务会在
    # 没有 medias 时单独标记为失败，而不是把整条搜索结果丢掉。
    return aweme


def parse_tiktok_comment(raw: dict, parent_id: str = "") -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    cid = str(_first(raw, "cid", "id", "comment_id", default="") or "")
    if not cid:
        return None
    user = raw.get("user") or raw.get("author") or {}
    reply_to = str(_first(raw, "reply_id", "replyId", "reply_to", default="") or "")
    if reply_to in {"0", cid}:
        reply_to = ""
    if not reply_to:
        reply_to = str(parent_id or "")
    return {
        "comment_id": cid,
        "text": str(_first(raw, "text", "content", default="") or "").strip(),
        "user_nickname": str(_first(user, "nickname", "uniqueId", "unique_id", default="") or ""),
        "user_sec_uid": str(_first(user, "secUid", "sec_uid", "uid", "id", default="") or ""),
        "user_profile_url": profile_url(username) if (username := parse_profile_reference(str(_first(user, "uniqueId", "unique_id", default="") or ""))) else "",
        "ip_label": str(_first(raw, "ip_label", "ipLabel", default="") or ""),
        "like_count": _int(_first(raw, "digg_count", "diggCount", "like_count", default=0)),
        "create_time": _int(_first(raw, "create_time", "createTime", default=0)),
        "reply_to": reply_to,
    }


def flatten_tiktok_comments(comments: list, include_replies: bool = True) -> list:
    output: list[dict] = []
    for root in comments or []:
        if not isinstance(root, dict):
            continue
        output.append(root)
        if not include_replies:
            continue
        parent = str(_first(root, "cid", "id", "comment_id", default="") or "")
        replies = (
            root.get("reply_comment") or root.get("replyComments")
            or root.get("replies") or root.get("children") or []
        )
        for reply in replies if isinstance(replies, list) else []:
            if isinstance(reply, dict):
                value = dict(reply)
                value.setdefault("reply_id", parent)
                output.append(value)
    return output


def parse_tiktok_user(raw: dict) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    container = raw.get("user_info") or raw.get("userInfo") or raw
    user = container.get("user") if isinstance(container, dict) else None
    user = user or raw.get("user") or container
    if not isinstance(user, dict):
        return None
    stats = (container.get("stats") if isinstance(container, dict) else None) \
        or user.get("stats") or raw.get("stats") or {}
    unique_id = str(_first(user, "uniqueId", "unique_id", "unique_id_str", default="") or "")
    uid = str(_first(user, "id", "uid", "userId", default="") or "")
    sec_uid = str(_first(user, "secUid", "sec_uid", default="") or "")
    if not (unique_id or uid or sec_uid):
        return None
    return {
        "uid": uid,
        "sec_uid": sec_uid,
        "unique_id": unique_id,
        "nickname": str(_first(user, "nickname", "nickName", default="") or ""),
        "avatar": _url(_first(user, "avatarThumb", "avatarLarger", "avatar_thumb", default={})),
        "signature": str(_first(user, "signature", "bioDescription", default="") or ""),
        "follower_count": _int(_first(stats, "followerCount", "follower_count", default=0)),
        "following_count": _int(_first(stats, "followingCount", "following_count", default=0)),
        "video_count": _int(_first(stats, "videoCount", "awemeCount", default=0)),
    }


def parse_tiktok_app_context(context: dict) -> Optional[dict]:
    """Normalize webapp.app-context from TikTok hydration data."""
    if not isinstance(context, dict):
        return None
    user = context.get("user") or context.get("userInfo") or {}
    stats = (
        context.get("stats") or context.get("userStats")
        or context.get("user_stats") or {}
    )
    if isinstance(user, dict) and not stats:
        stats = user.get("stats") or {}
    return parse_tiktok_user({"user": user, "stats": stats})
