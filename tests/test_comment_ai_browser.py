"""Real browser acceptance against fixture APIs, never real credentials/robots."""
import os
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from patchright.sync_api import expect
from test_web_appearance_browser import ui, navigate, assert_editor_layout, assert_no_overflow, capture

pytestmark = [pytest.mark.local_cdp, pytest.mark.skipif(
    os.environ.get("CREATORHUB_RUN_LOCAL_CDP") != "1", reason="local UI acceptance is opt-in")]


@pytest.mark.parametrize("width,height", [(1440, 1000), (375, 812)])
def test_comment_ai_settings_selection_and_forward(ui, width, height):
    page, errors = ui
    page.set_viewport_size({"width": width, "height": height})
    page.add_script_tag(path=str(Path(__file__).parent / "fixtures/comment-ai-api.js"))
    navigate(page, "comments", mobile=width <= 860)
    page.get_by_role("button", name="AI 潜客分析配置", exact=True).click()
    dialog = page.get_by_role("dialog", name="AI 潜客分析与转发")
    expect(dialog).to_be_visible()
    dialog.get_by_role("textbox", name="OpenRouter API Key").fill("fixture-key")
    dialog.get_by_role("checkbox", name="抓取新评论后自动分析").check()
    dialog.get_by_role("checkbox", name="分析完成后按各目标阈值自动转发").check()
    dialog.get_by_role("group", name="默认提示词 · v1").get_by_role(
        "textbox", name="排除的业务或行业关键词").fill("二手车")
    dialog.get_by_role("button", name="添加领域提示词").click()
    rule = dialog.get_by_role("group", name="自定义提示词 · v1")
    rule.get_by_role("textbox", name="名称", exact=True).fill("装修客户")
    rule.get_by_role("textbox", name="判断标准").fill("询价或预约上门视为潜在客户")
    rule.get_by_role("textbox", name="排除的业务或行业关键词").fill("跨境物流，金融科技")
    rule.get_by_role("button", name="适用平台", exact=True).click()
    expect(page.get_by_role("option", name="哔哩哔哩", exact=True)).to_be_visible()
    expect(page.get_by_role("option", name="海外小红书", exact=True)).to_be_visible()
    page.get_by_role("option", name="Instagram", exact=True).click()
    rule.locator('select[multiple]').select_option(["21", "22"])
    assert rule.locator('select[multiple]').evaluate("el => el.selectedOptions.length") == 2
    dialog.get_by_role("button", name="添加钉钉 / 飞书目标").click()
    robot = dialog.get_by_role("group", name="群机器人")
    robot.get_by_role("textbox", name="目标名称").fill("销售群")
    robot.get_by_role("button", name="类型", exact=True).click()
    page.get_by_role("option", name="飞书", exact=True).click()
    robot.get_by_role("textbox", name="Webhook 地址").fill("https://open.feishu.cn/open-apis/bot/v2/hook/fixture")
    robot.get_by_role("spinbutton", name="最低潜客置信度 %").fill("85")
    assert_editor_layout(page, "comment-ai-settings")
    capture(page, f"comment-ai-settings-{width}")

    page.evaluate("document.documentElement.dataset.fixtureAiFailSave = 'true'")
    dialog.get_by_role("button", name="保存配置").click()
    expect(page.locator("#ui-feedback")).to_contain_text("测试保存失败")
    expect(rule.get_by_role("textbox", name="名称", exact=True)).to_have_value("装修客户")
    page.evaluate("document.documentElement.dataset.fixtureAiFailSave = 'false'")
    dialog.get_by_role("button", name="保存配置").click()
    expect(dialog).to_be_hidden()
    posted = json.loads(page.locator('html').get_attribute('data-fixture-ai-requests'))[-1]['body']
    assert posted["prompts"][1]["watch_ids"] == [21, 22]
    assert posted["prompts"][1]["platform"] == "instagram"
    assert posted["destinations"][0]["type"] == "feishu"
    assert posted["destinations"][0]["threshold"] == .85
    assert [p["exclude_keywords"] for p in posted["prompts"]] == [["二手车"], ["跨境物流", "金融科技"]]

    page.get_by_role("button", name="AI 潜客分析配置", exact=True).click()
    expect(dialog.get_by_role("textbox", name="OpenRouter API Key")).to_have_value("********")
    expect(dialog.get_by_role("group", name="默认提示词 · v2").get_by_role(
        "textbox", name="排除的业务或行业关键词")).to_have_value("二手车")
    expect(dialog.get_by_role("group", name="自定义提示词 · v2").get_by_role(
        "textbox", name="排除的业务或行业关键词")).to_have_value("跨境物流\n金融科技")
    expect(dialog.get_by_role("group", name="自定义提示词 · v2")).to_be_visible()
    dialog.get_by_role("button", name="取消", exact=True).click()
    page.get_by_role("tab", name="评论记录", exact=True).click()
    records = page.locator("#comment-table")
    expect(page.get_by_role("button", name="分析所选", exact=True)).to_be_disabled()
    page.get_by_role("button", name="分析所有记录", exact=True).click()
    expect(page.get_by_text("已将所有平台的 1250 条未分析评论加入队列")).to_be_visible()
    all_request = json.loads(page.locator('html').get_attribute('data-fixture-ai-requests'))[-1]
    assert all_request == {"path": "/api/comment-ai/analyze-all", "search": "", "body": None}
    records.locator('input[type="checkbox"]').first.check()
    page.get_by_role("button", name="分析所选", exact=True).click()
    expect(records).to_contain_text("潜客置信度 91.0%")
    expect(records).not_to_contain_text("潜客判断：")
    assert len(json.loads(page.locator('html').get_attribute('data-fixture-ai-requests'))[-1]['body']['ids']) == 1
    page.get_by_role("button", name="按阈值转发所选", exact=True).click()
    expect(records).to_contain_text("销售群：已转发")
    page.locator('#comment-min-confidence').locator('xpath=ancestor::details').locator('summary').click()
    page.locator("#comment-min-confidence").fill("80")
    page.locator("#comment-min-confidence").press("Tab")
    page.wait_for_function("document.documentElement.dataset.fixtureAiQuery.includes('min_confidence=0.8')")
    page.evaluate("document.dispatchEvent(new Event('fixture-ai-report'))")
    report = page.locator('html').get_attribute('data-fixture-ai-report')
    assert parse_qs(report)["min_confidence"] == ["0.8"]
    assert_no_overflow(page, f"comment-ai-records-{width}")
    capture(page, f"comment-ai-records-{width}")
    page.get_by_role("tab", name="推送记录", exact=True).click()
    history = page.get_by_role("table", name="已成功推送的评论")
    expect(history).to_be_visible()
    for text in ("请问怎么预约？", "示例客户", "北京", "91.0%", "装修 / 咨询", "2026-11-20 14:00:00"):
        expect(history).to_contain_text(text)
    expect(history.locator("tbody tr")).to_have_count(1)
    expect(history.locator('td[data-label="渠道"] .meta-chip')).to_have_text(["销售群", "预约群"])
    expect(history.get_by_role("link", name="装修流程介绍")).to_have_attribute(
        "href", "https://www.douyin.com/video/1234")
    expect(history.get_by_role("link", name="示例客户", exact=True)).to_have_attribute(
        "href", "https://www.douyin.com/user/example")
    expect(history.get_by_text("用户主页", exact=True)).to_have_count(0)
    expect(history.locator('td[data-label="评论"] .watch-record-text + .content-published-time')).to_have_text(
        "评论时间 2026-11-20 14:00:00")
    expect(history.locator("td.watch-record-time")).not_to_contain_text("评论时间")
    expect(history.get_by_role("columnheader", name="平台", exact=True, include_hidden=True)).to_have_count(0)
    assert_no_overflow(page, f"comment-deliveries-{width}")
    page.locator('.pswitch [data-pf="xhs"]').click()
    expect(history).to_contain_text("暂无成功推送的评论")
    assert errors == []


