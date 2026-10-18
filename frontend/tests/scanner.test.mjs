import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import jsQR from 'jsqr';
import {CameraLease, parseCabinetQr} from '../src/scanner-core.ts';

const origin = 'https://bnbu.hmcl-helper.cn';

test('accepts only cabinet routes on this origin, including dynamic unicode codes', () => {
  assert.equal(parseCabinetQr(`${origin}/scan/A`, origin), '/scan/A');
  assert.equal(parseCabinetQr('/m/scan/B', origin), '/m/scan/B');
  assert.equal(parseCabinetQr(`${origin}/scan/正雅-C_01/`, origin), '/scan/%E6%AD%A3%E9%9B%85-C_01');
  for (const invalid of [
    'javascript:alert(1)', 'data:text/html,x', 'https://example.com/scan/A',
    '//example.com/scan/A', `${origin}.evil.test/scan/A`, `${origin}@evil.test/scan/A`,
    `https://user:password@bnbu.hmcl-helper.cn/scan/A`, 'http://bnbu.hmcl-helper.cn/scan/A',
    '/scan/A?preview=1', '/scan/A#fragment', '/api/admin/cabinets', '/scan/', '/scan/A/B',
    '/scan/A%2FB', '/scan/A%5CB', '/scan/%00', '/scan/A%0AB', '/scan/%E2%80%8B',
    '/scan/%20A', '/scan/%E0%A4%A', '/scan/' + 'A'.repeat(65), '/scan/A\n', 'A',
  ]) assert.equal(parseCabinetQr(invalid, origin), null, invalid);
});

function camera() {
  const stops = [0,0];
  return {stops, getTracks: () => stops.map((_,index) => ({stop:() => {stops[index] += 1;}}))};
}

test('closing stops every live track once; repeated close is harmless', async () => {
  const lease = new CameraLease();
  const stream = camera();
  assert.equal(await lease.acquire(async () => stream), stream);
  lease.stop(); lease.stop();
  assert.deepEqual(stream.stops, [1,1]);
});

test('permission resolving after close cannot restart a camera', async () => {
  const lease = new CameraLease();
  const stream = camera();
  let allow;
  const pending = lease.acquire(() => new Promise(resolve => {allow = resolve;}));
  lease.stop();
  allow(stream);
  assert.equal(await pending, null);
  assert.deepEqual(stream.stops, [1,1]);
});

test('a newer attempt owns the camera and superseded streams are stopped', async () => {
  const lease = new CameraLease();
  const old = camera(), current = camera();
  let allow;
  const pending = lease.acquire(() => new Promise(resolve => {allow = resolve;}));
  assert.equal(await lease.acquire(async () => current), current);
  allow(old);
  assert.equal(await pending, null);
  assert.deepEqual(old.stops, [1,1]);
  assert.deepEqual(current.stops, [0,0]);
  lease.stop();
  assert.deepEqual(current.stops, [1,1]);
});

test('jsQR decodes actual QR pixels without BarcodeDetector', async () => {
  // A QR matrix generated with python-qrcode 8.2; no generator is needed at runtime.
  const fixture = JSON.parse(await readFile(new URL('./fixtures/cabinet-a-qr.json', import.meta.url), 'utf8'));
  const scale = 6, width = fixture.rows.length * scale;
  const pixels = new Uint8ClampedArray(width * width * 4);
  for (let y=0;y<width;y++) for (let x=0;x<width;x++) {
    const offset = (y*width+x)*4;
    pixels.fill(fixture.rows[Math.floor(y/scale)][Math.floor(x/scale)] === '1' ? 0 : 255, offset, offset+3);
    pixels[offset+3] = 255;
  }
  const result = jsQR(pixels,width,width,{inversionAttempts:'attemptBoth'});
  assert.equal(result?.data, fixture.value);
  assert.equal(parseCabinetQr(result.data,origin), '/scan/A');
  for (let index=0;index<pixels.length;index++) if (index%4 !== 3) pixels[index] = 255-pixels[index];
  assert.equal(jsQR(pixels,width,width,{inversionAttempts:'attemptBoth'})?.data, fixture.value);
});
