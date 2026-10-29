import { ArgumentError, CommandExecutionError } from '@jackwener/opencli/errors';
import { api, evaluate, open, profileId, WEB } from './_social.js';
import { openMessages } from './_groups.js';

const EDITOR = '[data-e2e="msg-input"] [data-slate-editor="true"][contenteditable="true"]';
const SEND = '[data-e2e="msg-input"] .e2e-send-msg-btn';

export function messageText(value) {
  const text = String(value ?? '').trim();
  if (!text || text.length > 500 || /[\r\n]/.test(text)) {
    throw new ArgumentError('DM text must be one nonempty line of at most 500 characters');
  }
  return text;
}

export async function contacts(page) {
  await openMessages(page);
  const rows = await evaluate(page, `(() => [...document.querySelectorAll('[data-e2e="conversation-item"]')]
    .filter(item => item.querySelector('.commonIMAvataravatarContainer'))
    .map(item => ({
      name: (item.querySelector('.conversationConversationItemtitle')?.textContent || '').trim(),
      preview: (item.querySelector('.ConversationItemHinttextBox')?.textContent || '').trim(),
      time: (item.querySelector('.ConversationItemTagNextToTitletimeStr')?.textContent || '').trim(),
    })).filter(row => row.name))()`);
  if (!Array.isArray(rows)) throw new CommandExecutionError('Douyin contacts could not be read');
  return rows;
}

async function conversation(page, expectedName) {
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const state = await evaluate(page, `(() => {
      const header = document.querySelector('.StackLayoutStackChatHeaderinfoContainer');
      return {
        name: (header?.querySelector('.StackLayoutStackChatHeadertitle')?.textContent || '').trim(),
        person: !!header?.querySelector('[data-apm-action="个人页卡片"] .commonIMAvataravatarContainer'),
        editor: !!document.querySelector(${JSON.stringify(EDITOR)}),
        send: !!document.querySelector(${JSON.stringify(SEND)}),
      };
    })()`);
    if (state.name === expectedName && state.person && state.editor && state.send) return state;
    if (state.name === expectedName && !state.person) {
      throw new CommandExecutionError('Target is not a one-to-one Douyin conversation; nothing was sent');
    }
    await page.wait({ time: 1 });
  }
  throw new CommandExecutionError(`Douyin one-to-one composer for ${expectedName} was not available; nothing was sent`);
}

export async function openDmTarget(page, value) {
  const raw = String(value ?? '').trim();
  if (raw.startsWith('contact:')) {
    const name = raw.slice('contact:'.length).trim();
    if (!name) throw new ArgumentError('Expected contact:<exact conversation name>');
    const rows = await contacts(page);
    const matches = rows.filter(row => row.name === name);
    if (matches.length !== 1) {
      throw new CommandExecutionError(`Expected one visible individual contact named ${name}; found ${matches.length}`);
    }
    const opened = await evaluate(page, `(() => {
      const rows = [...document.querySelectorAll('[data-e2e="conversation-item"]')]
        .filter(item => (item.querySelector('.conversationConversationItemtitle')?.textContent || '').trim() === ${JSON.stringify(name)});
      if (rows.length !== 1) return false;
      rows[0].dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
      return true;
    })()`);
    if (!opened) throw new CommandExecutionError('Contact changed before opening conversation; nothing was sent');
    await conversation(page, name);
    return { kind: 'contact', name, author_url: '' };
  }

  const id = profileId(raw);
  const authorUrl = `${WEB}/user/${id}`;
  await open(page, authorUrl, `/user/${id}`);
  const data = await api(page, '/aweme/v1/web/user/profile/other/', { sec_user_id: id });
  const name = String((data.user || data.user_info || {}).nickname || '').trim();
  if (!name) throw new CommandExecutionError('Douyin author identity could not be verified; nothing was sent');
  await page.wait({ time: 2 });
  const clicked = await evaluate(page, `(() => {
    const buttons = [...document.querySelectorAll('button')]
      .filter(button => button.getClientRects().length && button.textContent.trim() === '私信' && button.getAttribute('aria-disabled') !== 'true');
    if (buttons.length !== 1) return false;
    buttons[0].click();
    return true;
  })()`);
  if (!clicked) throw new CommandExecutionError('Author has no unique enabled DM control; nothing was sent');
  await conversation(page, name);
  return { kind: 'author', name, author_url: authorUrl };
}

export async function sendDm(page, target, text) {
  const before = await evaluate(page, `(() => ({
    name: (document.querySelector('.StackLayoutStackChatHeadertitle')?.textContent || '').trim(),
    count: [...document.querySelectorAll('.messageMessageListlist .messageMessageBoxisFromMe .TextMessageTextpureText')]
      .filter(item => item.textContent.trim() === ${JSON.stringify(text)}).length,
  }))()`);
  if (before.name !== target.name) throw new CommandExecutionError('DM target changed before composing; nothing was sent');
  await page.click(EDITOR);
  await page.typeText(EDITOR, text);
  const draft = await evaluate(page, `(() => ({
    name: (document.querySelector('.StackLayoutStackChatHeadertitle')?.textContent || '').trim(),
    text: (document.querySelector(${JSON.stringify(EDITOR)})?.textContent || '').replace(/\\u200b/g, '').trim(),
    send: !!document.querySelector(${JSON.stringify(SEND)}),
  }))()`);
  if (draft.name !== target.name || draft.text !== text || !draft.send) {
    throw new CommandExecutionError('DM draft or target could not be verified; nothing was sent');
  }
  await page.click(SEND);
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const after = await evaluate(page, `(() => ({
      name: (document.querySelector('.StackLayoutStackChatHeadertitle')?.textContent || '').trim(),
      count: [...document.querySelectorAll('.messageMessageListlist .messageMessageBoxisFromMe .TextMessageTextpureText')]
        .filter(item => item.textContent.trim() === ${JSON.stringify(text)}).length,
      draft: (document.querySelector(${JSON.stringify(EDITOR)})?.textContent || '').replace(/\\u200b/g, '').trim(),
    }))()`);
    if (after.name === target.name && after.count > before.count && !after.draft) {
      return { ...target, status: 'sent_visible' };
    }
    await page.wait({ time: 1 });
  }
  throw new CommandExecutionError('DM send was attempted but the new message was not verified in the conversation; outcome unknown, do not retry automatically');
}
