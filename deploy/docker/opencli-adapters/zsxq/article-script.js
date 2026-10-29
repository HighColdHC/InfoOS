// Kept self-contained so OpenCLI can serialize this function into the page.
export async function collectArticle({ budgetMs = 15000, maxBodyChars = 120000 } = {}) {
  const clean = (value) => String(value || '').replace(/[\u200b-\u200d\u2060\ufeff]/g, '').trim();
  const deadline = Date.now() + budgetMs;
  let editor;
  do {
    editor = document.querySelector('.ql-editor');
    if (editor && clean(editor.innerText)) break;
    if (document.querySelector('input[type="password"], [class*="login-qr"], [class*="login-page"]')) break;
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  } while (true);
  const body = clean(editor?.innerText);
  const login = !body && (Boolean(document.querySelector(
    'input[type="password"], [class*="login-qr"], [class*="login-page"]',
  )) || /登录|扫码/.test(clean(document.body?.innerText)));
  const urls = (nodes, attribute) => [...new Set([...nodes].flatMap((node) => {
    try {
      const url = new URL(node[attribute] || node.getAttribute(attribute), location.href);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? [url.href] : [];
    } catch { return []; } // The DOM may contain non-URL links.
  }))].slice(0, 200);
  return {
    status: login ? 'login_required' : body ? 'fetched' : 'empty',
    url: location.href,
    title: clean(document.querySelector('h1, .article-title, .title')?.innerText || document.title),
    body: body.slice(0, maxBodyChars),
    complete: Boolean(body) && body.length < maxBodyChars,
    images: editor ? urls(editor.querySelectorAll('img'), 'src') : [],
    links: editor ? urls(editor.querySelectorAll('a[href]'), 'href') : [],
  };
}

export function normalizeArticleUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== 'articles.zsxq.com'
      || url.username || url.password || (url.port && url.port !== '443')) return '';
    const match = url.pathname.match(/^\/(?:inline_form\/)?id_([A-Za-z0-9_-]+)\.html$/);
    return match ? `https://articles.zsxq.com/id_${match[1]}.html` : '';
  } catch { return ''; } // External source data may not be a URL.
}
