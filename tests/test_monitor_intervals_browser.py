"""Custom interval acceptance against isolated local demo endpoints only."""
import json
import os

import pytest
from patchright.sync_api import expect

from test_web_appearance_browser import (
    ui, navigate, capture, assert_no_overflow, open_legacy_editor, assert_editor_layout,
)

pytestmark = [pytest.mark.local_cdp, pytest.mark.skipif(
    os.environ.get("CREATORHUB_RUN_LOCAL_CDP") != "1", reason="local UI acceptance is opt-in")]


def choose(page, field, label):
    page.locator(f"#{field}").locator("..").locator(".cs-trg").click()
    page.get_by_role("option", name=label, exact=True).click()


@pytest.mark.parametrize("tab,prefix,editor,path,action", [
    ("monitors", "t", "em", "/api/monitors", "开始监控"),
    ("comments", "w", "ew", "/api/comment-watches", "开始监控评论"),
    ("danmaku", "d-w", "edw", "/api/danmaku-watches", "开始监控弹幕"),
])
def test_workbench_custom_interval_create_edit_and_reload(ui, tab, prefix, editor, path, action):
    page, errors = ui
    page.set_default_timeout(8000)
    page.evaluate("document.documentElement.dataset.fixtureEditors = 'true'")
    navigate(page, tab)
    page.locator("#wb-create").click()
    page.locator(f"#{prefix}-url").fill("local-fixture-creator")
    page.locator(f"#{prefix}-acc").select_option("1", force=True)
    choose(page, prefix + "-interval", "自定义…")
    choose(page, prefix + "-interval-unit", "秒")
    page.locator(f"#{prefix}-interval-amount").fill("7")
    page.get_by_role("button", name=action, exact=True).click()
    page.locator(".wb-sheet").wait_for(state="hidden")
    assert page.locator("html").get_attribute("data-fixture-interval-path") == path
    posted = json.loads(page.locator("html").get_attribute("data-fixture-interval-body"))
    assert posted["interval_seconds"] == 7

    open_legacy_editor(page, tab)
    choose(page, editor + "-interval", "自定义…")
    choose(page, editor + "-interval-unit", "秒")
    amount = page.locator(f"#{editor}-interval-amount")
    # Native validation and conversion validation both keep the dialog/draft.
    for value in ("", "0", "1.5", "86401"):
        amount.fill(value)
        page.locator("#ui-ok").click()
        assert page.locator("#uimodal").is_visible()
        assert amount.get_attribute("aria-invalid") == "true"
        assert page.locator("html").get_attribute("data-fixture-edit-count") is None
    amount.fill("77")
    choose(page, editor + "-interval-unit", "分钟")
    assert float(amount.input_value()) == pytest.approx(77 / 60)
    page.locator("#ui-ok").click()
    page.locator("#uimodal").wait_for(state="hidden")
    posted = json.loads(page.locator("html").get_attribute("data-fixture-edit-body"))
    assert posted["interval_seconds"] == 77
    table = page.locator({"monitors": "#mon-table", "comments": "#watch-table", "danmaku": "#danmaku-watch-table"}[tab])
    table.locator("td").filter(has_text="1 分钟 17 秒").first.wait_for()

    open_legacy_editor(page, tab)
    assert page.locator(f"#{editor}-interval").input_value() == "custom"
    assert amount.input_value() == "77"
    assert page.locator(f"#{editor}-interval-unit").input_value() == "1"
    choose(page, editor + "-interval-unit", "分钟")
    amount.fill("0.01")
    page.locator("#ui-ok").click()
    assert page.locator("html").get_attribute("data-fixture-edit-count") == "1"
    assert amount.get_attribute("aria-invalid") == "true"
    amount.fill("1.5")
    page.locator("#ui-ok").click()
    page.locator("#uimodal").wait_for(state="hidden")
    posted = json.loads(page.locator("html").get_attribute("data-fixture-edit-body"))
    assert posted["interval_seconds"] == 90
    table.locator("td").filter(has_text="1 分钟 30 秒").first.wait_for()
    assert errors == []


