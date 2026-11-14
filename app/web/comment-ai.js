/* Comment lead pipeline UI. Credentials use the server's keep/replace/clear mask. */
const COMMENT_AI_LABELS = { unanalyzed: '未分析', queued: '等待分析', processing: '分析中', done: '已分析', failed: '分析失败', pending: '等待转发', sending: '转发中', sent: '已转发', skipped: '未转发' };
let COMMENT_AI_WATCHES = [];
let COMMENT_AI_POLL = null;
let COMMENT_AI_FIELD = 0;

function commentAIMarkup(row) {
  const ai = row.ai || {status: 'unanalyzed'};
  const score = ai.confidence == null ? '' : `<strong>潜客置信度 ${(ai.confidence * 100).toFixed(1)}%</strong>`;
  const exclusion = ai.excluded_by_model ? '<span>AI 判定命中排除规则</span>'
    : ai.excluded_keyword ? `<span>命中排除关键词：${esc(ai.excluded_keyword)}</span>` : '';
  const deliveries = (ai.deliveries || []).map(d => `<span title="${esc(d.error || '')}">${esc(d.name || d.destination_id)}：${d.status === 'failed' ? '转发失败' : esc(COMMENT_AI_LABELS[d.status] || d.status)}${d.status === 'failed' ? ` <button type="button" class="ghost sm wb-secondary-action" onclick="commentAIAction('forward',true,${row.id})">${ic("i-refresh")}重试转发</button>` : ''}</span>`).join(' · ');
  const retry = ai.status === 'failed' ? `<button type="button" class="ghost sm wb-secondary-action" onclick="commentAIAction('analyze',true,${row.id})">${ic("i-refresh")}重试分析</button>` : '';
  const refreshTitle = ai.can_refresh_title ? `<button type="button" class="ghost sm wb-secondary-action" onclick="commentAIAction('analyze',false,${row.id},true)">${ic("i-refresh")}补标题并重新分析</button>` : '';
  return `<div class="comment-record-ai" title="${esc(ai.error || '')}"><div class="comment-record-ai-summary">
    <span class="comment-record-ai-status" data-status="${esc(ai.status)}">${esc(COMMENT_AI_LABELS[ai.status] || ai.status)}</span>${score}${exclusion}
    ${ai.prompt_id ? `<span>${esc(ai.prompt_name || ai.prompt_id)} v${ai.prompt_version}</span>` : ''}${retry}${refreshTitle}</div>
    ${ai.error ? `<div>${esc(ai.error)}</div>` : ''}${deliveries ? `<div>${deliveries}</div>` : ''}</div>`;
}

