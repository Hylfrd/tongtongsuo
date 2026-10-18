import {test} from 'node:test';
import assert from 'node:assert/strict';
import {shouldOpenAdmin} from '../src/routing.ts';

test('fresh admin sign-in opens management, restored cabinet links stay in their cabinet', () => {
  assert.equal(shouldOpenAdmin('admin', '/scan/A', true), true);
  for (const path of ['/scan/A', '/scan/B', '/m/scan/A', '/m/scan/B', '/']) {
    assert.equal(shouldOpenAdmin('admin', path, false), false, path);
  }
  assert.equal(shouldOpenAdmin('admin', '/admin', false), true);
});
test('student identities cannot open management', () => {
  assert.equal(shouldOpenAdmin(undefined, '/admin', false), false);
  assert.equal(shouldOpenAdmin(undefined, '/scan/A', true), false);
});