def test_watch_ai_policy_editor_saves_and_restores_custom_destinations(ui):
    page, errors = ui
    page.add_script_tag(path=str(Path(__file__).parent / "fixtures/comment-ai-api.js"))
    navigate(page, "comments")
    edit = page.locator("#watch-table").get_by_role("button", name="编辑", exact=True).first
    previous_edit = edit.element_handle()
    edit.click()
    dialog = page.get_by_role("dialog", name="编辑评论监控")
    for label, option in [("抓取后自动分析", "关闭"), ("分析结果推送", "开启"),
                          ("推送渠道", "使用本目标专用渠道")]:
        dialog.get_by_role("button", name=label, exact=True).click()
        page.get_by_role("option", name=option, exact=True).click()
    dialog.get_by_role("button", name="添加钉钉 / 飞书目标").click()
    dialog.get_by_role("textbox", name="目标名称").fill("门店群")
    dialog.get_by_role("textbox", name="Webhook 地址").fill("https://oapi.dingtalk.com/robot/send?access_token=fixture")
    dialog.get_by_role("button", name="保存修改").click()
    expect(dialog).to_be_hidden()
    posted = json.loads(page.locator("html").get_attribute("data-fixture-ai-requests"))[-1]["body"]
    assert posted["ai_policy"]["auto_analyze"] == "off"
    assert posted["ai_policy"]["auto_forward"] == "on"
    assert posted["ai_policy"]["destination_mode"] == "custom"
    page.wait_for_function("button => !button.isConnected", arg=previous_edit)
    edit.click()
    expect(dialog.get_by_role("textbox", name="Webhook 地址")).to_have_value("********")
    assert errors == []


