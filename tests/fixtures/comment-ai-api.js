// Isolated UI contract fixture. No requests reach a provider or a real webhook.
(() => {
  const previous = window.fetch.bind(window);
  let config;
  const watchEdits = new Map();
  const analyzed = new Set(), forwarded = new Set();
  window.commentAIRequests = [];
  const flags = document.documentElement.dataset;
  const record = value => {
    window.commentAIRequests.push(value);
    flags.fixtureAiRequests = JSON.stringify(window.commentAIRequests);
  };
  document.addEventListener('fixture-ai-report', () => { flags.fixtureAiReport = _moduleReportParams('comments', false).toString(); });
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: {'Content-Type': 'application/json'},
  });
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = init.method || 'GET';
    const watchEdit = url.pathname.match(/^\/api\/comment-watches\/(\d+)$/);
    if (watchEdit && method === 'PUT') {
      const body = JSON.parse(init.body);
      record({path: url.pathname, body});
      const edits = structuredClone(body);
      for (const dest of edits.ai_policy.destinations) {
        dest.webhook = '********';
        if (dest.secret) dest.secret = '********';
      }
      watchEdits.set(Number(watchEdit[1]), edits);
      return json(edits);
    }
    if (url.pathname === '/api/comment-ai/settings') {
      if (!config) config = await (await previous(input)).json();
      if (method === 'PUT') {
        const body = JSON.parse(init.body);
        record({path: url.pathname, body});
        if (flags.fixtureAiFailSave === 'true') return json({detail: '测试保存失败，请重试'}, 422);
        config = body;
        config.prompts.forEach(p => p.version = 2);
        config.api_key = '********';
        config.destinations.forEach(d => { d.webhook = '********'; d.secret = '********'; });
      }
      return json(config);
    }
    if (/^\/api\/comment-ai\/(analyze|forward)$/.test(url.pathname)) {
      const body = JSON.parse(init.body);
      record({path: url.pathname, body});
      body.ids.forEach(id => (url.pathname.endsWith('analyze') ? analyzed : forwarded).add(id));
      return json({queued: body.ids.length, skipped: 0}, 202);
    }
    if (url.pathname === '/api/comment-ai/analyze-all' && method === 'POST') {
      record({path: url.pathname, search: url.search, body: init.body || null});
      return json({queued: 1250, skipped: 0}, 202);
    }
    if (url.pathname === '/api/reports/comment-deliveries.xlsx' && method === 'GET') {
      flags.fixtureAiDeliveryReport = url.search;
      return new Response('fixture workbook', {headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': 'attachment; filename="fixture-deliveries.xlsx"',
      }});
    }
    if (url.pathname === '/api/comment-ai/deliveries' && method === 'GET') {
      const params = url.searchParams;
      flags.fixtureAiDeliveryQuery = url.search;
      const rows = params.get('platform') === 'douyin' ? [...forwarded].map(() => ({
        confidence: '91.0%', group_name: '装修', tags: ['咨询'], group_tags: '装修 / 咨询',
        user_nickname: '示例客户', ip_label: '北京',
        text: '请问怎么预约？', comment_time: '2026-11-20 14:00:00', work_title: '装修流程介绍',
        work_url: 'https://www.douyin.com/video/1234', user_profile_url: 'https://www.douyin.com/user/example',
        destination_names: ['销售群', '预约群'], sent_at: '2026-11-20T06:30:00+00:00',
      })) : [];
      if (rows.length) rows.push({...rows[0], text: '第二页推送', ip_label: '上海', confidence: '80.0%'});
      const matches = rows.filter(row =>
        (!params.has('group_name') || row.group_name === params.get('group_name')) &&
        (!params.has('tag') || row.tags.includes(params.get('tag'))) &&
        row.ip_label.includes(params.get('ip_label') || '') &&
        parseFloat(row.confidence) / 100 >= Number(params.get('min_confidence')));
      const pages = Math.max(1, matches.length);
      const page = Math.min(pages, Number(params.get('page')));
      return json({items: matches.slice(page - 1, page), total: matches.length, page, pages});
    }
    const response = await previous(input, init);
    if (url.pathname === '/api/comment-watches' && method === 'GET') {
      const rows = await response.json();
      return json([...rows, {...rows[0], id: 22, title: '另一作品监控', alias: '另一作品', kind: 'video'}]
        .map(row => ({...row, ...watchEdits.get(row.id)})));
    }
    if (url.pathname === '/api/comments' && method === 'GET') {
      flags.fixtureAiQuery = url.search;
      const value = await response.json();
      const rows = Array.isArray(value) ? value : value.items;
      rows.forEach(row => row.ai = analyzed.has(row.id) ? {
        status: 'done', confidence: .91, prompt_id: 'default', prompt_name: '默认潜客判断', prompt_version: 2,
        deliveries: forwarded.has(row.id) ? [{destination_id: 'demo', name: '销售群', status: 'sent'}] : [],
      } : {status: 'unanalyzed', confidence: null, deliveries: []});
      return json(value);
    }
    return response;
  };
})();
