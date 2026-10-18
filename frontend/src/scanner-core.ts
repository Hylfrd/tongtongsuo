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
