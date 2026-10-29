import assert from 'node:assert/strict';
import test from 'node:test';
import { CommandExecutionError } from '@jackwener/opencli/errors';
import { __test__ } from './tweets.js';

const { resolveUserLookup } = __test__;

for (const [status, message] of [[429, /HTTP 429.*rate-limited/], [401, /auth failed/], [500, /HTTP 500/]]) {
    test(`UserByScreenName HTTP ${status} remains an API failure`, () => {
        assert.throws(() => resolveUserLookup('example', { ok: false, status, body: null }), (error) => {
            assert.ok(error instanceof CommandExecutionError);
            assert.match(error.message, message);
            assert.doesNotMatch(error.message, /not found or unavailable/);
            return true;
        });
    });
}

test('GraphQL errors preserve the first message without claiming the source is unavailable', () => {
    for (const data of [undefined, {}, { user: {} }, { user: { result: {} } }]) {
        assert.throws(() => resolveUserLookup('example', {
            ok: true, status: 200, body: { data, errors: [{ message: 'Operation is invalid' }] },
        }), /UserByScreenName API error: Operation is invalid/);
    }
});

test('explicit missing or unavailable user is a source failure', () => {
    for (const user of [undefined, null, {}, { result: { __typename: 'UserUnavailable', reason: 'Suspended' } }]) {
        assert.throws(() => resolveUserLookup('example', { ok: true, status: 200, body: { data: { user } } }), (error) => {
            assert.ok(error instanceof CommandExecutionError);
            assert.match(error.message, /X account @example not found or unavailable/);
            if (user?.result?.reason) assert.match(error.message, /\(Suspended\)/);
            return true;
        });
    }
});

test('normal user resolves rest_id, including usable results with GraphQL warnings', () => {
    const body = { data: { user: { result: { rest_id: '1234567890123456789' } } } };
    assert.equal(resolveUserLookup('example', { ok: true, status: 200, body }), '1234567890123456789');
    body.errors = [{ message: 'Optional field failed' }];
    assert.equal(resolveUserLookup('example', { ok: true, status: 200, body }), '1234567890123456789');
});

test('malformed responses keep the ambiguous legacy failure', () => {
    for (const body of [undefined, {}, { data: { user: { result: {} } } }]) {
        assert.throws(() => resolveUserLookup('example', { ok: true, status: 200, body }), /Could not resolve @example/);
    }
});
