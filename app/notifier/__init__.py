"""通知推送。对应逆向 internal/notifier(bark / dingding / telegram)。

每个渠道 config 字段:
  bark:     {"key": "设备key", "server": "https://api.day.app"(可选)}
  dingtalk: {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
             "secret": "加签密钥"(可选), "keyword": "关键词"(可选)}
  telegram: {"bot_token": "xxx", "chat_id": "xxx", "api_base": "https://api.telegram.org"(可选)}
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Dict, List, Tuple

import httpx

from .rate_limit import (WEBHOOK_INTERVAL, WEBHOOK_RATE_CODE, WebhookBusy, WebhookRateLimited,
                         finish_webhook, reserve_webhook, robot_key)

TIMEOUT = 12.0


def _webhook_result(response: httpx.Response, kind: str) -> Tuple[bool, str]:
    try:
        result = response.json()
    except ValueError:
        result = None
    code = None
    if isinstance(result, dict):
        code = result.get("errcode") if kind == "dingtalk" else result.get("code", result.get("StatusCode"))
    if response.status_code == 429 or code == WEBHOOK_RATE_CODE[kind]:
        raise WebhookRateLimited(kind, response.headers.get("Retry-After", ""))
    return response.is_success and code == 0, response.text[:120]


async def _send_bark(cfg: dict, title: str, text: str) -> Tuple[bool, str]:
    key = (cfg.get("key") or "").strip()
    if not key:
        return False, "缺少 bark key"
    server = (cfg.get("server") or "https://api.day.app").rstrip("/")
    url = f"{server}/{key}/{urllib.parse.quote(title)}/{urllib.parse.quote(text)}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url, params={"group": "douyin-monitor"})
        return (r.status_code == 200), f"HTTP {r.status_code}"


async def _send_dingtalk(cfg: dict, title: str, text: str) -> Tuple[bool, str]:
    webhook = (cfg.get("webhook") or "").strip()
    if not webhook:
        return False, "缺少 webhook"
    secret = (cfg.get("secret") or "").strip()
    keyword = (cfg.get("keyword") or "").strip()
    if secret:
        ts = str(round(time.time() * 1000))
        sign = base64.b64encode(hmac.new(
            secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest())
        webhook += f"&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
    content = f"{keyword} {title}\n{text}".strip()
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(webhook, json={"msgtype": "text", "text": {"content": content}})
        return _webhook_result(r, "dingtalk")


async def _send_telegram(cfg: dict, title: str, text: str) -> Tuple[bool, str]:
    token = (cfg.get("bot_token") or "").strip()
    chat_id = (cfg.get("chat_id") or "").strip()
    if not token or not chat_id:
        return False, "缺少 bot_token / chat_id"
    api = (cfg.get("api_base") or "https://api.telegram.org").rstrip("/")
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{api}/bot{token}/sendMessage",
                         json={"chat_id": chat_id, "text": f"{title}\n{text}",
                               "disable_web_page_preview": True})
        return (r.status_code == 200), f"HTTP {r.status_code} {r.text[:80]}"


async def _send_feishu(cfg: dict, title: str, text: str) -> Tuple[bool, str]:
    webhook = (cfg.get("webhook") or "").strip()
    if not webhook:
        return False, "缺少 webhook"
    payload = {"msg_type": "text", "content": {"text": f"{cfg.get('keyword', '')} {title}\n{text}".strip()}}
    secret = (cfg.get("secret") or "").strip()
    if secret:
        timestamp = str(int(time.time()))
        payload.update(timestamp=timestamp, sign=base64.b64encode(hmac.new(
            f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode())
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(webhook, json=payload)
        return _webhook_result(response, "feishu")


_SENDERS = {"bark": _send_bark, "dingtalk": _send_dingtalk, "telegram": _send_telegram,
            "feishu": _send_feishu}
CHANNEL_TYPES = tuple(_SENDERS.keys())


async def send_one(ch_type: str, config: dict, title: str, text: str, *, defer_if_limited: bool = False) -> Tuple[bool, str]:
    fn = _SENDERS.get(ch_type)
    if not fn:
        return False, f"未知渠道类型: {ch_type}"
    try:
        key = robot_key(ch_type, config.get("webhook") or "")
        if key is None:
            return await fn(config, title, text)
        lease = await reserve_webhook(key, wait_seconds=0 if defer_if_limited else TIMEOUT + 4)
        delay = WEBHOOK_INTERVAL[ch_type]
        try:
            return await asyncio.wait_for(fn(config, title, text), timeout=TIMEOUT)
        except WebhookRateLimited as exc:
            delay = exc.cooldown
            raise
        finally:
            finish_webhook(key, lease, delay)
    except (WebhookBusy, WebhookRateLimited) as exc:
        if defer_if_limited:
            raise
        return False, str(exc)
    except Exception as e:
        return False, repr(e)


async def notify_all(channels: List[dict], title: str, text: str):
    """channels: [{type, config(dict)} ...]。逐个发送,失败不影响其它。"""
    for ch in channels:
        await send_one(ch["type"], ch.get("config") or {}, title, text)
