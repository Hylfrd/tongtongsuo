/** Only local cabinet routes may be returned to navigation; QR text is never fetched. */
export function parseCabinetQr(text: string, origin: string): string | null {
  if (!text || text.length > 2048 || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(text)) return null;
  try {
    if (!/^https?:\/\//i.test(text) && !/^\/(?!\/)/.test(text)) return null;
    const url = new URL(text, origin);
    if (url.origin !== new URL(origin).origin || url.username || url.password || url.search || url.hash) return null;
    const match = url.pathname.match(/^\/(m\/)?scan\/([^/]+)\/?$/);
    if (!match) return null;
    const code = decodeURIComponent(match[2]);
    if (!code || [...code].length > 64 || code !== code.trim() || /[/\\\p{Cc}\p{Cf}\p{Cs}]/u.test(code)) return null;
    return `/${match[1] ?? ''}scan/${encodeURIComponent(code)}`;
  } catch { return null; }
}

type CameraStream = { getTracks(): {stop(): void}[] };

/** Discards camera permissions that resolve after close, pause or another attempt. */
export class CameraLease<T extends CameraStream = MediaStream> {
  private generation = 0;
  private stream: T | null = null;
  stop(): void {
    this.generation += 1;
    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
  }
  async acquire(open: () => Promise<T>): Promise<T | null> {
    this.stop();
    const generation = this.generation;
    const stream = await open();
    if (generation !== this.generation) {
      stream.getTracks().forEach(track => track.stop());
      return null;
    }
    this.stream = stream;
    return stream;
  }
}

/** Fit the complete source inside the viewport; never crop to fill a portrait screen. */
export function containSize(width: number, height: number, viewWidth: number, viewHeight: number) {
  if (![width,height,viewWidth,viewHeight].every(value => Number.isFinite(value) && value > 0)) return null;
  const scale = Math.min(viewWidth / width, viewHeight / height);
  return {width:width*scale, height:height*scale};
}

/** Match the centered software zoom shown inside the source's own aspect-ratio box. */
export function zoomCrop(width: number, height: number, zoom=1) {
  if (![width,height,zoom].every(value => Number.isFinite(value) && value > 0) || zoom < 1) return null;
  const cropWidth=width/zoom, cropHeight=height/zoom;
  return {x:(width-cropWidth)/2, y:(height-cropHeight)/2, width:cropWidth, height:cropHeight};
}

export type NativeQrDetector = {detect(source: CanvasImageSource): Promise<{rawValue:string}[]>};
export type NativeQrConstructor = {new(options:{formats:string[]}): NativeQrDetector; getSupportedFormats(): Promise<string[]>};
export async function chooseNativeQrDetector(Constructor?: NativeQrConstructor): Promise<NativeQrDetector|null> {
  if (!Constructor?.getSupportedFormats) return null;
  try {
    const formats=await Constructor.getSupportedFormats();
    return formats.includes('qr_code') ? new Constructor({formats:['qr_code']}) : null;
  } catch { return null; }
}

/** A suspended native decoder must not hold the scanning loop indefinitely. */
export async function decodeWithDeadline<T>(result: Promise<T>, milliseconds=1500): Promise<T> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try {
    return await Promise.race([result, new Promise<never>((_,reject) => {
      timer=setTimeout(() => reject(new Error('Decoder timeout')),milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

/** Resume only permission already granted; closing is irreversible for this instance. */
export class CameraLifecycle<T extends CameraStream = MediaStream> {
  private lease = new CameraLease<T>();
  private closed = false;
  private visible = true;
  private granted = false;
  private denied = false;
  private starting = false;
  private generation = 0;
  private open: () => Promise<T>;
  private ready: (stream: T, current: () => boolean) => Promise<void>;
  private stopped: () => void;
  private failed: (error: unknown) => void;
  constructor(open: () => Promise<T>, ready: (stream: T, current: () => boolean) => Promise<void>, stopped: () => void, failed: (error: unknown) => void) {
    this.open=open; this.ready=ready; this.stopped=stopped; this.failed=failed;
  }
  private stop(): void {
    this.generation += 1; this.starting=false; this.lease.stop(); this.stopped();
  }
  close(): void { if (!this.closed) { this.closed=true; this.stop(); } }
  setVisible(visible: boolean): void {
    if (this.closed || this.visible === visible) return;
    this.visible=visible;
    if (!visible) this.stop();
    else if (this.granted && !this.denied) void this.start();
  }
  async start(): Promise<void> {
    if (this.closed || !this.visible || this.denied || this.starting) return;
    this.starting=true;
    const generation=++this.generation;
    const current=() => !this.closed && this.visible && generation === this.generation;
    try {
      const stream=await this.lease.acquire(async () => { const result=await this.open(); this.granted=true; return result; });
      if (!stream || !current()) {
        if (!this.closed && this.visible && this.granted && !this.starting) void this.start();
        return;
      }
      await this.ready(stream,current);
    } catch (error) {
      if (!current()) return;
      this.denied = error instanceof Error && ['NotAllowedError','SecurityError'].includes(error.name);
      this.stop(); this.failed(error);
    } finally { if (current()) this.starting=false; }
  }
}
