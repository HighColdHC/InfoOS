import assert from 'node:assert/strict';
import test from 'node:test';

import { boundedInt, commentId, profileIdentity, signedNoteIdentity } from './_shared.js';
import { submitComment } from './_comment_action.js';
import { readableMessage } from './group-messages.js';

test('write targets must be exact Xiaohongshu identities', () => {
  const note = signedNoteIdentity('https://www.xiaohongshu.com/explore/000000000000000000000002?xsec_token=signed');
  assert.equal(note.noteId, '000000000000000000000002');
  assert.equal(signedNoteIdentity('https://www.xiaohongshu.com/user/profile/000000000000000000000001/000000000000000000000002?xsec_token=signed').noteId, note.noteId);
  assert.throws(() => signedNoteIdentity('https://example.com/explore/000000000000000000000002?xsec_token=signed'));
  assert.throws(() => signedNoteIdentity('https://www.xiaohongshu.com/explore/000000000000000000000002'));
  assert.equal(profileIdentity('000000000000000000000001').userId, '000000000000000000000001');
  assert.equal(commentId('000000000000000000000003'), '000000000000000000000003');
  assert.equal(boundedInt(0, 'page', { min: 0, max: 100, fallback: 0 }), 0);
});

test('group message projection keeps stable identity and hides revoked text', () => {
  const message = { id: 'm1', store_id: 42, sender_id: 'u1', created_at: 1780000000000,
    content: JSON.stringify({ content: 'hello', content_type: 0 }) };
  assert.deepEqual(readableMessage(message, 'g1'), {
    group_id: 'g1', message_id: 'm1', store_id: 42, sender_id: 'u1',
    created_at: 1780000000000, content_type: 0, text: 'hello', revoked: false,
  });
  assert.equal(readableMessage({ ...message, revoked: true }, 'g1').text, '');
});

test('comment submission does not trust an optimistic page result when the platform rejects it', async () => {
  const clicked = [];
  const page = {
    evaluate: async (script) => {
      if (script.includes('querySelectorAll')) return [];
      if (script.includes('comment draft')) throw new Error('unexpected script');
      if (script.includes('textContent')) return '👏';
      if (script.includes('button.submit')) return true;
      throw new Error('unexpected page evaluation');
    },
    click: async (selector) => clicked.push(selector),
    typeText: async () => {},
    installInterceptor: async () => {},
    getInterceptedRequests: async () => [{ code: 300017, success: false }],
    wait: async () => {},
  };
  await assert.rejects(submitComment(page, '👏'), /rejected the comment/);
  assert.deepEqual(clicked, ['#content-textarea[contenteditable="true"]', 'button.submit:not([disabled])']);
});
