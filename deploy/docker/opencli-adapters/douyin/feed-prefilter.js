// Rules and thresholds are supplied by the backend, never duplicated here.
export function prefilterOptions(value) {
  if (value == null || value === '') return null;
  const config = JSON.parse(value);
  const categories = ['contact', 'commercial_intent', 'redirect', 'account_reference', 'comment_demand'];
  if (!config || typeof config.rules_version !== 'string' || !config.rules_version
    || categories.some(key => !Array.isArray(config.tokens?.[key])
      || config.tokens[key].some(token => typeof token !== 'string' || !token
        || !['strong', 'weak'].includes(config.token_strengths?.[token.toLowerCase()])))
    || !Number.isInteger(config.comment_min_count) || config.comment_min_count < 0 || config.comment_min_count > 1000000
    || !Number.isInteger(config.demand_min_comments) || config.demand_min_comments < 1 || config.demand_min_comments > 20) {
    throw new Error('Invalid monetization prefilter rules or thresholds');
  }
  return config;
}

// Self-contained: executed inside the recommendation page. Only compact evidence
// leaves the page; comment text, identities and reply payloads stay in memory.
export async function screenFeedItem(row, config, request) {
  const result = { status: row.is_ad || row.is_live ? 'skipped' : 'unknown',
    rules_version: config.rules_version, hits: [], mentions: [], signals: { ...row.prefilter_signals },
    profile_ok: false, comments_checked: false, comments_ok: null, demand_comment_count: 0 };
  let signature = '';
  const output = () => ({ prefilter: result, author_signature: signature });
  if (result.status === 'skipped') return output();
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const fold = value => value.replace(/[A-Z]/g, char => char.toLowerCase());
  // Case-folded duplicates such as v/V count once per field.
  const hits = (text, tokens, field) => [...new Set(tokens.map(fold))]
    .filter(token => fold(text).includes(token)).map(token => ({ field, token, strength: config.token_strengths[token] }));
  const strong = hits => hits.some(hit => hit.strength === 'strong');
  // Deadline covers both fetch and JSON decoding. Abort cancels the request;
  // the race also bounds clients whose mocked/failed transport ignores abort.
  const get = request || (async (path, params) => {
    const controller = new AbortController();
    let timer;
    try {
      return await Promise.race([
        (async () => {
          const url = new URL(path, location.origin);
          url.searchParams.set('aid', '6383');
          for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
          const response = await fetch(url.toString(), { method: 'GET', credentials: 'include', signal: controller.signal });
          if (response.status !== 200) throw new Error('http_error');
          const body = await response.json();
          if (!object(body) || body.status_code !== 0) throw new Error('invalid_response');
          return body;
        })(),
        new Promise((_, reject) => { timer = setTimeout(() => {
          controller.abort(); reject(new Error('timeout'));
        }, 5000); }),
      ]);
    } finally { clearTimeout(timer); }
  });
  try {
    const secUid = new URL(row.author_url).pathname.split('/user/')[1];
    if (!secUid) throw new Error('missing_author');
    const profile = await get('/aweme/v1/web/user/profile/other/', { sec_user_id: secUid });
    const user = profile?.user;
    if (profile?.status_code !== 0 || !object(user) || typeof user.signature !== 'string'
      || (user.sec_uid && user.sec_uid !== secUid)) throw new Error('invalid_profile');
    signature = user.signature;
    const enterprise = typeof user.enterprise_user_info === 'string'
      ? JSON.parse(user.enterprise_user_info || '{}') : user.enterprise_user_info;
    if (enterprise != null && !object(enterprise)) throw new Error('invalid_enterprise');
    if (user.r_fans_group_info != null && !object(user.r_fans_group_info)) throw new Error('invalid_group');
    result.profile_ok = true;
    result.signals = { ...result.signals,
      with_fusion_shop_entry: user.with_fusion_shop_entry ?? null,
      with_commerce_entry: user.with_commerce_entry ?? null,
      commerce_user_info: user.commerce_user_info ?? null,
      enterprise_permissions: enterprise?.permissions ?? null,
      can_show_group_card: user.can_show_group_card ?? null,
      custom_verify: user.custom_verify ?? null,
      enterprise_verify_reason: user.enterprise_verify_reason ?? null,
      has_fans_group: object(user.r_fans_group_info) && Object.keys(user.r_fans_group_info).length > 0,
    };
    const { comment_demand: _demand, ...profileTokens } = config.tokens;
    result.hits = hits(signature, Object.values(profileTokens).flat(), 'signature');
    if (result.signals.has_fans_group) result.hits.push({ field: 'r_fans_group_info', token: 'non_empty', strength: 'strong' });
    const targets = [...new Set((Array.isArray(user.signature_extra) ? user.signature_extra : [])
      .filter(entry => entry?.type === 0 && typeof entry.sec_uid === 'string'
        && entry.sec_uid.trim() && entry.sec_uid !== secUid)
      .map(entry => entry.sec_uid))].slice(0, 2);
    for (const target of targets) {
      const mention = { sec_uid: target, profile_ok: false, signature: '', hits: [], has_fans_group: false,
        pinned_checked: false, pinned_ok: null, pinned: [] };
      result.mentions.push(mention);
      // Sequential requests: wait at least one second after the previous request completed,
      // including failures. Always inspect both targets, even when already matched.
      await new Promise(resolve => setTimeout(resolve, 1000));
      try {
        const data = await get('/aweme/v1/web/user/profile/other/', { sec_user_id: target });
        const mentioned = data?.user;
        if (data?.status_code !== 0 || !object(mentioned) || mentioned.sec_uid !== target
          || typeof mentioned.signature !== 'string') throw new Error('invalid_mention_profile');
        mention.profile_ok = true;
        mention.signature = mentioned.signature;
        mention.hits = hits(mention.signature, Object.values(profileTokens).flat(), 'mention_signature');
        mention.has_fans_group = object(mentioned.r_fans_group_info) && Object.keys(mentioned.r_fans_group_info).length > 0;
        if (mention.has_fans_group) mention.hits.push({ field: 'mention_fans_group', token: 'non_empty', strength: 'strong' });
        await new Promise(resolve => setTimeout(resolve, 1000));
        mention.pinned_checked = true;
        mention.pinned_ok = false;
        try {
          const posts = await get('/aweme/v1/web/aweme/post/', { sec_user_id: target, max_cursor: 0, count: 20 });
          if (posts?.status_code !== 0 || !Array.isArray(posts.aweme_list)) throw new Error('invalid_posts');
          const pinned = posts.aweme_list.filter(video => video?.is_top === 1 || video?.is_top === true).slice(0, 3);
          if (pinned.some(video => typeof video.desc !== 'string')) throw new Error('invalid_pinned_desc');
          mention.pinned = pinned.map(video => {
            const desc = Array.from(video.desc).slice(0, 300).join('');
            return { desc, hits: hits(desc, Object.values(profileTokens).flat(), 'mention_pinned') };
          });
          mention.pinned_ok = true;
          for (const hit of mention.pinned.flatMap(video => video.hits)) {
            if (!mention.hits.some(existing => existing.field === hit.field && existing.token === hit.token)) mention.hits.push(hit);
          }
        } catch { /* Optional pinned evidence cannot invalidate profile hits or the existing verdict. */ }
        for (const hit of mention.hits) {
          if (!result.hits.some(existing => existing.field === hit.field && existing.token === hit.token)) result.hits.push(hit);
        }
      } catch { /* Optional evidence failure must not change the original author's verdict. */ }
    }
    if (strong(result.hits)) {
      result.status = 'matched';
      return output();
    }
    if (!Number.isFinite(row.comment_count) || row.comment_count < 0) throw new Error('invalid_comment_count');
    if (row.comment_count >= config.comment_min_count) {
      result.comments_checked = true;
      result.comments_ok = false;
      const data = await get('/aweme/v1/web/comment/list/', { aweme_id: row.aweme_id, count: 20, cursor: 0 });
      if (data?.status_code !== 0 || !Array.isArray(data.comments)
        || data.comments.some(comment => !object(comment) || typeof comment.text !== 'string')) {
        throw new Error('invalid_comments');
      }
      const tokens = new Set();
      for (const comment of data.comments.slice(0, 20)) {
        const found = hits(comment.text, config.tokens.comment_demand, 'comments.text');
        if (strong(found)) result.demand_comment_count += 1;
        found.forEach(hit => tokens.add(hit.token));
      }
      result.comments_ok = true;
      result.hits.push(...[...tokens].map(token => ({
        field: 'comments.text', token, strength: config.token_strengths[token],
      })));
    }
    result.status = result.demand_comment_count >= config.demand_min_comments ? 'matched' : 'missed';
  } catch { /* Fail open; normal feed collection and dwell continue. */ }
  return output();
}

export async function prefilterRow(page, row, config, evaluate) {
  let evidence;
  try {
    evidence = await evaluate(page, `(${screenFeedItem.toString()})(${JSON.stringify(row)}, ${JSON.stringify(config)})`);
    if (!evidence?.prefilter) throw new Error('missing_prefilter_result');
  } catch {
    // A failed page evaluation is evidence unavailable, never a negative result.
    evidence = { author_signature: '', prefilter: {
      status: row.is_ad || row.is_live ? 'skipped' : 'unknown', rules_version: config.rules_version,
      hits: [], mentions: [], signals: { ...row.prefilter_signals }, profile_ok: false,
      comments_checked: false, comments_ok: null, demand_comment_count: 0,
    } };
  }
  const { prefilter_signals, ...clean } = row;
  return { ...clean, ...evidence };
}
