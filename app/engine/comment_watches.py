"""评论监控主流程及抖音、快手、视频号、TikTok 评论监听拉取。"""
from __future__ import annotations

from datetime import datetime
import time

from sqlalchemy import or_, update
from sqlmodel import select

from app.browser import fetch_channels_comments, fetch_channels_works, fetch_comments, fetch_creator_comments, fetch_ks_comments, fetch_ks_videos, fetch_videos
from app.browser.douyin_detail import fetch_douyin_video_detail
from app.browser.tiktok_fetcher import fetch_tiktok_comments, fetch_tiktok_profile_works, fetch_tiktok_work_title
from app.db import get_session
from app.engine.monitor_helpers import log
from app.models import AccountWork, CommentRecord, CommentWatch, DouyinAccount, MonitorTarget
from app.platforms.channels import ChannelsProtocolClient, flatten_channels_comments, parse_channels_comment, parse_channels_feed, parse_self_user as parse_channels_self_user
from app.platforms.channels.resolve import work_page_url as channels_work_url
from app.platforms.douyin import DouyinClient, cookie_from_state as dy_cookie_from_state, parse_comment, parse_creator_comment
from app.platforms.kuaishou import flatten_ks_comments, parse_ks_comment, parse_ks_feed, parse_self_user as parse_ks_self_user
from app.platforms.rednote import RednoteApiError, has_web_session as rednote_has_web_session
from app.platforms.tiktok import TikTokNativeClient, parse_tiktok_aweme, parse_tiktok_comment
from app.platforms.tiktok.urls import video_url as tiktok_video_url
from app.platforms.xhs import XhsApiError
from app.risk import OperationKind, RiskCategory, classify_platform_error
from app.settings import get_setting
from app.transport_matrix import douyin_client_environment, resolve_transport


