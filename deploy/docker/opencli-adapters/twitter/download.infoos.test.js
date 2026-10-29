import assert from 'node:assert/strict';
import test from 'node:test';

import { EmptyResultError } from '@jackwener/opencli/errors';
import { getRegistry } from '@jackwener/opencli/registry';
import './download.js';

function element({ href = '', src = '', time = false } = {}) {
    return {
        href,
        src,
        parentElement: null,
        article: null,
        linkedCard: null,
        getAttribute(name) {
            if (name === 'href') return this.href;
            return '';
        },
        querySelector(selector) {
            if (selector === 'time' && time) return { tagName: 'TIME' };
            return null;
        },
        querySelectorAll() {
            return [];
        },
        closest(selector) {
            if (selector === 'article') return this.article;
            if (selector === '[role="link"]') return this.linkedCard;
            return null;
        },
    };
}

function article({ statusId, images = [], videos = [], players = [] }) {
    const statusLink = element({ href: `/author/status/${statusId}`, time: true });
    const descendants = new Set([statusLink, ...images, ...videos, ...players]);
    const value = {
        contains(node) {
            return descendants.has(node) || node.article === value;
        },
        querySelectorAll(selector) {
            if (selector === 'a[href*="/status/"]') return [statusLink];
            if (selector === 'img[src*="pbs.twimg.com/media"]') return images;
            if (selector === 'video') return videos;
            if (selector === '[data-testid="videoPlayer"]') return players;
            return [];
        },
    };
    for (const node of descendants) node.article = value;
    return value;
}

test('single tweet extraction ignores side rail, promoted, and quoted-post media', async () => {
    const targetImage = element({ src: 'https://pbs.twimg.com/media/target.jpg?format=jpg&name=small' });
    const quotedImage = element({ src: 'https://pbs.twimg.com/media/quoted.jpg' });
    const sideRailImage = element({ src: 'https://pbs.twimg.com/media/sidebar.jpg' });
    const promotedImage = element({ src: 'https://pbs.twimg.com/media/promoted.jpg' });
    const targetArticle = article({ statusId: '123', images: [targetImage, quotedImage] });
    const quoteCard = element({ href: '/quoted/status/999' });
    quoteCard.article = targetArticle;
    quotedImage.linkedCard = quoteCard;

    const root = {
        querySelectorAll(selector) {
            if (selector === 'article') return [targetArticle];
            if (selector === 'img[src*="pbs.twimg.com/media"]') {
                return [targetImage, quotedImage, sideRailImage, promotedImage];
            }
            return [];
        },
    };

    let extracted;
    const page = {
        goto: async () => {},
        wait: async () => {},
        evaluate: async (script) => {
            extracted = Function('document', `return ${script}`)(root);
            return extracted;
        },
        getCookies: async () => {
            throw new Error('stop after extraction');
        },
    };
    const command = getRegistry().get('twitter/download');
    await assert.rejects(
        command.func(page, { 'tweet-url': 'https://x.com/author/status/123' }),
        /stop after extraction/,
    );
    assert.deepEqual(extracted, [{
        type: 'image',
        url: 'https://pbs.twimg.com/media/target.jpg?format=jpg&name=large',
    }]);
});

test('single tweet download throws EmptyResultError when the target article is absent', async () => {
    const page = {
        goto: async () => {},
        wait: async () => {},
        evaluate: async () => null,
    };
    const command = getRegistry().get('twitter/download');
    await assert.rejects(
        command.func(page, { 'tweet-url': 'https://x.com/author/status/123' }),
        EmptyResultError,
    );
});
