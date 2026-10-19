import { test } from 'node:test';
import assert from 'node:assert/strict';
import { licenseHref, safeReturnPath } from '../src/license-navigation.ts';

test('license returns to local cabinet, mobile and admin context', () => {
  for (const path of ['/', '/m', '/m/', '/admin?section=tools', '/contact?from=%2Fscan%2FA&lang=en', '/scan/A', '/scan/new-cabinet_2027', '/m/scan/B', '/scan/%E6%96%B0%E6%9F%9C?resume=borrow&lang=en#selection', '/scan/A?resume=borrow&lang=en']) {
    assert.equal(safeReturnPath(path), path);
    const destination = new URL(licenseHref(path), 'https://toolbox.invalid');
    assert.equal(destination.pathname, '/license');
    assert.equal(safeReturnPath(destination.searchParams.get('from')), path);
  }
});

test('unsafe and malformed return destinations fall back to the scan entry', () => {
  for (const path of [null, '', 'https://evil.example', '//evil.example', '/\\evil.example', '/scan/..', '/scan/%2e%2e', '/m/../admin', '/scan/A%2FB', '/scan/A%5CB', '/scan/%252f%252fevil', '/scan/%00', '/scan/%E2%80%AE', '/admin?section=%0a', '/admin#%5c', '/scan/%ZZ', '/license', '/license?from=/license', '/api/health', '/scan/A/other', '/scan/' + 'a'.repeat(65), '/scan/A\n', 'javascript:alert(1)']) {
    assert.equal(safeReturnPath(path), '/', String(path));
  }
});