@pytest.mark.parametrize("width,height", [(1440, 1000), (375, 812)])
def test_collection_result_opens_single_work_comment_watch(ui, width, height):
    page, errors = ui
    page.set_viewport_size({"width": width, "height": height})
    page.add_script_tag(content="""{
      const originalFetch = window.fetch;
      const flags = document.documentElement.dataset;
      const job = {id: 7, platform: 'douyin', source_type: 'keyword', account_id: 1, status: 'done',
                   keywords: ['监控测试'], content_count: 1, comment_count: 0, error_count: 0};
      const title = '采集到的作品标题' + (innerWidth <= 860 ? '\\n很长的作品描述'.repeat(200) : '');
      const work = {id: 42, job_id: 7, platform: 'douyin', keyword: '监控测试',
                    desc: title, aweme_id: '7461234567890123456',
                    url: 'https://www.douyin.com/video/7461234567890123456',
                    like_count: 0, comment_count: 0, collected_comment_count: 0};
      const json = (data, status = 200) => new Response(JSON.stringify(data), {
        status, headers: {'Content-Type': 'application/json'},
      });
      window.fetch = async (input, init = {}) => {
        const path = new URL(input, location.href).pathname;
        if (path === '/api/collections') return json([job]);
        if (path === '/api/collections/7/contents') return json({items: [work], total: 1, page: 1, pages: 1});
        if (path === '/api/collections/7/contents/42/comment-watch' && init.method === 'POST') {
          flags.fixtureWatchBody = init.body;
          if (flags.fixtureWatchFail) return json({detail: '测试保存失败'}, 422);
          return json({id: 81});
        }
        return originalFetch(input, init);
      };
    }""")
    navigate(page, "collections", mobile=width <= 860)
    page.locator("#collection-job-table").get_by_role("button", name="查看结果").click()
    work = page.locator(".collection-result-item")
    expect(work.get_by_role("button", name="评论 0", exact=True)).to_be_disabled()
    work.get_by_role("button", name="评论监控", exact=True).click()
    dialog = page.get_by_role("dialog", name="作品评论监控")
    title = dialog.locator("#ui-hint")
    expect(title).to_be_visible()
    expect(title).to_contain_text("采集到的作品标题")
    assert len(title.inner_text()) <= 121
    expect(dialog.get_by_role("group", name="AI 潜客分析与推送")).to_be_visible()
    expect(page.locator("#cw-account")).to_have_value("1")
    expect(page.locator("#cw-interval")).to_have_value("600")
    for field, option in [("抓取频率", "每 5 分钟"), ("抓取后自动分析", "开启"),
                          ("分析结果推送", "开启"), ("推送渠道", "使用本目标专用渠道")]:
        dialog.get_by_role("button", name=field, exact=True).click()
        page.get_by_role("option", name=option, exact=True).click()
    dialog.get_by_role("button", name="添加钉钉 / 飞书目标").click()
    dialog.get_by_role("textbox", name="目标名称").fill("门店群")
    dialog.get_by_role("textbox", name="Webhook 地址").fill("https://oapi.dingtalk.com/robot/send?access_token=fixture")
    dialog.get_by_role("spinbutton", name="最低潜客置信度 %").fill("85")
    assert_editor_layout(page, "collection-comment-watch")
    assert_no_overflow(page, "collection-comment-watch")

    page.evaluate("document.documentElement.dataset.fixtureWatchFail = 'true'")
    dialog.get_by_role("button", name="开始监控", exact=True).click()
    expect(page.locator("#ui-feedback")).to_contain_text("测试保存失败")
    expect(dialog.get_by_role("textbox", name="目标名称")).to_have_value("门店群")
    page.evaluate("delete document.documentElement.dataset.fixtureWatchFail")
    dialog.get_by_role("button", name="开始监控", exact=True).click()
    expect(dialog).to_be_hidden()
    expect(page.get_by_text("已开始监控此作品的评论", exact=True)).to_be_visible()
    posted = json.loads(page.locator("html").get_attribute("data-fixture-watch-body"))
    assert (posted["account_id"], posted["interval_seconds"]) == (1, 300)
    policy = posted["ai_policy"]
    assert (policy["auto_analyze"], policy["auto_forward"], policy["destination_mode"]) == ("on", "on", "custom")
    assert (policy["destinations"][0]["name"], policy["destinations"][0]["threshold"]) == ("门店群", .85)
    assert errors == []
