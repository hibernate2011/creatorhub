import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import app.db as db
from app.config import Config
from app.engine import MonitorEngine
from app.models import CommentAnalysis, CommentRecord, CommentWatch, DouyinAccount, KeywordCollectionContent, KeywordCollectionJob
from sqlmodel import select

from app import comment_ai


class KeywordCollectionCommentWatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        previous_engine = db._engine
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        engine = db.init_db(str(Path(temp.name) / "collection-watch.db"))
        self.addCleanup(setattr, db, "_engine", previous_engine)
        self.addCleanup(engine.dispose)

    def add_work(self, platform, aweme_id, *, xsec_token="", xsec_source="", source_url=""):
        with db.get_session() as session:
            cookie = {"name": "web_session", "value": "fixture"}
            if platform == "rednote":
                cookie.update(value="fixture-rednote-session", domain=".rednote.com")
            account = DouyinAccount(platform=platform, status="active", storage_state=json.dumps({
                "cookies": [cookie],
            }))
            session.add(account)
            session.flush()
            job = KeywordCollectionJob(platform=platform, account_id=account.id)
            session.add(job)
            session.flush()
            work = KeywordCollectionContent(
                job_id=job.id, platform=platform, aweme_id=aweme_id, desc="采集到的作品标题",
                xsec_token=xsec_token, xsec_source=xsec_source, source_url=source_url,
            )
            session.add(work)
            session.flush()
            result = (f"/api/collections/{job.id}/contents/{work.id}/comment-watch", account.id)
            session.commit()
            return result

    async def test_create_applies_ai_policy_atomically_and_rejects_duplicate_watch(self):
        from app.main import app

        work_id = "7461234567890123456"
        path, account_id = self.add_work("douyin", work_id)
        destination = {
            "id": "fixture", "name": "门店群", "type": "feishu", "threshold": 0.85,
            "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/fixture",
        }
        policy = {"auto_forward": "on", "destination_mode": "custom", "destinations": [destination]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            failed = await client.post(path, json={"ai_policy": {**policy, "auto_analyze": "on"}})
            self.assertEqual(failed.status_code, 422)
            self.assertEqual((await client.get("/api/comment-watches")).json(), [])
            response = await client.post(path, json={
                "account_id": account_id, "interval_seconds": 90, "ai_policy": policy,
            })
            self.assertEqual(response.status_code, 200, response.text)
            watch = response.json()
            self.assertEqual((watch["kind"], watch["mode"], watch["aweme_id"]), ("video", "public", work_id))
            self.assertEqual((watch["account_id"], watch["interval_seconds"]), (account_id, 90))
            self.assertEqual(watch["title"], "采集到的作品标题")
            masked = {**destination, "webhook": "********", "secret": "", "keyword": "", "enabled": True}
            self.assertEqual(watch["ai_policy"], {**policy, "auto_analyze": "inherit", "destinations": [masked]})
            self.assertEqual((await client.post(path, json={})).status_code, 409)
            self.assertEqual((await client.post("/api/comment-watches", json={
                "url_or_id": work_id, "kind": "video",
            })).status_code, 409)
            self.assertEqual((await client.get("/api/comment-watches?platform=douyin")).json(), [watch])
        with db.get_session() as session:
            saved = session.get(CommentWatch, watch["id"])
            self.assertEqual(json.loads(saved.ai_policy)["destinations"][0]["webhook"], destination["webhook"])

    async def test_platform_specific_target_and_account_data_are_preserved(self):
        from app.main import app

        cases = [
            ("xhs", "652123456789012345678901", "xhs-token", "", ""),
            ("kuaishou", "3xexample", "", "", ""),
            ("shipinhao", "1234567890123", "finder-nonce", "", ""),
            ("tiktok", "7361234567890123456", "", "fixture.creator",
             "https://www.tiktok.com/@fixture.creator/video/7361234567890123456"),
            ("bilibili", "1077998733285138440", "", "",
             "https://www.bilibili.com/opus/1077998733285138440"),
            ("instagram", "DdL0sPNt5jn", "", "",
             "https://www.instagram.com/reel/DdL0sPNt5jn/"),
            ("twitter", "1900000000000000000", "", "fixture_author",
             "https://x.com/fixture_author/status/1900000000000000000"),
            ("rednote", "67a1b2c3d4e5f60000000001", "rednote-token", "",
             "https://www.rednote.com/zh/discovery/item/67a1b2c3d4e5f60000000001"),
        ]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            for platform, aweme_id, token, author, source_url in cases:
                with self.subTest(platform=platform):
                    source = "pc_user" if platform == "rednote" else ""
                    path, account_id = self.add_work(platform, aweme_id, xsec_token=token, source_url=source_url,
                                                     xsec_source=source)
                    wrong_job_path = f"/api/collections/999/contents/{path.split('/contents/', 1)[1]}"
                    self.assertEqual((await client.post(wrong_job_path, json={})).status_code, 404)
                    if platform in {"xhs", "tiktok", "instagram", "rednote", "twitter"}:
                        self.assertEqual((await client.post(path, json={})).status_code, 400)
                    selected_account = None if platform == "bilibili" else account_id
                    response = await client.post(path, json={"account_id": selected_account})
                    self.assertEqual(response.status_code, 200, response.text)
                    if platform in {"bilibili", "instagram", "rednote", "twitter"}:
                        duplicate = await client.post("/api/comment-watches", json={
                            "platform": platform, "url_or_id": source_url, "account_id": selected_account,
                        })
                        self.assertEqual(duplicate.status_code, 409, duplicate.text)
                    with db.get_session() as session:
                        watch = session.get(CommentWatch, response.json()["id"])
                        self.assertEqual((watch.aweme_id, watch.sec_uid, watch.xsec_token), (aweme_id, author, token))
                        self.assertEqual(watch.account_id, None if platform == "shipinhao" else selected_account)
                        if platform in {"instagram", "twitter"}:
                            self.assertEqual(watch.work_url, source_url)
                        if platform == "rednote":
                            self.assertEqual(watch.xsec_source, source)

    async def test_twitter_scan_queues_leads_with_the_original_tweet_and_region(self):
        from app.main import app

        tweet_id = "1900000000000000000"
        tweet_url = f"https://x.com/fixture_author/status/{tweet_id}"
        path, account_id = self.add_work("twitter", tweet_id, source_url=tweet_url)
        with db.get_session() as session:
            config = comment_ai.settings(session)
        config.update(api_key="test-key", auto_analyze=True)
        comment_ai.save_settings(comment_ai.SettingsIn(**config))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            response = await client.post(path, json={"account_id": account_id})
            self.assertEqual(response.status_code, 200, response.text)
            watch_id = response.json()["id"]
            engine = MonitorEngine(Config(), SimpleNamespace(
                anon_identity=object, identity_for=lambda _: object()))
            syndication = SimpleNamespace(
                author_unique_id="fixture_author", author_name="作者", desc="推文标题", avatar="", cover="")
            replies = [{"comment_id": "1900000000000000001", "create_time": 1,
                        "text": "请问价格", "user_sec_uid": "buyer_x", "ip_label": "Seattle"}]
            with patch("app.engine.comment_watches_ext.TwitterClient.fetch_tweet_syndication",
                       new=AsyncMock(return_value=(syndication, {}, ""))), patch.object(
                           engine, "_tw_read_comments", new=AsyncMock(return_value=(replies, "", "api"))):
                result = await engine._scan_comment_watch_locked(watch_id)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["new_comments"], 1)
            rows = (await client.get(f"/api/comments?platform=twitter&watch_id={watch_id}")).json()
            self.assertEqual((rows[0]["work_title"], rows[0]["work_url"], rows[0]["ip_label"]),
                             ("推文标题", tweet_url, "Seattle"))
        with db.get_session() as session:
            row = session.exec(select(CommentRecord)).one()
            analysis = session.get(CommentAnalysis, comment_ai.analysis_id(row))
            self.assertEqual((analysis.status, analysis.work_url), ("queued", tweet_url))

    async def test_xhs_watch_uses_the_collected_token_source_when_scanning(self):
        from app.main import app

        path, account_id = self.add_work("xhs", "652123456789012345678901", xsec_token="fixture-token",
                                         xsec_source="pc_user")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            response = await client.post(path, json={"account_id": account_id})
            self.assertEqual(response.status_code, 200, response.text)
        cfg = Config()
        cfg.engine.xhs_read_mode = "browser"
        browser = SimpleNamespace(anon_identity=lambda: None, identity_for=lambda _: None,
                                  visible_page=MagicMock())
        engine = MonitorEngine(cfg, browser)
        with patch("app.engine.comment_watches_ext.fetch_xhs_comments", new=AsyncMock(return_value=([], ""))) as fetch:
            result = await engine._scan_comment_watch_locked(response.json()["id"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(fetch.await_args.kwargs["xsec_token"], "fixture-token")
        self.assertEqual(fetch.await_args.kwargs["xsec_source"], "pc_user")
