import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError } from '@jackwener/opencli/errors';
import { collectArticle, normalizeArticleUrl } from './article-script.js';

cli({
  site: 'zsxq', name: 'article', access: 'read',
  description: '读取知识星球文章页的可见正文', domain: 'articles.zsxq.com',
  strategy: Strategy.COOKIE, browser: true,
  args: [{ name: 'url', required: true, positional: true, help: 'HTTPS ZSXQ article URL' }],
  columns: ['status', 'url', 'title', 'body', 'complete', 'images', 'links'],
  func: async (page, kwargs) => {
    const url = normalizeArticleUrl(kwargs.url);
    if (!url) throw new ArgumentError('url must be an HTTPS articles.zsxq.com article URL');
    await page.goto(url);
    const result = await page.evaluate(`(${collectArticle.toString()})()`);
    return [result && typeof result === 'object' && 'session' in result && 'data' in result ? result.data : result];
  },
});