async function commentAIAction(action, retry = false, id = null, refreshTitle = false) {
  const ids = id == null ? [...selComment] : [id];
  if (!ids.length) return toast('请先选择评论', 'err');
  if (retry && !await uiConfirm({title: action === 'analyze' ? '重试失败分析' : '重试失败转发',
    message: action === 'analyze' ? '若上次请求超时，远端可能已计费。仅重试失败项，成功结果会保留。' : '若上次请求超时，群里可能已收到消息，请核对后重试。', okText: '重试'})) return;
  try {
    const body = {ids, retry_failed: retry};
    if (refreshTitle) body.refresh_missing_title = true;
    const result = await api(`/api/comment-ai/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const message = refreshTitle ? `已入队 ${result.refreshed || 0} 条待补标题的分析`
      : action === 'analyze' ? `已入队 ${result.queued + (result.retried || 0)} 条；已有结果或已在队列的评论会跳过`
      : `已入队 ${result.queued} 条转发；仅处理达到各目标阈值且未成功转发的评论`;
    toast(message, 'ok');
    await refreshComments();
    commentAIPoll();
  } catch (error) { toast(error.message, 'err'); }
}

function commentAIPoll() {
  clearTimeout(COMMENT_AI_POLL);
  let remaining = 60;
  const tick = async () => {
    const table = $('comment-table');
    if (!table?.isConnected || !table.getClientRects().length || document.hidden) return;
    await refreshComments();
    if (--remaining > 0) COMMENT_AI_POLL = setTimeout(tick, 3000);
  };
  COMMENT_AI_POLL = setTimeout(tick, 3000);
}

function commentAIField(label, html) {
  const existing = html.match(/\bid="([^"]+)"/);
  const id = existing ? existing[1] : `cai-field-${++COMMENT_AI_FIELD}`;
  if (!existing) html = html.replace(/^<(input|select|textarea)/, `<$1 id="${id}"`);
  return `<div class="form-field" style="margin:10px 0"><label for="${id}">${label}</label>${html}</div>`;
}
function commentAISplit(value) { return [...new Set(value.split(/[,，\n]/).map(x => x.trim()).filter(Boolean))]; }
function commentAIId(prefix) { return prefix + '_' + crypto.randomUUID().replaceAll('-', '').slice(0, 12); }
function commentAIRemove(button) { button.closest('fieldset').remove(); }

function commentAIAddPrompt(prompt = null) {
  const p = prompt || {id: commentAIId('prompt'), name: '', text: '', version: 1, enabled: true, platform: '', watch_ids: [], work_ids: [], groups: []};
  const isDefault = p.id === 'default';
  const platformOptions = [['', '全部平台'], ['douyin', '抖音'], ['xhs', '小红书'], ['kuaishou', '快手'], ['shipinhao', '视频号'], ['tiktok', 'TikTok']];
  const watchOptions = COMMENT_AI_WATCHES.map(w => `<option value="${w.id}" ${p.watch_ids.includes(w.id) ? 'selected' : ''}>#${w.id} ${esc(w.alias || w.title || w.aweme_id || w.sec_uid)} (${esc(w.platform)} · ${w.kind === 'video' ? '作品' : '账号'})</option>`).join('');
  const missing = p.watch_ids.filter(id => !COMMENT_AI_WATCHES.some(w => w.id === id)).map(id => `<option value="${id}" selected>#${id} 已删除的监控任务</option>`).join('');
  $('cai-prompts').insertAdjacentHTML('beforeend', `<fieldset data-prompt="${esc(p.id)}" style="padding:14px;margin:14px 0;border:1px solid var(--border);border-radius:10px;min-width:0">
    <legend>${isDefault ? '默认提示词' : '自定义提示词'} · v${Number(p.version) || 1}</legend>
    ${commentAIField('名称', `<input data-field="name" value="${esc(p.name)}" maxlength="80">`)}
    ${commentAIField('判断标准', `<textarea data-field="text" rows="6" maxlength="12000">${esc(p.text)}</textarea>`)}
    ${commentAIField('排除的业务或行业关键词', `<textarea data-field="exclude_keywords" rows="3" aria-describedby="cai-exclude-help-${esc(p.id)}">${esc((p.exclude_keywords || []).join('\n'))}</textarea>`)}
    <p class="field-help" id="cai-exclude-help-${esc(p.id)}">逗号或换行分隔。AI 结合评论和作品标题判断语义，命中排除范围时潜客置信度记为 0%，并跳过转发。</p>
    ${!isDefault ? `${commentAIField('适用平台', `<select data-field="platform">${platformOptions.map(([v, label]) => `<option value="${v}" ${v === p.platform ? 'selected' : ''}>${label}</option>`).join('')}</select>`)}
      ${commentAIField('指定账号或作品监控（可多选；Ctrl / Cmd 加选）', `<select data-field="watch_ids" multiple size="5">${watchOptions}${missing}</select>`)}
      ${commentAIField('作品 ID（多个用逗号分隔，需指定平台）', `<input data-field="work_ids" value="${esc(p.work_ids.join(', '))}">`)}
      ${commentAIField('监控分组（多个用逗号分隔）', `<input data-field="groups" value="${esc(p.groups.join(', '))}">`)}
      <label><input data-field="enabled" type="checkbox" ${p.enabled ? 'checked' : ''}>启用此规则</label>
      <button type="button" class="sm danger" onclick="commentAIRemove(this)">${ic("i-trash")}删除规则</button>` : ''}
  </fieldset>`);
}

function commentAIAddDestination(destination = null) {
  const d = destination || {id: commentAIId('robot'), name: '', type: 'dingtalk', webhook: '', secret: '', keyword: '', threshold: 0.8, enabled: true};
  $('cai-destinations').insertAdjacentHTML('beforeend', `<fieldset data-destination="${esc(d.id)}" style="padding:14px;margin:14px 0;border:1px solid var(--border);border-radius:10px;min-width:0">
    <legend>群机器人</legend>
    ${commentAIField('目标名称', `<input data-field="name" value="${esc(d.name)}" maxlength="80">`)}
    ${commentAIField('类型', `<select data-field="type"><option value="dingtalk" ${d.type === 'dingtalk' ? 'selected' : ''}>钉钉</option><option value="feishu" ${d.type === 'feishu' ? 'selected' : ''}>飞书</option></select>`)}
    ${commentAIField('Webhook 地址', `<input data-field="webhook" type="password" autocomplete="new-password" value="${esc(d.webhook)}">`)}
    ${commentAIField('加签密钥（可选）', `<input data-field="secret" type="password" autocomplete="new-password" value="${esc(d.secret)}">`)}
    ${commentAIField('机器人要求的关键词（可选）', `<input data-field="keyword" value="${esc(d.keyword)}" maxlength="100">`)}
    ${commentAIField('最低潜客置信度 %', `<input data-field="threshold" type="number" min="0" max="100" step="0.1" value="${d.threshold * 100}">`)}
    <label><input data-field="enabled" type="checkbox" ${d.enabled ? 'checked' : ''}>启用此目标</label>
    <button type="button" class="sm danger" onclick="commentAIRemove(this)">${ic("i-trash")}删除目标</button>
  </fieldset>`);
}

function watchAIFields(policy = {}) {
  const options = [['inherit', '跟随全局'], ['on', '开启'], ['off', '关闭']];
  const select = (id, value, choices) => `<select id="${id}">${choices.map(([key, label]) =>
    `<option value="${key}"${key === value ? ' selected' : ''}>${label}</option>`).join('')}</select>`;
  const mode = policy.destination_mode || 'global';
  return `<fieldset class="monitor-config-group">
    <legend>AI 潜客分析与推送</legend>
    <div class="row">
      ${commentAIField('抓取后自动分析', select('cai-watch-analyze', policy.auto_analyze || 'inherit', options))}
      ${commentAIField('分析结果推送', select('cai-watch-forward', policy.auto_forward || 'inherit', options))}
    </div>
    ${commentAIField('推送渠道', select('cai-watch-mode', mode, [['global', '使用全局渠道'], ['custom', '使用本目标专用渠道']]))}
    <fieldset id="cai-watch-custom" style="padding:0;margin:0;border:0" ${mode === 'custom' ? '' : 'hidden disabled'}>
      <p class="field-help">专用渠道的阈值独立设置。已有密钥以 ******** 显示。</p>
      <div id="cai-destinations"></div>
      <button class="ghost sm wb-secondary-action" type="button" onclick="commentAIAddDestination()">${ic('i-plus')}添加钉钉 / 飞书目标</button>
    </fieldset>
    <p class="field-help">自动分析使用全局 OpenRouter Key。关闭自动分析后仍可手动分析。关闭推送会停用该目标的自动和手动转发。</p>
  </fieldset>`;
}

function setupWatchAIFields(policy) {
  (policy?.destinations || []).forEach(commentAIAddDestination);
  $('cai-watch-mode').addEventListener('change', event => {
    const custom = $('cai-watch-custom');
    custom.hidden = custom.disabled = event.target.value !== 'custom';
  });
}

function readWatchAIFields(policy) {
  const destinationMode = $('cai-watch-mode').value;
  return {
    auto_analyze: $('cai-watch-analyze').value,
    auto_forward: $('cai-watch-forward').value,
    destination_mode: destinationMode,
    destinations: destinationMode === 'custom' ? commentAIReadDestinations() : (policy?.destinations || []),
  };
}

function commentAIReadSettings() {
  const prompts = [...$('cai-prompts').querySelectorAll('[data-prompt]')].map(node => {
    const field = name => node.querySelector(`[data-field="${name}"]`);
    return {id: node.dataset.prompt, name: field('name').value.trim(), text: field('text').value.trim(),
      exclude_keywords: commentAISplit(field('exclude_keywords').value),
      platform: field('platform')?.value || '', enabled: field('enabled')?.checked ?? true,
      watch_ids: [...(field('watch_ids')?.selectedOptions || [])].map(o => +o.value),
      work_ids: commentAISplit(field('work_ids')?.value || ''), groups: commentAISplit(field('groups')?.value || '')};
  });
  return {api_key: $('cai-key').value.trim(), auto_analyze: $('cai-auto').checked,
    auto_forward: $('cai-forward').checked, batch_size: Number($('cai-batch').value), prompts,
    destinations: commentAIReadDestinations()};
}

function commentAIReadDestinations() {
  return [...$('cai-destinations').querySelectorAll('[data-destination]')].map(node => {
    const field = name => node.querySelector(`[data-field="${name}"]`);
    const threshold = field('threshold').value;
    if (threshold === '' || !Number.isFinite(+threshold) || +threshold < 0 || +threshold > 100) uiEditorError('转发置信度须为 0–100');
    return {id: node.dataset.destination, name: field('name').value.trim(), type: field('type').value,
      webhook: field('webhook').value.trim(), secret: field('secret').value.trim(), keyword: field('keyword').value.trim(),
      threshold: Number(threshold) / 100, enabled: field('enabled').checked};
  });
}

async function openCommentAISettings() {
  let config;
  try {
    [config, COMMENT_AI_WATCHES] = await Promise.all([api('/api/comment-ai/settings'), api('/api/comment-watches')]);
  } catch (error) { toast('读取 AI 配置失败：' + error.message, 'err'); return; }
  const saved = await new Promise(resolve => {
    _uiResolve = resolve; _uiCancelVal = null; _uiGetVal = commentAIReadSettings;
    $('ui-body').innerHTML = `
      <p class="mut">使用 google/gemini-decision-1 判断评论的潜客可能性。分析前会补查缺少标题的抖音作品，标题会随请求发送。</p>
      ${commentAIField('OpenRouter API Key', `<input id="cai-key" type="password" autocomplete="new-password" value="${esc(config.api_key)}">`)}
      <label style="display:block;margin:10px 0"><input id="cai-auto" type="checkbox" ${config.auto_analyze ? 'checked' : ''}>抓取新评论后自动分析</label>
      <label style="display:block;margin:10px 0"><input id="cai-forward" type="checkbox" ${config.auto_forward ? 'checked' : ''}>分析完成后按各目标阈值自动转发</label>
      ${commentAIField('同作品每批最多评论数（1–500）', `<input id="cai-batch" type="number" min="1" max="500" value="${config.batch_size}">`)}
      <p class="mut">提示词匹配顺序：作品 ID → 指定监控任务 → 分组 → 默认。同级规则优先使用列表中靠前的规则。修改会创建新版本，仅影响之后入队的评论。</p>
      <div id="cai-prompts"></div><button class="ghost sm wb-secondary-action" type="button" onclick="commentAIAddPrompt()">${ic("i-plus")}添加领域提示词</button>
      <p class="mut">每个群机器人可单独设置阈值。已成功转发的评论不会重复发送到同一目标。******** 表示保留已配置的密钥，清空字段会删除该值。</p>
      <div id="cai-destinations"></div><button class="ghost sm wb-secondary-action" type="button" onclick="commentAIAddDestination()">${ic("i-plus")}添加钉钉 / 飞书目标</button>`;
    config.prompts.forEach(commentAIAddPrompt);
    config.destinations.forEach(commentAIAddDestination);
    _uiOpen('AI 潜客分析与转发', '自动抓取 → 批量分析 → 阈值筛选 → 群聊通知', {okText: '保存配置', wide: true, icon: 'i-bolt',
      submit: value => api('/api/comment-ai/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)})});
  });
  if (saved) toast('AI 分析配置已保存', 'ok');
}
