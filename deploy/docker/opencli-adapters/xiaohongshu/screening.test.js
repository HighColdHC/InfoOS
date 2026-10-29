import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { noteSnapshot, profileSnapshot, pageRisk } from './_screening.js';
import { readProfiles } from './screening-profiles.js';
import { readScreeningNote } from './screening-note.js';

function browser({ riskAt = -1, failAt = -1 } = {}) {
  let now = 0, url = '', index = -1;
  const opens = [], ends = [], delays = [];
  const page = {
    goto: async value => { index++; url = value; opens.push(now); now += 200; if (index === failAt) throw Error('navigation failed'); },
    wait: async ({ time }) => { now += time * 1000; },
    evaluate: async script => {
      if (script === '() => location.href') return url;
      if (script.includes('function pageRisk')) return index === riskAt;
      if (script.includes('function noteSnapshot')) return { content: '日常', title: '日常', author_user_id: 'author0001', comments_ok: true, first_screen_comments: [] };
      assert.ok(script.includes('function profileSnapshot'));
      ends.push(now);
      return { profile_ok: true, description: '日常', pinned_ok: true, pinned_titles: [], group_entry: false, shop_entry: false };
    },
  };
  return { page, opens, ends, delays, wait: async ms => { delays.push(ms); now += ms; } };
}

test('profile batch deduplicates, caps at 20 and waits three seconds after each page completes', async () => {
  const b = browser();
  const users = ['author0000', ...Array.from({ length: 25 }, (_, i) => `author${String(i).padStart(4, '0')}`)];
  const rows = await readProfiles(b.page, users, b.wait);
  assert.equal(rows.length, 20);
  assert.equal(b.opens.length, 20);
  assert.equal(new Set(rows.map(r => r.user_id)).size, 20);
  assert.ok(rows.every(r => r.profile_checked && r.profile_ok));
  for (let i = 1; i < b.opens.length; i++) assert.ok(b.opens[i] - b.ends[i - 1] >= 3000);
  assert.deepEqual(b.delays, Array(19).fill(3000));
});

test('risk or navigation failure stops the entire batch with remaining authors unknown', async () => {
  for (const options of [{ riskAt: 0 }, { failAt: 0 }]) {
    const b = browser(options);
    const rows = await readProfiles(b.page, ['author0000', 'author0001'], b.wait);
    assert.equal(b.opens.length, 1);
    assert.equal(rows[0].profile_checked, true);
    assert.ok(rows.every(r => r.profile_ok === false));
    assert.equal(rows[1].profile_checked, false);
  }
});

test('screening note navigates once and returns comments from the same page', async () => {
  const b = browser();
  const rows = await readScreeningNote(b.page, 'https://www.xiaohongshu.com/explore/000000000000000000000001?xsec_token=fixture');
  assert.equal(b.opens.length, 1);
  assert.equal(rows.find(r => r.field === 'comments_ok').value, true);
});

function read(fn, window, document = {}, args = '') {
  return JSON.parse(JSON.stringify(vm.runInNewContext(`(${fn})(${args})`, {
    window, document, location: { href: 'https://www.xiaohongshu.com/user/profile/author0001' },
    fetch: () => { throw Error('Page readers must never fetch'); },
  })));
}

test('note reader validates identity and uses root comment IDs and explicit author markers', () => {
  const comment = (id, userId, tags = []) => ({ id, content: '求教程', userInfo: { userId }, showTags: tags });
  const entry = { note: { noteId: 'note1', title: '日常', desc: '', user: { userId: 'self', nickname: '作者' }, tagList: [] },
    comments: { firstRequestFinish: true, list: [comment('c1', 'self'), comment('c2', 'other', ['user_top']),
      comment('c2', 'other'), comment('c3', 'other', ['is_author'])] } };
  const window = { __INITIAL_STATE__: { note: { noteDetailMap: { _value: { note1: entry } } } } };
  const row = read(noteSnapshot, window, {}, '"note1"');
  assert.equal(row.comments_ok, true);
  assert.deepEqual(row.first_screen_comments.map(c => [c.is_author, c.pinned]), [[true, false], [false, true], [true, false]]);
  assert.equal(read(noteSnapshot, window, {}, '"wrong"'), null);
  entry.comments.list.push({ content: "求教程" });
  assert.equal(read(noteSnapshot, window, {}, '"note1"').comments_ok, false);
  entry.comments.firstRequestFinish = false;
  assert.equal(read(noteSnapshot, window, {}, '"note1"').comments_ok, false);
});

function element(text, visible = true) {
  return { textContent: text, getClientRects: () => visible ? [{}] : [] };
}

test('profile reader gets DOM pinned titles and only visible exact group controls', () => {
  const badge = element('置顶');
  const card = { ...element('置顶'), querySelectorAll: () => [badge], querySelector: () => element('课程说明') };
  const controls = [element('申请入群'), element('店铺'), element('加入群聊', false), element('我的群聊日记')];
  const window = { __INITIAL_STATE__: { user: { userPageData: { _value: { basicInfo: { desc: '简介', nickname: '作者' }, posted: 4 } } } } };
  const document = { querySelectorAll: selector => selector === 'section.note-item' ? [card] : controls };
  const row = read(profileSnapshot, window, document);
  assert.equal(row.profile_ok, true);
  assert.deepEqual(row.pinned_titles, ['课程说明']);
  assert.equal(row.group_entry, true);
  assert.equal(row.shop_entry, true);
  controls.splice(0, 1);
  assert.equal(read(profileSnapshot, window, document).group_entry, false);
  document.querySelectorAll = () => [];
  assert.equal(read(profileSnapshot, window, document).pinned_ok, false);
  window.__INITIAL_STATE__.user.userPageData._value.posted = 0;
  assert.equal(read(profileSnapshot, window, document).pinned_ok, true);
});

test('risk reader detects visible captcha and frequency messages without requests', () => {
  assert.equal(read(pageRisk, {}, { body: { innerText: '操作频繁' }, querySelectorAll: () => [] }), true);
  assert.equal(read(pageRisk, {}, { body: { innerText: '日常' }, querySelectorAll: () => [element('', true)] }), true);
});
