import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { api, evaluate } from './_social.js';

export function commentText(value) {
  const text = String(value ?? '').trim();
  if (!text || text.length > 500) throw new ArgumentError('Comment text must contain 1-500 characters');
  return text;
}

export async function postComment(page, awemeId, text, replyId = '') {
  const elapsed = Number(await evaluate(page, 'Math.max(0, Math.floor(performance.now() / 1000) * 1000)'));
  const form = {
    aweme_id: awemeId, text, text_extra: '[]',
    one_level_comment_rank: replyId ? '1' : '-1',
    paste_edit_method: 'non_paste',
    comment_send_celltime: String(elapsed),
    comment_video_celltime: String(elapsed + 5000),
  };
  if (replyId) form.reply_id = replyId;
  const data = await api(page, '/aweme/v1/web/comment/publish/', {
    aweme_id: awemeId, item_type: 0, app_name: 'aweme',
    enter_from: 'video_detail', previous_page: 'homepage',
  }, {
    method: 'POST', form,
  });
  const comment = data.comment || {};
  const id = String(comment.cid || data.cid || '');
  if (!/^\d{15,22}$/.test(id)) {
    throw new CommandExecutionError('Douyin accepted comment submission but returned no comment ID; outcome unknown, do not retry automatically');
  }
  return id;
}
