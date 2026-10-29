// Self-contained page readers: no requests, clicks, scrolling or state mutation.
export function pageRisk() {
  const body = document.body?.innerText || '';
  const visible = e => e.getClientRects().length > 0;
  return /登录后查看|请登录|操作频繁|访问频繁|安全验证|请完成验证|拖动滑块|安全限制|访问链接异常/.test(body)
    || /\/login|website-login\/error|error_code=300017|error_code=300031/.test(location.href)
    || [...document.querySelectorAll('[class*="captcha"],[id*="captcha"]')].some(visible);
}

export function noteSnapshot(noteId) {
  const unwrap = value => value?._value ?? value;
  const store = unwrap(window.__INITIAL_STATE__?.note?.noteDetailMap);
  const entry = store?.[noteId];
  const note = entry?.note;
  if (note?.noteId !== noteId || typeof note.desc !== 'string' || typeof note.title !== 'string') return null;
  const comments = entry.comments;
  const authorId = note.user?.userId;
  const rows = Array.isArray(comments?.list) ? comments.list : [];
  const seen = new Set();
  const firstScreen = rows.filter(c => typeof c?.id === 'string' && !seen.has(c.id) && seen.add(c.id));
  const commentsOk = comments?.firstRequestFinish === true && Array.isArray(comments.list)
    && typeof authorId === 'string' && authorId.length > 0
    && rows.every(c => typeof c?.id === 'string' && c.id.length > 0
      && typeof c.content === 'string' && typeof c.userInfo?.userId === 'string' && c.userInfo.userId.length > 0);
  const metric = selector => {
    const text = document.querySelector?.(selector)?.textContent?.trim() || '';
    return /^\d+/.test(text) ? text : '0';
  };
  return {
    title: note.title, content: note.desc,
    likes: metric('.interact-container .like-wrapper .count'),
    collects: metric('.interact-container .collect-wrapper .count'),
    comments: metric('.interact-container .chat-wrapper .count'),
    author: typeof note.user?.nickname === 'string' ? note.user.nickname : '',
    author_user_id: typeof authorId === 'string' ? authorId : '',
    tags: (Array.isArray(note.tagList) ? note.tagList : []).map(t => t.name || '').join(', '),
    comments_ok: commentsOk,
    // Count root comments only; never expand replies. Author comments are explicit.
    first_screen_comments: commentsOk ? firstScreen.map(c => ({
      text: c.content, is_author: c.userInfo.userId === authorId || c.showTags?.includes('is_author') === true,
      pinned: c.showTags?.includes('user_top') === true,
    })) : [],
  };
}

export function profileSnapshot() {
  const unwrap = value => value?._value ?? value;
  const data = unwrap(window.__INITIAL_STATE__?.user?.userPageData);
  const basic = data?.basicInfo;
  const visible = e => e.getClientRects().length > 0;
  const cards = [...document.querySelectorAll('section.note-item')].filter(visible);
  const pinned = cards.filter(e => [...e.querySelectorAll('.top-tag-area, .top-wrapper')]
    .some(b => visible(b) && b.textContent?.trim() === '置顶'));
  const pinnedTitles = pinned.map(e => e.querySelector('.title')?.textContent?.trim());
  // An empty list during loading is unknown; it is not evidence of no pinned notes.
  const pinnedOk = (cards.length > 0 || data?.posted === 0)
    && pinnedTitles.every(t => typeof t === 'string' && t.length > 0);
  const controls = [...document.querySelectorAll('button, [role="button"], a')].filter(visible);
  const groupLabels = new Set(['加入群聊', '加入该群', '申请加入', '申请入群', '进入群聊']);
  const shopLabels = new Set(['店铺', '商品', '进入店铺', '我的店铺']);
  return {
    profile_ok: typeof basic?.desc === 'string' && typeof basic?.nickname === 'string',
    description: typeof basic?.desc === 'string' ? basic.desc : '',
    pinned_ok: pinnedOk, pinned_titles: pinnedOk ? pinnedTitles : [],
    group_entry: controls.some(e => groupLabels.has(e.textContent?.trim())),
    shop_entry: controls.some(e => shopLabels.has(e.textContent?.trim())),
  };
}
