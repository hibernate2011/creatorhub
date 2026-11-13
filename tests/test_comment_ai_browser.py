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
    dialog.get_by_role("button", name="添加领域提示词").click()
    rule = dialog.get_by_role("group", name="自定义提示词 · v1")
    rule.get_by_role("textbox", name="名称", exact=True).fill("装修客户")
    rule.get_by_role("textbox", name="判断标准").fill("询价或预约上门视为潜在客户")
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
    assert posted["destinations"][0]["type"] == "feishu"
    assert posted["destinations"][0]["threshold"] == .85

    page.get_by_role("button", name="AI 潜客分析配置", exact=True).click()
    expect(dialog.get_by_role("textbox", name="OpenRouter API Key")).to_have_value("********")
    expect(dialog.get_by_role("group", name="自定义提示词 · v2")).to_be_visible()
    dialog.get_by_role("button", name="取消", exact=True).click()
    page.get_by_role("tab", name="评论记录", exact=True).click()
    records = page.locator("#comment-table")
    records.locator('input[type="checkbox"]').first.check()
    page.get_by_role("button", name="分析所选", exact=True).click()
    expect(records).to_contain_text("潜客置信度 91.0%")
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
    assert errors == []


def test_watch_ai_policy_editor_saves_and_restores_custom_destinations(ui):
    page, errors = ui
    page.add_script_tag(path=str(Path(__file__).parent / "fixtures/comment-ai-api.js"))
    navigate(page, "comments")
    edit = page.locator("#watch-table").get_by_role("button", name="编辑", exact=True).first
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
    page.wait_for_function("document.querySelector('#watch-table button[onclick^=editWatchMeta]')")
    edit.click()
    expect(dialog.get_by_role("textbox", name="Webhook 地址")).to_have_value("********")
    assert errors == []
