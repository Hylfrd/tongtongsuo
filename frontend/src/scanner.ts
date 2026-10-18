import jsQR from 'jsqr';
import {showToast} from './toast';
import {CameraLifecycle, coverCrop, parseCabinetQr, chooseNativeQrDetector, decodeWithDeadline, type NativeQrDetector, type NativeQrConstructor} from './scanner-core';
import './scanner.css';

export {parseCabinetQr} from './scanner-core';

const copy = {
  zh: {title:'扫描柜子二维码', close:'关闭扫码', denied:'请允许使用摄像头，然后关闭扫码窗口重新打开。', unavailable:'暂时无法使用摄像头，请关闭扫码窗口后重试。', invalid:'这不是本站的柜子二维码。'},
  en: {title:'Scan cabinet QR code', close:'Close scanner', denied:'Allow camera access, then close and reopen the scanner.', unavailable:'Camera unavailable. Close the scanner and try again.', invalid:'This is not a cabinet QR code for this site.'},
} as const;
let closeActive: (() => void) | null = null;

export async function openCabinetScanner(language: 'zh'|'en', onResult: (path: string) => void): Promise<void> {
  closeActive?.();
  const text=copy[language], previousFocus=document.activeElement;
  const dialog=document.createElement('dialog');
  dialog.className='cabinet-scanner';
  dialog.setAttribute('aria-labelledby','scanner-title');
  dialog.innerHTML=`<header><h2 id="scanner-title">${text.title}</h2><button type="button" class="scanner-close" aria-label="${text.close}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header><div class="scanner-view"><video autoplay playsinline muted></video><span class="scanner-frame" aria-hidden="true"></span></div>`;
  const video=dialog.querySelector('video')!;
  const viewport=dialog.querySelector<HTMLDivElement>('.scanner-view')!;
  const canvas=document.createElement('canvas');
  const context=canvas.getContext('2d',{willReadFrequently:true});
  let closed=false, decoding=false, native:NativeQrDetector|null=null;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let lastInvalid='', invalidAt=0;
  const Constructor=(globalThis as typeof globalThis & {BarcodeDetector?:NativeQrConstructor}).BarcodeDetector;
  void chooseNativeQrDetector(Constructor).then(detector => { if (!closed) native=detector; });

  function close(): void {
    if (closed) return;
    closed=true; lifecycle.close();
    document.removeEventListener('visibilitychange',visibility);
    window.removeEventListener('pagehide',close);
    window.removeEventListener('popstate',close);
    window.removeEventListener('hashchange',close);
    if (dialog.open) dialog.close();
    dialog.remove();
    if (closeActive === close) closeActive=null;
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
  }
  function accept(value: string): boolean {
    const path=parseCabinetQr(value,location.origin);
    if (!path) {
      if (lastInvalid !== value || Date.now()-invalidAt > 9000) {
        lastInvalid=value; invalidAt=Date.now(); showToast(text.invalid,'error');
      }
      return false;
    }
    close(); onResult(path); return true;
  }
  async function tick(current: () => boolean): Promise<void> {
    if (!current() || closed || !video.srcObject) return;
    if (decoding || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
      timer=setTimeout(() => void tick(current),180); return;
    }
    decoding=true;
    try {
      const crop=coverCrop(video.videoWidth,video.videoHeight,viewport.clientWidth,viewport.clientHeight);
      if (!crop || !context) return;
      const scale=Math.min(1,1280/Math.max(crop.width,crop.height));
      canvas.width=Math.max(1,Math.round(crop.width*scale)); canvas.height=Math.max(1,Math.round(crop.height*scale));
      context.drawImage(video,crop.x,crop.y,crop.width,crop.height,0,0,canvas.width,canvas.height);
      let value:string|undefined;
      if (native) {
        try { value=(await decodeWithDeadline(native.detect(canvas)))[0]?.rawValue; }
        catch { native=null; }
        if (!current() || closed) return;
      }
      if (!native) {
        const pixels=context.getImageData(0,0,canvas.width,canvas.height);
        value=jsQR(pixels.data,pixels.width,pixels.height,{inversionAttempts:'attemptBoth'})?.data;
      }
      if (current() && value && accept(value)) return;
    } catch { if (current()) showToast(text.unavailable,'error'); }
    finally {
      decoding=false;
      if (current() && !closed) timer=setTimeout(() => void tick(current),180);
    }
  }
  const lifecycle=new CameraLifecycle<MediaStream>(
    async () => {
      if (!navigator.mediaDevices?.getUserMedia || !context) throw new Error('Camera unavailable');
      return navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}});
    },
    async (stream,current) => {
      if (!current()) return;
      video.srcObject=stream;
      await video.play();
      if (current()) void tick(current);
    },
    () => { clearTimeout(timer); video.pause(); video.srcObject=null; },
    error => { showToast(error instanceof Error && ['NotAllowedError','SecurityError'].includes(error.name) ? text.denied : text.unavailable,'error'); },
  );
  function visibility(): void { lifecycle.setVisible(!document.hidden); }
  dialog.querySelector('.scanner-close')!.addEventListener('click',close);
  dialog.addEventListener('cancel',event => { event.preventDefault(); close(); });
  dialog.addEventListener('close',close);
  document.addEventListener('visibilitychange',visibility);
  window.addEventListener('pagehide',close);
  window.addEventListener('popstate',close);
  window.addEventListener('hashchange',close);
  document.body.append(dialog); closeActive=close; dialog.showModal();
  lifecycle.setVisible(!document.hidden);
  void lifecycle.start();
}
