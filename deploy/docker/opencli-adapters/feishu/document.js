import { cli, Strategy } from '@jackwener/opencli/registry';
import { readFeishuDocument } from './utils.js';

cli({
  site: 'feishu',
  name: 'document',
  access: 'read',
  description: 'Read complete content from an allowed Feishu or Lark document URL',
  domain: 'feishu.cn',
  strategy: Strategy.COOKIE,
  browser: true,
  args: [
    { name: 'budget-seconds', type: 'int', default: 40, help: 'Per-document time budget in seconds' },
    // Declaring --timeout lifts OpenCLI's 60s default browser-command ceiling (value + 30s padding).
    { name: 'timeout', type: 'int', default: 60, help: 'Command timeout in seconds; keep it above budget-seconds' },
    { name: 'url', required: true, positional: true, help: 'HTTPS Feishu or Lark document URL' },
  ],
  columns: ['url', 'title', 'body', 'login_required', 'images', 'videos', 'media_blobs', 'complete', 'completeness', 'blocks', 'reached_end'],
  func: async (page, kwargs) => [
    await readFeishuDocument(page, String(kwargs.url || ''), { budgetSeconds: kwargs['budget-seconds'] }),
  ],
});
