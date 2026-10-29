import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, openSignedNote } from './_shared.js';
import { noteSnapshot, pageRisk } from './_screening.js';

export async function readScreeningNote(page, url) {
  const identity = await openSignedNote(page, url);
  if (await evaluate(page, `(${pageRisk})()`, 'screening risk')) {
    throw new CommandExecutionError('Xiaohongshu screening stopped: login or risk control');
  }
  const data = await evaluate(page, `(${noteSnapshot})(${JSON.stringify(identity.noteId)})`, 'screening note');
  if (!data || typeof data.content !== 'string') throw new CommandExecutionError('Xiaohongshu note state unavailable');
  return Object.entries(data).map(([field, value]) => ({ field, value }));
}

cli({
  site: 'xiaohongshu', name: 'screening-note', access: 'read',
  description: '初筛专用：一次详情导航读取正文、作者标识和已加载首屏评论',
  domain: 'www.xiaohongshu.com', strategy: Strategy.COOKIE, navigateBefore: false,
  args: [{ name: 'note-url', required: true, positional: true, help: '带签名的笔记 URL' }],
  columns: ['field', 'value'],
  func: (page, kwargs) => readScreeningNote(page, kwargs['note-url']),
});
