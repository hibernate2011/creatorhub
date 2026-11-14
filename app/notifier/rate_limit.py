"""Share webhook quotas across all notification callers using the same database."""
from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime
from hashlib import sha256
from time import monotonic, time
from urllib.parse import parse_qs, unquote, urlsplit

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert

from ..db import get_session
from ..models import WebhookSendState

# Spacing requests satisfies both rolling limits for Feishu (100/min, 5/s)
# and the DingTalk limit (20/min). Leave a margin at the window boundary.
WEBHOOK_INTERVAL = {"dingtalk": 3.1, "feishu": 0.65}
WEBHOOK_COOLDOWN = {"dingtalk": 600, "feishu": 60}
WEBHOOK_RATE_CODE = {"dingtalk": 130101, "feishu": 11232}
LEASE_SECONDS = 30  # Longer than the bounded outbound request.


class WebhookBusy(Exception):
    """A local slot is unavailable and no request was sent."""


class WebhookRateLimited(Exception):
    def __init__(self, kind: str, retry_after: str):
        seconds = 0
        try:
            if retry_after.isascii() and retry_after.isdecimal():
                seconds = float(int(retry_after))
            elif retry_after:
                seconds = parsedate_to_datetime(retry_after).timestamp() - time()
        except (ValueError, OverflowError, OSError):
            pass
        self.cooldown = max(WEBHOOK_COOLDOWN[kind], seconds)
        super().__init__("机器人发送频率受限")


def robot_key(kind: str, webhook: str) -> str | None:
    if kind not in WEBHOOK_INTERVAL or not webhook:
        return None
    identity = webhook.strip()
    try:
        url = urlsplit(identity)
        identity = url._replace(fragment="").geturl()
        if kind == "dingtalk" and url.hostname == "oapi.dingtalk.com" and url.path == "/robot/send":
            identity = parse_qs(url.query).get("access_token", [identity])[0]
        elif kind == "feishu" and url.hostname in {"open.feishu.cn", "open.larksuite.com"}:
            if url.path.startswith("/open-apis/bot/v2/hook/"):
                identity = unquote(url.path.rstrip("/").rsplit("/", 1)[-1])
    except ValueError:
        pass  # The sender will report an invalid URL as a delivery failure.
    return sha256(f"{kind}:{identity}".encode()).hexdigest()


def webhook_ready(session, key: str) -> bool:
    state = session.get(WebhookSendState, key)
    return state is None or max(state.available_at, state.lease_until) <= time()


def _reserve(key: str) -> tuple[float | None, float]:
    with get_session() as session:
        now = time()
        state = session.get(WebhookSendState, key)
        if state and max(state.available_at, state.lease_until) > now:
            return None, max(0.0, state.available_at - now)

        lease = now + LEASE_SECONDS
        stmt = insert(WebhookSendState).values(robot_key=key, lease_until=lease)
        claimed = session.execute(stmt.on_conflict_do_update(
            index_elements=[WebhookSendState.robot_key], set_={"lease_until": lease},
            where=(WebhookSendState.available_at <= now) & (WebhookSendState.lease_until <= now),
        )).rowcount
        session.commit()
        return (lease if claimed else None), 0.0


async def reserve_webhook(key: str, *, wait_seconds: float) -> float:
    deadline = monotonic() + wait_seconds
    while True:
        lease, delay = _reserve(key)
        if lease is not None:
            return lease
        remaining = deadline - monotonic()
        if remaining <= 0 or delay > remaining:
            raise WebhookBusy("机器人发送间隔未到，请稍后重试")
        # While a request is in flight, its lease can be released at any time.
        await asyncio.sleep(min(delay or 0.25, remaining))


def finish_webhook(key: str, lease: float, delay: float) -> None:
    with get_session() as session:
        session.execute(update(WebhookSendState).where(
            WebhookSendState.robot_key == key, WebhookSendState.lease_until == lease,
        ).values(available_at=time() + delay, lease_until=0.0))
        session.commit()