def test_workbench_custom_interval_layout_keyboard_and_platform_drafts(ui):
    page, errors = ui
    page.set_default_timeout(8000)
    page.evaluate("document.documentElement.dataset.fixtureEditors = 'true'")
    navigate(page, "monitors")
    page.locator("#wb-create").click()
    # Keyboard-only custom selection, followed by numeric entry.
    page.locator("#t-interval").locator("..").locator(".cs-trg").focus()
    page.keyboard.press("ArrowDown")
    page.get_by_role("option", name="每 5 分钟", exact=True).wait_for()
    page.wait_for_function("document.activeElement?.getAttribute('role') === 'option'")
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.locator("#t-interval-amount").fill("1.5")
    for width, height, theme in ((1440, 960, "light"), (375, 812, "dark"), (812, 375, "light")):
        page.set_viewport_size({"width": width, "height": height})
        page.emulate_media(color_scheme=theme)
        page.locator("#t-interval-amount").scroll_into_view_if_needed()
        assert page.locator(".wb-sheet").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
        assert_no_overflow(page, f"custom-create-{width}-{theme}")
        capture(page, f"custom-interval-create-{width}-{theme}")
    page.get_by_role("button", name="返回列表", exact=True).click()
    page.locator(".wb-sheet").wait_for(state="hidden")
    page.set_viewport_size({"width": 1440, "height": 960})
    page.locator('[data-pf="xhs"]').click()
    page.locator("#wb-create").click()
    assert page.locator("#t-interval").input_value() == "300"
    assert not page.locator("#t-interval-custom").is_visible()
    choose(page, "t-interval", "自定义…")
    choose(page, "t-interval-unit", "秒")
    page.locator("#t-interval-amount").fill("23")
    page.get_by_role("button", name="返回列表", exact=True).click()
    page.locator(".wb-sheet").wait_for(state="hidden")
    page.locator('[data-pf="douyin"]').click()
    page.locator("#wb-create").click()
    assert page.locator("#t-interval-custom").is_visible()
    assert page.locator("#t-interval-unit").input_value() == "60"
    assert page.locator("#t-interval-amount").input_value() == "1.5"
    choose(page, "t-interval-unit", "秒")
    assert page.locator("#t-interval-amount").input_value() == "90"
    page.get_by_role("button", name="返回列表", exact=True).click()
    page.locator(".wb-sheet").wait_for(state="hidden")
    for tab, editor in (("monitors", "em"), ("comments", "ew"), ("danmaku", "edw")):
        open_legacy_editor(page, tab)
        choose(page, editor + "-interval", "自定义…")
        for width, height, theme in ((1440, 960, "light"), (375, 812, "dark"), (812, 375, "light")):
            page.set_viewport_size({"width": width, "height": height})
            page.emulate_media(color_scheme=theme)
            assert_editor_layout(page, tab)
            page.locator(f"#{editor}-interval-amount").scroll_into_view_if_needed()
            capture(page, f"ios-editor-custom-{tab}-{width}-{theme}")
        page.keyboard.press("Escape")
        page.locator("#uimodal").wait_for(state="hidden")
        page.set_viewport_size({"width": 1440, "height": 960})
    assert page.locator("html").get_attribute("data-fixture-edit-count") is None
    assert errors == []


def test_workbench_batch_comment_watches_keep_failed_lines_for_retry(ui):
    page, errors = ui
    page.set_default_timeout(8000)
    page.add_script_tag(content="""{
      const originalFetch = window.fetch;
      const created = [];
      const submitted = [];
      const video = "https://www.douyin.com/video/1234567890123456789";
      const profile = "https://www.douyin.com/user/test-profile";
      const json = (data, status = 200) => new Response(JSON.stringify(data), {status});
      window.fetch = (path, opts = {}) => {
        if (path === "/api/share-download/links") {
          const {share_text} = JSON.parse(opts.body);
          const url = [video, profile].find(link => share_text.includes(link));
          return Promise.resolve(json({links: url ? [{url, platform: "douyin"}] : []}));
        }
        if (path === "/api/comment-watches" && opts.method === "POST") {
          const body = JSON.parse(opts.body);
          submitted.push(body);
          document.documentElement.dataset.batchSubmitted = JSON.stringify(submitted);
          if (body.url_or_id === "invalid") return Promise.resolve(json({detail: "目标无效"}, 400));
          if (created.some(w => w.url_or_id === body.url_or_id)) {
            return Promise.resolve(json({detail: "已存在相同的评论监控"}, 409));
          }
          const watch = {...body, id: created.length + 1, title: body.url_or_id, enabled: true,
            kind: body.url_or_id === video ? "video" : "user"};
          created.push(watch);
          return Promise.resolve(json(watch));
        }
        if (path === "/api/comment-watches?platform=douyin") return Promise.resolve(json(created));
        return originalFetch(path, opts);
      };
    }""")
    navigate(page, "comments")
    page.locator("#wb-create").click()
    targets = page.locator("#w-url")
    video = "https://www.douyin.com/video/1234567890123456789"
    profile = "https://www.douyin.com/user/test-profile"
    targets.fill(f"作品分享 {video}")
    targets.press("End")
    targets.press("Enter")
    assert targets.input_value().endswith("\n")
    assert page.locator("html").get_attribute("data-batch-submitted") is None
    targets.fill(f"作品分享 {video}\n \n{profile}\ninvalid\n{video}")
    page.get_by_role("button", name="识别并清洗评论监控分享文案").click()
    feedback = page.locator("#w-url-feedback")
    expect(feedback).to_contain_text("已识别 3 条链接")
    assert targets.input_value() == f"{video}\n \n{profile}\ninvalid\n{video}"

    page.locator("#w-acc").select_option("1", force=True)
    page.locator('.wb-sheet [data-composer="comments"] .wb-optional > summary').click()
    page.locator("#w-alias").fill("同批监控")
    submit = page.get_by_role("button", name="开始监控评论", exact=True)
    submit.click()
    expect(feedback).to_have_text("原第 4 行 · 目标无效")
    assert targets.input_value() == "invalid"
    assert page.locator("#w-msg").inner_text() == "已添加 2 条，1 条已存在，1 条失败"
    assert page.locator("#w-alias").input_value() == "同批监控"

    targets.fill("@retry")
    submit.click()
    page.locator(".wb-sheet").wait_for(state="hidden")
    page.locator("#watch-table tr").nth(2).wait_for()
    posted = json.loads(page.locator("html").get_attribute("data-batch-submitted"))
    assert [body.pop("url_or_id") for body in posted] == [video, profile, "invalid", video, "@retry"]
    assert all(body == posted[0] for body in posted)
    assert posted[0]["kind"] == "auto"
    assert posted[0]["alias"] == "同批监控"
    assert posted[0]["account_id"] == 1
    assert posted[0]["interval_seconds"] == 600
    assert errors == []
