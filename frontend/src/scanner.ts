import jsQR from 'jsqr';
import {showToast} from './toast';
import {CameraLease, parseCabinetQr} from './scanner-core';
import './scanner.css';

export {parseCabinetQr} from './scanner-core';

const copy = {
  zh: {title:'扫描柜子二维码', close:'关闭扫码', aim:'将柜子上的二维码放入画面', starting:'正在打开摄像头…', camera:'打开摄像头', upload:'从图片识别', paused:'摄像头已暂停', denied:'摄像头权限未开启，可重试或选择二维码图片。', unavailable:'无法使用摄像头，请选择二维码图片。', invalid:'这不是本站的柜子二维码。', missing:'图片中未找到二维码，请换一张清晰图片。', image:'请选择不超过 10 MB 的 PNG、JPEG 或 WebP 图片。'},
  en: {title:'Scan cabinet QR code', close:'Close scanner', aim:'Place the cabinet QR code in the frame', starting:'Opening camera…', camera:'Open camera', upload:'Choose QR image', paused:'Camera paused', denied:'Camera permission was denied. Retry or choose a QR image.', unavailable:'Camera unavailable. Please choose a QR image.', invalid:'This is not a cabinet QR code for this site.', missing:'No QR code found. Please choose a clearer image.', image:'Choose a PNG, JPEG or WebP image under 10 MB.'},
} as const;
let closeActive: (() => void) | null = null;

export async function openCabinetScanner(language: 'zh'|'en', onResult: (path: string) => void): Promise<void> {
  closeActive?.();
  const text = copy[language];
  const previousFocus = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'cabinet-scanner';
  dialog.setAttribute('aria-labelledby', 'scanner-title');
  dialog.innerHTML = `<header><h2 id="scanner-title">${text.title}</h2><button type="button" class="scanner-close" aria-label="${text.close}">×</button></header><div class="scanner-view"><video autoplay playsinline muted></video><span class="scanner-frame" aria-hidden="true"></span></div><p class="scanner-status" role="status"></p><div class="scanner-actions"><button type="button" class="scanner-camera">${text.camera}</button><button type="button" class="scanner-upload">${text.upload}</button><input type="file" accept="image/png,image/jpeg,image/webp" hidden></div>`;
  const video = dialog.querySelector('video')!;
  const status = dialog.querySelector<HTMLParagraphElement>('.scanner-status')!;
  const camera = dialog.querySelector<HTMLButtonElement>('.scanner-camera')!;
  const upload = dialog.querySelector<HTMLInputElement>('input')!;
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', {willReadFrequently:true});
  const lease = new CameraLease();
  let closed = false, timer: ReturnType<typeof setTimeout> | undefined;
  let imageUrl: string | null = null, imageGeneration = 0, cameraGeneration = 0;
  let lastInvalid = '';

  function stopCamera(): void {
    cameraGeneration += 1;
    clearTimeout(timer);
    lease.stop();
    video.pause();
    video.srcObject = null;
    camera.disabled = false;
  }
  function releaseImage(): void {
    imageGeneration += 1;
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = null;
  }
  function close(): void {
    if (closed) return;
    closed = true;
    stopCamera(); releaseImage();
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', close);
    window.removeEventListener('popstate', close);
    window.removeEventListener('hashchange', close);
    if (dialog.open) dialog.close();
    dialog.remove();
    if (closeActive === close) closeActive = null;
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
  }
  function accept(value: string): boolean {
    const path = parseCabinetQr(value, location.origin);
    if (!path) {
      stopCamera();
      status.textContent = text.invalid;
      if (lastInvalid !== value) { lastInvalid = value; showToast(text.invalid, 'error'); }
      return false;
    }
    close(); onResult(path); return true;
  }
  function decode(source: CanvasImageSource, width: number, height: number): string | null {
    if (!context || !width || !height) return null;
    const scale = Math.min(1, 1600 / Math.max(width,height));
    canvas.width = Math.max(1, Math.round(width*scale));
    canvas.height = Math.max(1, Math.round(height*scale));
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    const frame = context.getImageData(0,0,canvas.width,canvas.height);
    return jsQR(frame.data, frame.width, frame.height, {inversionAttempts:'attemptBoth'})?.data ?? null;
  }
  function tick(): void {
    if (closed || document.hidden || !video.srcObject) return;
    try {
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        const value = decode(video,video.videoWidth,video.videoHeight);
        if (value) { accept(value); return; }
      }
      timer = setTimeout(tick,180);
    } catch {
      stopCamera(); status.textContent = text.unavailable; showToast(text.unavailable,'error');
    }
  }
  async function startCamera(): Promise<void> {
    if (closed || document.hidden) return;
    stopCamera(); releaseImage();
    const generation = cameraGeneration;
    camera.disabled = true; status.textContent = text.starting;
    try {
      if (!navigator.mediaDevices?.getUserMedia || !context) throw new Error('Unavailable');
      const stream = await lease.acquire(() => navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}}}));
      if (!stream || closed || generation !== cameraGeneration) return;
      video.srcObject = stream;
      await video.play();
      if (closed || generation !== cameraGeneration) return;
      status.textContent = text.aim; tick();
    } catch (error) {
      if (closed || generation !== cameraGeneration) return;
      stopCamera();
      const message = error instanceof DOMException && ['NotAllowedError','SecurityError'].includes(error.name) ? text.denied : text.unavailable;
      status.textContent = message; showToast(message,'error');
    } finally { if (!closed && generation === cameraGeneration) camera.disabled = false; }
  }
  function visibility(): void {
    if (document.hidden) { stopCamera(); releaseImage(); status.textContent = text.paused; }
    // Resume only through a new tap, including browsers requiring a user gesture.
  }
  async function scanImage(file: File): Promise<void> {
    stopCamera(); releaseImage();
    if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 10*1024*1024) { showToast(text.image,'error'); return; }
    const generation = imageGeneration;
    imageUrl = URL.createObjectURL(file);
    const image = new Image();
    image.src = imageUrl;
    try {
      await image.decode();
      if (closed || generation !== imageGeneration) return;
      if (image.naturalWidth > 16384 || image.naturalHeight > 16384) throw new Error('Image too large');
      const value = decode(image,image.naturalWidth,image.naturalHeight);
      if (value) accept(value); else { status.textContent = text.missing; showToast(text.missing,'error'); }
    } catch { if (!closed && generation === imageGeneration) { status.textContent = text.image; showToast(text.image,'error'); } }
    finally { if (generation === imageGeneration) releaseImage(); }
  }
  dialog.querySelector('.scanner-close')!.addEventListener('click',close);
  dialog.addEventListener('cancel',event => { event.preventDefault(); close(); });
  dialog.addEventListener('close',close);
  camera.addEventListener('click',() => void startCamera());
  dialog.querySelector('.scanner-upload')!.addEventListener('click',() => { stopCamera(); releaseImage(); upload.click(); });
  upload.addEventListener('change',() => { const file = upload.files?.[0]; upload.value=''; if (file) void scanImage(file); });
  document.addEventListener('visibilitychange',visibility);
  window.addEventListener('pagehide',close);
  window.addEventListener('popstate',close);
  window.addEventListener('hashchange',close);
  document.body.append(dialog);
  closeActive = close;
  dialog.showModal();
  // Permission prompts may remain pending even after our dialog is closed.
  // Let the caller restore its launch button immediately; CameraLease still
  // releases any stream delivered after close or another attempt.
  void startCamera();
}
