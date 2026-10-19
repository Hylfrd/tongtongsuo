import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import jsQR from 'jsqr';
import {CameraLease, CameraLifecycle, containSize, zoomCrop, parseCabinetQr, chooseNativeQrDetector, decodeWithDeadline} from '../src/scanner-core.ts';

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

const settle = () => new Promise(resolve => setImmediate(resolve));

test('camera stops in background and resumes automatically with a fresh stream', async () => {
  const streams=[], checks=[];
  const life=new CameraLifecycle(async () => {const stream=camera(); streams.push(stream); return stream;},
    async (_,current) => {checks.push(current);}, () => {}, error => assert.fail(String(error)));
  await life.start();
  assert.equal(checks[0](),true);
  life.setVisible(false);
  assert.deepEqual(streams[0].stops,[1,1]);
  assert.equal(checks[0](),false);
  life.setVisible(true);
  await settle();
  assert.equal(streams.length,2);
  assert.equal(checks[1](),true);
  life.close(); life.setVisible(false); life.setVisible(true);
  await settle();
  assert.deepEqual(streams[1].stops,[1,1]);
  assert.equal(streams.length,2);
});

test('closing during foreground reacquisition stops a late stream and never restarts', async () => {
  const first=camera(), late=camera();
  let requests=0, allow, ready=0;
  const life=new CameraLifecycle(() => ++requests===1 ? Promise.resolve(first) : new Promise(resolve => {allow=resolve;}),
    async () => {ready++;}, () => {}, error => assert.fail(String(error)));
  await life.start(); life.setVisible(false); life.setVisible(true);
  life.close(); allow(late); await settle();
  assert.equal(requests,2); assert.equal(ready,1);
  assert.deepEqual(late.stops,[1,1]);
});

test('first permission granted after background round trip resumes without leaving a paused screen', async () => {
  const discarded=camera(), active=camera();
  let requests=0, allow, ready=0;
  const life=new CameraLifecycle(() => ++requests===1 ? new Promise(resolve => {allow=resolve;}) : Promise.resolve(active),
    async () => {ready++;}, () => {}, error => assert.fail(String(error)));
  const first=life.start(); life.setVisible(false); life.setVisible(true);
  allow(discarded); await first; await settle();
  assert.equal(requests,2); assert.equal(ready,1);
  assert.deepEqual(discarded.stops,[1,1]);
  life.close(); assert.deepEqual(active.stops,[1,1]);
});

test('permission denial does not cause automatic prompts on returning to the tab', async () => {
  let requests=0, errors=0;
  const life=new CameraLifecycle(async () => {requests++; throw new DOMException('Denied','NotAllowedError');},
    async () => assert.fail('No permission'), () => {}, () => {errors++;});
  await life.start(); life.setVisible(false); life.setVisible(true); await settle();
  assert.equal(requests,1); assert.equal(errors,1);
  life.close();
});

test('asynchronous decoder results from a closed or paused stream cannot navigate', async () => {
  let resolveDetection, accepted=0;
  const decoded=new Promise(resolve => {resolveDetection=resolve;});
  const life=new CameraLifecycle(async () => camera(), async (_,current) => {
    void decoded.then(() => {if(current()) accepted++;});
  }, () => {}, error => assert.fail(String(error)));
  await life.start(); life.setVisible(false); life.close(); resolveDetection('/scan/A'); await settle();
  assert.equal(accepted,0);
});

test('portrait scanning preserves the full source at 1x and decodes the same crop as image zoom', () => {
  assert.deepEqual(containSize(1920,1080,390,844),{width:390,height:219.375});
  assert.deepEqual(zoomCrop(1920,1080),{x:0,y:0,width:1920,height:1080});
  assert.deepEqual(zoomCrop(1920,1080,2),{x:480,y:270,width:960,height:540});
  assert.deepEqual(containSize(1080,1920,390,600),{width:337.5,height:600});
  assert.deepEqual(zoomCrop(1080,1920,2),{x:270,y:480,width:540,height:960});
  assert.equal(containSize(0,720,390,844),null);
  assert.equal(zoomCrop(1280,720,0.5),null);
});

test('native detection is selected only when QR support is advertised', async () => {
  let options;
  class Supported {
    static async getSupportedFormats() { return ['qr_code','ean_13']; }
    constructor(value) {options=value;}
    async detect() {return [];}
  }
  assert.ok(await chooseNativeQrDetector(Supported) instanceof Supported);
  assert.deepEqual(options,{formats:['qr_code']});
  class Unsupported extends Supported {static async getSupportedFormats() {return ['ean_13'];}}
  class Failed extends Supported {static async getSupportedFormats() {throw new Error('Unavailable');}}
  assert.equal(await chooseNativeQrDetector(Unsupported),null);
  assert.equal(await chooseNativeQrDetector(Failed),null);
  assert.equal(await chooseNativeQrDetector(),null);
});

test('a hanging native detector yields to fallback and late native results are discarded', async () => {
  let finish, accepted=false;
  const pending=new Promise(resolve => {finish=resolve;});
  await assert.rejects(decodeWithDeadline(pending,5).then(() => {accepted=true;}),/Decoder timeout/);
  finish([{rawValue:'/scan/A'}]); await settle();
  assert.equal(accepted,false);
  assert.deepEqual(await decodeWithDeadline(Promise.resolve([{rawValue:'/scan/B'}])),[{rawValue:'/scan/B'}]);
});