class CommentWatchMixin:
    # ── 独立评论监控(CommentWatch)──
    async def _scan_comment_watches(self):
        due = []
        with get_session() as s:
            ws = s.exec(select(CommentWatch).where(CommentWatch.enabled == True)).all()  # noqa: E712
            for w in ws:
                if self._periodic_due(w, "comment_watch"):
                    due.append(w.id)
        for wid in due:
            await self.scan_comment_watch(wid)

    async def scan_comment_watch(self, watch_id: int) -> dict:
        key = f"cw:{watch_id}"
        if key in self._inflight:
            return {"ok": True, "new_comments": 0, "skipped": "正在抓取中"}
        self._inflight.add(key)
        try:
            with get_session() as s:
                w = s.get(CommentWatch, watch_id)
                account_id = w.account_id if w else None
            return await self._guarded_read_dict(
                account_id, OperationKind.READ_HEAVY, key,
                lambda: self._scan_comment_watch_locked(watch_id))
        finally:
            self._inflight.discard(key)

    async def _scan_comment_watch_locked(self, watch_id: int) -> dict:
        with get_session() as s:
            w = s.get(CommentWatch, watch_id)
            if not w:
                return {"ok": False, "error": "watch not found"}
            first_scan = w.last_scan_at is None
            platform = w.platform
            kind, mode = w.kind, w.mode
            aweme_id, sec_uid = w.aweme_id, w.sec_uid
            work_url = w.work_url
            xsec_token = w.xsec_token or ""
            xsec_source = w.xsec_source or ""
            name = w.title or aweme_id or (sec_uid[:12] if sec_uid else "watch")
            state = creator_state = proxy = ""
            ua = self.cfg.engine.user_agent
            identity = (
                self.browser.anon_identity()
                if callable(getattr(self.browser, "anon_identity", None))
                else None
            )
            has_creator = False
            acc = None
            if w.account_id:
                acc = s.get(DouyinAccount, w.account_id)
                if acc:
                    if platform == "tiktok" and (
                            acc.platform != "tiktok" or acc.status != "active"):
                        msg = "绑定的 TikTok 账号不存在或登录态已失效"
                        w.last_scan_at = datetime.utcnow()
                        w.last_error = msg
                        s.add(w); s.commit()
                        return {"ok": False, "new_comments": 0, "error": msg}
                    if (platform == "tiktok"
                            and not (acc.storage_state
                                     or acc.creator_storage_state)):
                        msg = "绑定的 TikTok 账号缺少登录会话，请重新登录"
                        w.last_scan_at = datetime.utcnow()
                        w.last_error = msg
                        s.add(w); s.commit()
                        return {"ok": False, "new_comments": 0, "error": msg}
                    if self._proxy_bad(acc):
                        msg = "账号代理标记为不可用(proxy bad),已跳过以免暴露真实 IP"
                        w2 = s.get(CommentWatch, watch_id)
                        if w2:
                            w2.last_scan_at = datetime.utcnow()
                            w2.last_error = msg
                            s.add(w2); s.commit()
                        return {"ok": False, "new_comments": 0, "error": msg, "skipped": True}
                    state = acc.storage_state or acc.creator_storage_state or ""
                    creator_state = acc.creator_storage_state or ""
                    ua = acc.ua or self.cfg.engine.user_agent
                    proxy = acc.proxy or ""
                    has_creator = bool(creator_state)
                    identity = (
                        self.browser.identity_for(acc)
                        if callable(getattr(self.browser, "identity_for", None))
                        else None
                    )
                elif platform == "tiktok":
                    msg = "绑定的 TikTok 账号不存在或登录态已失效"
                    w.last_scan_at = datetime.utcnow()
                    w.last_error = msg
                    s.add(w); s.commit()
                    return {"ok": False, "new_comments": 0, "error": msg}
            if platform == "tiktok" and not w.account_id:
                msg = "TikTok 评论监控必须绑定已登录账号"
                w.last_scan_at = datetime.utcnow()
                w.last_error = msg
                s.add(w); s.commit()
                return {"ok": False, "new_comments": 0, "error": msg}
            if platform == "instagram":
                acc_ig = s.get(DouyinAccount, w.account_id) if w.account_id else None
                if not w.account_id or not acc_ig or acc_ig.platform != "instagram" or acc_ig.status != "active" or not state:
                    msg = "Instagram 评论监控必须绑定已登录且状态正常的 Instagram 账号"
                    w.last_scan_at = datetime.utcnow()
                    w.last_error = msg
                    s.add(w); s.commit()
                    return {"ok": False, "new_comments": 0, "error": msg}
            if platform == "twitter":
                acc_tw = s.get(DouyinAccount, w.account_id) if w.account_id else None
                if not w.account_id or not acc_tw or acc_tw.platform != "twitter" or acc_tw.status != "active" or not state:
                    msg = "Twitter 评论监控必须绑定已登录且状态正常的 Twitter 账号"
                    w.last_scan_at = datetime.utcnow()
                    w.last_error = msg
                    s.add(w); s.commit()
                    return {"ok": False, "new_comments": 0, "error": msg}
            if platform == "rednote" and (
                not acc or acc.platform != "rednote" or acc.status != "active"
                or not rednote_has_web_session(acc.storage_state)
            ):
                msg = "海外小红书评论监控需要绑定一个已登录主站的海外小红书账号"
                w.last_scan_at = datetime.utcnow()
                w.last_error = msg
                s.add(w); s.commit()
                return {"ok": False, "new_comments": 0, "error": msg}

        # 公开抖音读取可复用一个带 Cookie 的 Web API 会话；创作中心仍由
        # 浏览器处理。没有登录态时 hybrid 继续允许匿名浏览器回退。
        api_client = None
        if (platform == "douyin" and mode == "public"
                and self.cfg.engine.douyin_read_mode in {"api", "hybrid"}):
            cookie = dy_cookie_from_state(state)
            if cookie:
                api_client = DouyinClient(
                    cookie, ua,
                    timeout=self.cfg.engine.request_timeout_seconds,
                    proxy=proxy,
                    **douyin_client_environment(identity))

        error = ""
        total_new = 0
        author = None
        if platform == "xhs" and not state:
            msg = "小红书评论监控需要绑定一个已登录的小红书账号(笔记页需登录)"
            with get_session() as s:
                w = s.get(CommentWatch, watch_id)
                if w:
                    w.last_scan_at = datetime.utcnow()
                    w.last_error = msg
                    s.add(w); s.commit()
            return {"ok": False, "new_comments": 0, "error": msg}
        try:
            if platform == "xhs" and kind == "user":
                total_new, author = await self._cw_xhs_creator(
                    watch_id, identity, state, sec_uid,
                    xsec_token, name, first_scan, proxy)
            elif platform == "xhs":   # 单条笔记
                total_new, author = await self._cw_xhs_note(
                    watch_id, identity, state, aweme_id,
                    xsec_token, name, first_scan, proxy, xsec_source=xsec_source)
            elif platform == "rednote" and kind == "user":
                total_new, author = await self._cw_rednote_creator(
                    watch_id, identity, state, sec_uid,
                    xsec_token, name, first_scan, proxy, xsec_source=xsec_source)
            elif platform == "rednote":
                total_new, author = await self._cw_rednote_note(
                    watch_id, identity, state, aweme_id,
                    xsec_token, name, first_scan, proxy, xsec_source=xsec_source)
            elif platform == "kuaishou" and kind == "user":
                total_new, author = await self._cw_ks_user(watch_id, identity, sec_uid,
                                                           name, first_scan)
            elif platform == "kuaishou":   # 单条作品
                total_new, author = await self._cw_ks_video(watch_id, identity, aweme_id,
                                                            name, first_scan)
            elif platform == "tiktok" and kind == "user":
                total_new, author = await self._cw_tiktok_user(
                    watch_id, identity, state, sec_uid, name, first_scan, proxy)
            elif platform == "tiktok":
                total_new, author = await self._cw_tiktok_video(
                    watch_id, identity, state, aweme_id, sec_uid, name,
                    first_scan, proxy)
            elif platform == "shipinhao" and kind == "user":
                total_new, author = await self._cw_channels_user(
                    watch_id, identity, state, sec_uid, name, first_scan)
            elif platform == "shipinhao":
                total_new, author = await self._cw_channels_video(
                    watch_id, identity, state, aweme_id, xsec_token, name, first_scan)
            elif platform == "bilibili" and kind == "user":
                total_new, author = await self._cw_bilibili_user(
                    watch_id, identity, state, ua, proxy, sec_uid, name, first_scan)
            elif platform == "bilibili":
                total_new, author = await self._cw_bilibili_video(
                    watch_id, identity, state, ua, proxy, aweme_id, name, first_scan)
            elif platform == "instagram" and kind == "user":
                total_new, author = await self._cw_instagram_user(
                    watch_id, identity, state, ua, proxy, sec_uid, name, first_scan)
            elif platform == "instagram":
                total_new, author = await self._cw_instagram_video(
                    watch_id, identity, state, ua, proxy, aweme_id, name, first_scan, work_url=work_url)
            elif platform == "twitter" and kind == "user":
                total_new, author = await self._cw_twitter_user(
                    watch_id, identity, state, ua, proxy, sec_uid, name, first_scan)
            elif platform == "twitter":
                total_new, author = await self._cw_twitter_video(
                    watch_id, identity, state, ua, proxy, aweme_id, name, first_scan, screen_name=sec_uid)
            elif kind == "user" and mode == "creator":
                total_new, author = await self._cw_creator(watch_id, identity, has_creator,
                                                           name, first_scan)
            elif kind == "user":
                total_new, author = await self._cw_user_public(
                    watch_id, identity, sec_uid, name, first_scan,
                    api_client=api_client)
            else:  # video
                total_new, author = await self._cw_video(
                    watch_id, identity, aweme_id, name, first_scan,
                    api_client=api_client)
        except (XhsApiError, RednoteApiError) as e:
            error = e
        except Exception as e:
            category, _signal = classify_platform_error(e)
            error = (e if category in {
                RiskCategory.RISK, RiskCategory.AUTH, RiskCategory.NETWORK
            } else repr(e))
            log.warning("评论监控 %s 失败: %s", watch_id, e)

        with get_session() as s:
            w = s.get(CommentWatch, watch_id)
            if w:
                w.last_scan_at = datetime.utcnow()
                w.last_error = str(error or "")
                if author:
                    if (
                        not w.title
                        or (w.platform == "instagram" and w.title.startswith("IG "))
                        or (w.platform == "twitter" and w.title.startswith("推文 "))
                    ):
                        w.title = author.get("nickname") or w.title
                    ava = (author.get("avatar_thumb") or {}).get("url_list") or []
                    if ava and ava[0] and (not w.avatar or w.platform in {"instagram", "twitter"}):
                        w.avatar = ava[0]
                w.comment_count = len(s.exec(select(CommentRecord.id)
                                             .where(CommentRecord.watch_id == watch_id)).all())
                s.add(w); s.commit()
        return {"ok": not error, "new_comments": total_new, "error": error}

    def _comment_work_title(self, platform, aweme_id, work_desc=""):
        from ..comment_ai import find_work_title
        with get_session() as s:
            return find_work_title(s, platform, aweme_id, work_desc)

    async def load_douyin_comment_title(self, aweme_id: str, watch_id: int, comment_id: str) -> str:
        """Read the work itself when old comments lack a stored title."""
        from ..comment_ai import clean_work_title
        from ..platforms.douyin.web_session import context_key, validated_detail_context

        if not aweme_id.isascii() or not aweme_id.isdecimal():
            return ""
        with get_session() as s:
            watch = s.get(CommentWatch, watch_id) if watch_id else None
            account_id = watch.account_id if watch and watch.platform == "douyin" else None
            if not account_id and not watch_id:
                source = s.exec(select(CommentRecord).where(
                    CommentRecord.platform == "douyin", CommentRecord.aweme_id == aweme_id,
                    CommentRecord.comment_id == comment_id,
                    or_(CommentRecord.watch_id == 0, CommentRecord.watch_id.is_(None))
                ).order_by(CommentRecord.id)).first()
                target = s.get(MonitorTarget, source.target_id) if source and source.target_id else None
                if target and target.platform == "douyin":
                    account_id = target.account_id
                elif source and source.watch_id == 0:
                    account_id = s.exec(select(AccountWork.account_id).where(
                        AccountWork.platform == "douyin", AccountWork.item_id == aweme_id
                    ).order_by(AccountWork.id.desc())).first()

        async def read_detail():
            account = self._load_account(account_id)
            if account_id and (not account or account.platform != "douyin"
                               or account.status != "active" or self._proxy_bad(account)):
                return "", "account_unavailable"
            mode = resolve_transport(self.cfg, "douyin", "public_monitor", account)["effective_mode"]
            error = "no_cookie"
            if account and mode in {"api", "hybrid"}:
                state = account.storage_state or account.creator_storage_state or ""
                cookie = dy_cookie_from_state(state) or account.cookie
                if cookie:
                    ua = account.ua or self.cfg.engine.user_agent
                    detail_context = validated_detail_context(
                        get_setting(context_key(account_id), ""), account, state, ua)
                    client = DouyinClient(cookie, ua, timeout=self.cfg.engine.request_timeout_seconds,
                                          proxy=account.proxy, storage_state=state,
                                          detail_context=detail_context, **douyin_client_environment(account))
                    async with client.session_scope():
                        detail = await client.fetch_video_detail(aweme_id) or {}
                    title = clean_work_title(detail.get("desc"), aweme_id)
                    if title:
                        return title, ""
                    error = client.last_error or "missing_work_title"
                    category, _ = classify_platform_error(error)
                    if (category in {RiskCategory.AUTH, RiskCategory.RISK, RiskCategory.NETWORK}
                            or error == "aweme_unavailable"):
                        return "", error
            if mode in {"browser", "hybrid"}:
                identity, _ = self._identity_proxy(account)
                detail, error = await fetch_douyin_video_detail(
                    self.browser, identity, aweme_id, timeout_ms=3000)
                title = clean_work_title(detail.get("desc"), aweme_id)
                return title, error or ("" if title else "missing_work_title")
            return "", error

        title, _ = await self.guarded_read_pair(
            account_id, OperationKind.READ_LIGHT, "comment-ai-title", read_detail, empty_result="")
        return title

    async def _ingest(self, watch_id, aweme_id, fresh, name, work_desc, first_scan,
                      platform="douyin", *, work_url="", update_watch_title=True) -> int:
        """fresh: parse_comment 结果(无 aweme_id)。入库 + 按时间水位线推送。"""
        from ..comment_ai import clean_work_title, enqueue

        work_desc = clean_work_title(work_desc, aweme_id)
        if not fresh and not work_desc and not work_url:
            return 0
        with get_session() as s:
            if work_url:
                s.execute(update(CommentRecord).where(
                    CommentRecord.watch_id == watch_id, CommentRecord.platform == platform,
                    CommentRecord.aweme_id == aweme_id, CommentRecord.work_url != work_url,
                ).values(work_url=work_url))
            if work_desc and update_watch_title:
                watch = s.get(CommentWatch, watch_id)
                if (watch and watch.kind == "video" and watch.aweme_id == aweme_id
                        and watch.platform == platform and watch.title != work_desc):
                    watch.title = work_desc
            if not fresh:
                s.commit()
                return 0
            times = s.exec(select(CommentRecord.create_time)
                           .where(CommentRecord.watch_id == watch_id)
                           .where(CommentRecord.aweme_id == aweme_id)).all()
            prev_max = max([t for t in times if t] or [0])
            ai_rows = []
            for c in fresh:
                record = CommentRecord(platform=platform, watch_id=watch_id,
                                       aweme_id=aweme_id, work_url=work_url,
                                       **{**c, "work_title": work_desc})
                s.add(record)
                ai_rows.append(record)
            enqueue(s, ai_rows, automatic=True)
            s.commit()
        newer = [c for c in fresh if c["create_time"] > prev_max]
        if not first_scan and newer:
            await self._notify_comments(name, work_desc or name, newer)
        return len(fresh)

    def _comment_watch_settings(self, watch_id: int) -> dict:
        cfg = self.cfg.engine
        with get_session() as s:
            watch = s.get(CommentWatch, watch_id)
            return {
                "recent_works": ((watch.recent_works if watch else 0)
                                 or cfg.comment_recent_works),
                "recent_days": ((watch.recent_days if watch else 0)
                                or cfg.comment_recent_days),
                "max_scrolls": ((watch.max_scrolls if watch else 0)
                                or cfg.comment_max_scrolls),
            }

    async def _cw_video(self, watch_id, identity, aweme_id, name, first_scan,
                        api_client=None, work_desc=""):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        work_desc = self._comment_work_title("douyin", aweme_id, work_desc)
        with get_session() as s:
            known = set(s.exec(select(CommentRecord.comment_id)
                               .where(CommentRecord.watch_id == watch_id)
                               .where(CommentRecord.aweme_id == aweme_id)).all())
        raw, err = [], ""
        if api_client is not None and cfg.douyin_read_mode in {"api", "hybrid"}:
            try:
                async with api_client.session_scope():
                    raw = await api_client.fetch_all_comments(aweme_id)
                fresh = [c for c in (parse_comment(rc) for rc in raw)
                         if c and c["comment_id"] not in known]
                # 非空响应即使全部是已知评论也代表 API 成功，不应重复打开浏览器。
                if raw or not api_client.last_error:
                    if raw and not work_desc:
                        try:
                            async with api_client.session_scope():
                                detail = await api_client.fetch_video_detail(aweme_id)
                            work_desc = (detail or {}).get("desc") or ""
                            if (not work_desc and cfg.douyin_read_mode == "hybrid"
                                    and classify_platform_error(api_client.last_error)[0] not in {
                                        RiskCategory.RISK, RiskCategory.AUTH, RiskCategory.NETWORK
                                    }):
                                detail, _ = await fetch_douyin_video_detail(
                                    self.browser, identity, aweme_id, timeout_ms=3000)
                                work_desc = detail.get("desc") or ""
                        except Exception as exc:
                            log.debug("评论监控(抖音作品)读取标题失败 %s: %s", aweme_id, exc)
                    return (await self._ingest(
                        watch_id, aweme_id, fresh, name, work_desc,
                        first_scan), None)
                err = api_client.last_error or "empty_response"
                if cfg.douyin_read_mode == "api":
                    raise RuntimeError(f"douyin_api_comments:{err}")
                print(f"[dy-comment-watch] API 空响应({err}),回退浏览器")
            except Exception as exc:
                err = f"api:{type(exc).__name__}"
                if cfg.douyin_read_mode == "api":
                    log.info("评论监控(视频)API 失败 %s: %s", aweme_id, exc)
                    raise
        elif cfg.douyin_read_mode == "api":
            raise RuntimeError("douyin_api_comments:no_cookie")
        metadata = {}
        raw, browser_err = await fetch_comments(
            self.browser, identity, aweme_id, known,
            max_scrolls=settings["max_scrolls"],
            block_media=cfg.block_media_resources,
            work_metadata=None if work_desc else metadata)
        err = browser_err or err
        if err:
            log.info("评论监控(视频)%s: %s", aweme_id, err)
        fresh = [c for c in (parse_comment(rc) for rc in raw) if c]
        n = await self._ingest(watch_id, aweme_id, fresh, name,
                               work_desc or metadata.get("title", ""), first_scan)
        return n, None

    async def _cw_user_public(self, watch_id, identity, sec_uid, name, first_scan,
                              api_client=None):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        items, author, err = [], None, ""
        if api_client is not None and cfg.douyin_read_mode in {"api", "hybrid"}:
            try:
                async with api_client.session_scope():
                    items = await api_client.fetch_all_video_list(sec_uid)
                    author = await api_client.fetch_profile(sec_uid)
                if not items:
                    err = api_client.last_error or "empty_response"
                if not items and cfg.douyin_read_mode == "hybrid":
                    print(f"[dy-comment-watch] API 作品空响应({err}),回退浏览器")
            except Exception as exc:
                err = f"api:{type(exc).__name__}"
                if cfg.douyin_read_mode == "api":
                    log.info("评论监控(账号)API 失败 %s: %s", sec_uid, exc)
                    raise
        if not items and cfg.douyin_read_mode != "api":
            items, author, browser_err = await fetch_videos(
                self.browser, identity, sec_uid, set(), max_scrolls=4,
                block_media=cfg.block_media_resources)
            err = browser_err or err
        elif not items and err and cfg.douyin_read_mode == "api":
            raise RuntimeError(f"douyin_api_works:{err}")
        if err:
            log.info("评论监控(账号)%s: %s", sec_uid, err)
        cutoff = int(time.time()) - settings["recent_days"] * 86400
        works = []
        for it in items:
            aid = str(it.get("aweme_id") or "")
            ct = int(it.get("create_time") or 0)
            if aid and (not cutoff or not ct or ct >= cutoff):
                works.append((aid, (it.get("desc") or "")))
        works = works[:settings["recent_works"]]
        total = 0
        for aid, desc in works:
            with get_session() as s:
                known = set(s.exec(select(CommentRecord.comment_id)
                                   .where(CommentRecord.watch_id == watch_id)
                                   .where(CommentRecord.aweme_id == aid)).all())
            n, _ = await self._cw_video(
                watch_id, identity, aid, name, first_scan,
                api_client=api_client, work_desc=desc)
            total += n
        return total, author

    # ── 快手评论监控(浏览器拦截 GraphQL)──
    async def _cw_ks_video(self, watch_id, identity, photo_id, name, first_scan):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        work_desc = self._comment_work_title("kuaishou", photo_id)
        metadata = {}
        with get_session() as s:
            known = set(s.exec(select(CommentRecord.comment_id)
                               .where(CommentRecord.watch_id == watch_id)
                               .where(CommentRecord.aweme_id == photo_id)).all())
        raw, err = await fetch_ks_comments(self.browser, identity, photo_id, known,
                                           max_scrolls=settings["max_scrolls"],
                                           block_media=cfg.block_media_resources,
                                           work_metadata=None if work_desc else metadata)
        if err:
            log.info("评论监控(快手作品)%s: %s", photo_id, err)
        fresh = [c for c in (parse_ks_comment(rc) for rc in flatten_ks_comments(raw)) if c]
        n = await self._ingest(watch_id, photo_id, fresh, name,
                               work_desc or metadata.get("title", ""), first_scan,
                               platform="kuaishou")
        return n, None

    async def _cw_ks_user(self, watch_id, identity, user_id, name, first_scan):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        items, author, err = await fetch_ks_videos(self.browser, identity, user_id, set(),
                                                   max_scrolls=4,
                                                   block_media=cfg.block_media_resources)
        if err:
            log.info("评论监控(快手账号)%s: %s", user_id, err)
        works = []
        cutoff = int(time.time()) - settings["recent_days"] * 86400
        for feed in items:
            aw = parse_ks_feed(feed)
            if aw and (not aw.create_time or aw.create_time >= cutoff):
                works.append((aw.aweme_id, aw.desc))
                if len(works) >= settings["recent_works"]:
                    break
        total = 0
        for pid, desc in works:
            with get_session() as s:
                known = set(s.exec(select(CommentRecord.comment_id)
                                   .where(CommentRecord.watch_id == watch_id)
                                   .where(CommentRecord.aweme_id == pid)).all())
            raw, _e = await fetch_ks_comments(self.browser, identity, pid, known,
                                              max_scrolls=settings["max_scrolls"],
                                              block_media=cfg.block_media_resources)
            fresh = [c for c in (parse_ks_comment(rc) for rc in flatten_ks_comments(raw)) if c]
            total += await self._ingest(watch_id, pid, fresh, name, desc, first_scan,
                                        platform="kuaishou")
        author_dict = parse_ks_self_user(author) if author else None
        return total, ({"nickname": author_dict["nickname"],
                        "avatar_thumb": {"url_list": [author_dict["avatar"]]}}
                       if author_dict else None)

    # ── 视频号评论监控(优先微信 Finder 原生 pc_findergetcommentlist 协议)──
    async def _cw_channels_video(
            self, watch_id, identity, state, object_id, object_nonce_id,
            name, first_scan, work_desc="", proto_client=None):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        read_mode = str(getattr(cfg, "channels_read_mode", "auto") or "auto").lower()
        owns_client = False
        if proto_client is None and read_mode in {"auto", "protocol"}:
            proto_client = ChannelsProtocolClient.from_config(self.cfg)
            owns_client = proto_client is not None

        resolved_oid = str(object_id or "").strip()
        resolved_nid = str(object_nonce_id or "").strip()
        work_desc = self._comment_work_title("shipinhao", resolved_oid, work_desc)
        try:
            if proto_client is not None:
                if not resolved_oid.isdecimal() or not resolved_nid:
                    is_obj_or_share = resolved_oid.isdecimal() or resolved_oid.startswith("sph:")
                    detail_obj, detail_err = await proto_client.fetch_feed_detail(
                        object_id=resolved_oid if is_obj_or_share else "",
                        export_id="" if is_obj_or_share else resolved_oid,
                    )
                    if detail_obj:
                        resolved_oid = str(
                            detail_obj.get("id") or detail_obj.get("objectId") or resolved_oid
                        )
                        resolved_nid = str(detail_obj.get("objectNonceId") or resolved_nid)
                        aw_detail = parse_channels_feed(detail_obj)
                        if aw_detail:
                            work_desc = work_desc or aw_detail.desc
                        with get_session() as s:
                            w = s.get(CommentWatch, watch_id)
                            if w:
                                w.aweme_id = resolved_oid
                                if resolved_nid:
                                    w.xsec_token = resolved_nid
                                if aw_detail and not w.title:
                                    w.title = aw_detail.desc[:40] or aw_detail.author_name or w.title
                                s.add(w)
                                s.commit()
                    elif read_mode == "protocol" or not state:
                        raise RuntimeError(detail_err or "无法解析视频号作品详情(objectId/objectNonceId)")

                if resolved_oid.isdecimal() and resolved_nid:
                    with get_session() as s:
                        known = set(s.exec(select(CommentRecord.comment_id).where(
                            CommentRecord.watch_id == watch_id,
                            CommentRecord.aweme_id == resolved_oid)).all())
                    comments, _audit, proto_err = await proto_client.collect_comments(
                        resolved_oid,
                        resolved_nid,
                        limit=max(100, settings["max_scrolls"] * 20),
                        include_replies=True,
                        max_pages=max(2, settings["max_scrolls"]),
                    )
                    if comments or not proto_err or read_mode == "protocol" or not state:
                        if proto_err and not comments:
                            raise RuntimeError(proto_err)
                        fresh = [c for c in comments if c and c["comment_id"] not in known]
                        n = await self._ingest(
                            watch_id, resolved_oid, fresh, name,
                            work_desc, first_scan, platform="shipinhao",
                            work_url=channels_work_url(resolved_oid, resolved_nid))
                        return n, None
        finally:
            if owns_client and proto_client is not None:
                await proto_client.close()

        if read_mode == "protocol" or not state:
            raise RuntimeError(
                "视频号评论监控需要连接本机已登录微信，请先在「设置 → 视频号微信通道」开启一键本地代理并在微信内打开一次视频号页面"
            )
        with get_session() as s:
            known = set(s.exec(select(CommentRecord.comment_id).where(
                CommentRecord.watch_id == watch_id,
                CommentRecord.aweme_id == resolved_oid)).all())
        raw, err = await fetch_channels_comments(
            self.browser, identity, resolved_oid, known,
            max_scrolls=settings["max_scrolls"],
            block_media=cfg.block_media_resources)
        if err:
            log.info("评论监控(视频号作品)%s: %s", resolved_oid, err)
        fresh = [
            c for c in (parse_channels_comment(rc) for rc in flatten_channels_comments(raw))
            if c and c["comment_id"] not in known
        ]
        n = await self._ingest(
            watch_id, resolved_oid, fresh, name, work_desc,
            first_scan, platform="shipinhao",
            work_url=channels_work_url(resolved_oid, resolved_nid) if resolved_nid else "")
        return n, None

    async def _cw_channels_user(self, watch_id, identity, state, user_id, name, first_scan):
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        read_mode = str(getattr(cfg, "channels_read_mode", "auto") or "auto").lower()
        proto_client = (
            ChannelsProtocolClient.from_config(self.cfg)
            if read_mode in {"auto", "protocol"}
            else None
        )
        items: list = []
        author = None
        err = ""
        try:
            if proto_client is not None:
                items, author, err = await proto_client.fetch_user_feeds(
                    user_id,
                    max_results=max(10, settings["recent_works"]),
                    max_pages=4,
                )
            if not items and (proto_client is None or (read_mode == "auto" and err)):
                if state and read_mode != "protocol":
                    items, author, err = await fetch_channels_works(
                        self.browser, identity, set(),
                        max_scrolls=4,
                        block_media=cfg.block_media_resources)
                elif proto_client is None:
                    raise RuntimeError(
                        "视频号创作者评论监控需要连接本机已登录微信，请先在「设置 → 视频号微信通道」开启一键本地代理并在微信内打开一次视频号页面"
                    )
                elif err:
                    raise RuntimeError(err)

            cutoff = int(time.time()) - settings["recent_days"] * 86400
            works = []
            for feed in items:
                aw = parse_channels_feed(feed)
                if aw and (not aw.create_time or aw.create_time >= cutoff):
                    works.append((aw.aweme_id, aw.object_nonce_id, aw.desc))
                    if len(works) >= settings["recent_works"]:
                        break

            total = 0
            for oid, nid, desc in works:
                n, _ = await self._cw_channels_video(
                    watch_id, identity, state, oid, nid, name, first_scan,
                    work_desc=desc, proto_client=proto_client)
                total += n
            author_dict = parse_channels_self_user(author) if author else None
            return total, (
                {
                    "nickname": author_dict["nickname"],
                    "avatar_thumb": {"url_list": [author_dict["avatar"]]},
                }
                if author_dict and (author_dict.get("nickname") or author_dict.get("avatar"))
                else None
            )
        finally:
            if proto_client is not None:
                await proto_client.close()

    async def _cw_tiktok_video(self, watch_id, identity, state, aweme_id,
                               author_unique_id, name, first_scan, proxy,
                               work_desc=""):
        if not author_unique_id:
            raise RuntimeError("TikTok 评论监控缺少作品作者 @username")
        settings = self._comment_watch_settings(watch_id)
        work_desc = self._comment_work_title("tiktok", aweme_id, work_desc)
        metadata = {}
        with get_session() as s:
            known = set(s.exec(select(CommentRecord.comment_id).where(
                CommentRecord.watch_id == watch_id,
                CommentRecord.aweme_id == aweme_id)).all())
        mode = str(getattr(self.cfg.engine, "tiktok_read_mode", "native") or "native")
        raw: list = []
        native_error = ""
        target_url = tiktok_video_url(aweme_id, author_unique_id)
        if mode in {"auto", "native"}:
            client = TikTokNativeClient(
                storage_state=state, user_agent=identity.ua, proxy=proxy,
                locale=identity.locale, timezone=identity.timezone_id,
                viewport=(identity.viewport_w, identity.viewport_h), region="US",
                device_seed=identity.fp_seed or str(identity.account_id or "creatorhub"),
                timeout=max(10, int(self.cfg.engine.request_timeout_seconds)))
            native = await client.comments(
                aweme_id, count=300, include_replies=True,
                author_unique_id=author_unique_id, target_url=target_url)
            if native.compatible:
                raw = native.items
                native_error = native.error or ""
            elif mode == "native":
                raise RuntimeError(native.error or "TikTok 离线评论接口不可用")
            else:
                native_error = native.error or native.category
        if mode == "browser" or (mode == "auto" and not raw and native_error):
            raw, browser_error = await fetch_tiktok_comments(
                self.browser, identity, aweme_id, known, max_comments=300,
                max_scrolls=settings["max_scrolls"], include_replies=True,
                block_media=self.cfg.engine.block_media_resources,
                author_unique_id=author_unique_id, target_url=target_url,
                work_metadata=None if work_desc else metadata)
            if browser_error and not raw:
                raise RuntimeError(browser_error)
        elif raw and not work_desc:
            try:
                work_desc = await fetch_tiktok_work_title(self.browser, identity, aweme_id, target_url)
            except Exception as exc:
                log.debug("评论监控(TikTok 作品)读取标题失败 %s: %s", aweme_id, exc)
        fresh = [c for c in (parse_tiktok_comment(item) for item in raw)
                 if c and c["comment_id"] not in known]
        count = await self._ingest(
            watch_id, aweme_id, fresh, name, work_desc or metadata.get("title", ""), first_scan,
            platform="tiktok", work_url=target_url)
        return count, None

    async def _cw_tiktok_user(self, watch_id, identity, state, unique_id,
                              name, first_scan, proxy):
        settings = self._comment_watch_settings(watch_id)
        items, error = await fetch_tiktok_profile_works(
            self.browser, identity, unique_id, max_results=100, max_scrolls=4,
            block_media=self.cfg.engine.block_media_resources)
        if error and not items:
            raise RuntimeError(error)
        works = []
        cutoff = int(time.time()) - settings["recent_days"] * 86400
        for raw in items:
            aw = parse_tiktok_aweme(raw)
            if aw and (not aw.create_time or aw.create_time >= cutoff):
                works.append(aw)
        works.sort(key=lambda aw: (aw.create_time, aw.aweme_id), reverse=True)
        works = works[:settings["recent_works"]]
        total = 0
        for aw in works:
            count, _ = await self._cw_tiktok_video(
                watch_id, identity, state, aw.aweme_id,
                aw.author_unique_id or unique_id, name, first_scan, proxy, aw.desc)
            total += count
        first = works[0] if works else None
        author = ({"nickname": first.author_name,
                   "avatar_thumb": {"url_list": [first.avatar]}}
                  if first else None)
        return total, author

    async def _cw_creator(self, watch_id, identity, has_creator, name, first_scan):
        if not has_creator:
            log.warning("评论监控 %s 选创作中心,但账号无创作者登录态", watch_id)
            return 0, None
        cfg = self.cfg.engine
        settings = self._comment_watch_settings(watch_id)
        with get_session() as s:
            known = set(s.exec(select(CommentRecord.comment_id)
                               .where(CommentRecord.watch_id == watch_id)).all())
            times = s.exec(select(CommentRecord.create_time)
                           .where(CommentRecord.watch_id == watch_id)).all()
            prev_max = max([t for t in times if t] or [0])
        raw, err = await fetch_creator_comments(self.browser, identity, known,
                                                page_url=cfg.creator_comment_url,
                                                max_scrolls=settings["max_scrolls"],
                                                block_media=cfg.block_media_resources)
        if err:
            log.info("评论监控(创作中心): %s", err)
        fresh = [c for c in (parse_creator_comment(rc) for rc in raw) if c]
        if not fresh:
            return 0, None
        with get_session() as s:
            ai_rows = []
            for c in fresh:
                record = CommentRecord(watch_id=watch_id, **c)   # c 自带 aweme_id
                s.add(record)
                ai_rows.append(record)
            from ..comment_ai import enqueue
            enqueue(s, ai_rows, automatic=True)
            s.commit()
        newer = [c for c in fresh if c["create_time"] > prev_max]
        if not first_scan and newer:
            await self._notify_comments(name, "(创作中心)", newer)
        return len(fresh), None
