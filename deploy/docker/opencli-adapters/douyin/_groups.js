import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { evaluate, open, WEB } from './_social.js';

export async function openMessages(page) {
  await open(page, `${WEB}/jingxuan`);
  const result = await evaluate(page, `(() => {
    const button = [...document.querySelectorAll('[data-e2e="something-button"]')]
      .find((element) => element.textContent.trim() === '消息');
    if (!button) return false;
    button.click();
    return true;
  })()`);
  if (!result) throw new CommandExecutionError('Douyin web message control is unavailable');
  await page.wait({ time: 1 });
}

export async function groups(page) {
  const rows = await evaluate(page, `(() => [...document.querySelectorAll('[data-e2e="conversation-item"]')]
    .map((item) => ({
      name: (item.querySelector('.conversationConversationItemtitle')?.textContent || '').trim(),
      preview: (item.querySelector('.ConversationItemHinttextBox')?.textContent || '').trim(),
      time: (item.querySelector('.ConversationItemTagNextToTitletimeStr')?.textContent || '').trim(),
    })))()`);
  if (!Array.isArray(rows)) throw new CommandExecutionError('Douyin message list could not be read');
  return rows.filter((row) => row.name && (/群/.test(row.name) || /群聊/.test(row.preview)));
}

export function groupName(value) {
  const name = String(value ?? '').trim();
  if (!name || name.length > 120) throw new ArgumentError('Expected a group name from group-list');
  return name;
}
