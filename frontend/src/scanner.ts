import jsQR from 'jsqr';
import {showToast} from './toast';
import {CameraLifecycle, containSize, zoomCrop, parseCabinetQr, chooseNativeQrDetector, decodeWithDeadline, type NativeQrDetector, type NativeQrConstructor} from './scanner-core';
import './scanner.css';

export {parseCabinetQr} from './scanner-core';

const copy = {
  zh: {title:'扫描柜子二维码', close:'关闭扫码', denied:'请允许使用摄像头，然后关闭扫码窗口重新打开。', unavailable:'暂时无法使用摄像头，请关闭扫码窗口后重试。', invalid:'这不是本站的柜子二维码。', cameraZoom:'相机缩放', imageZoom:'画面缩放', resetZoom:'还原缩放'},
  en: {title:'Scan cabinet QR code', close:'Close scanner', denied:'Allow camera access, then close and reopen the scanner.', unavailable:'Camera unavailable. Close the scanner and try again.', invalid:'This is not a cabinet QR code for this site.', cameraZoom:'Camera zoom', imageZoom:'Image zoom', resetZoom:'Reset zoom'},
} as const;
let closeActive: (() => void) | null = null;
type ZoomRange = {min:number; max:number; step?:number};
type ZoomSettings = MediaTrackSettings & {zoom?:number};
type ZoomCapabilities = MediaTrackCapabilities & {zoom?:ZoomRange};
type ZoomConstraintSet = MediaTrackConstraintSet & {zoom?:number};

export async function openCabinetScanner(language: 'zh'|'en', onResult: (path: string) => void): Promise<void> {
  closeActive?.();
  const text=copy[language], previousFocus=document.activeElement;
  const dialog=document.createElement('dialog');
  dialog.className='cabinet-scanner';
  dialog.setAttribute('aria-labelledby','scanner-title');
  dialog.innerHTML=`<header><h2 id="scanner-title">${text.title}</h2><button type="button" class="scanner-close" aria-label="${text.close}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header><div class="scanner-view"><div class="scanner-media"><video autoplay playsinline muted></video><span class="scanner-frame" aria-hidden="true"></span></div></div><div class="scanner-zoom"><label for="scanner-zoom">${text.imageZoom}</label><input id="scanner-zoom" type="range" min="1" max="4" step="0.01" value="1" disabled><button class="scanner-zoom-reset" type="button" title="${text.resetZoom}" aria-label="${text.resetZoom}" disabled>1×</button></div>`;
  const video=dialog.querySelector('video')!;
  const viewport=dialog.querySelector<HTMLDivElement>('.scanner-view')!;
  const media=dialog.querySelector<HTMLDivElement>('.scanner-media')!;
  const zoomSlider=dialog.querySelector<HTMLInputElement>('#scanner-zoom')!;
  const zoomLabel=dialog.querySelector<HTMLLabelElement>('.scanner-zoom label')!;
  const zoomReset=dialog.querySelector<HTMLButtonElement>('.scanner-zoom-reset')!;
  const canvas=document.createElement('canvas');
  const context=canvas.getContext('2d',{willReadFrequently:true});
  let closed=false, decoding=false, native:NativeQrDetector|null=null;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let lastInvalid='', invalidAt=0;
  let softwareZoom=1, previewRevision=0;
  let hardwareZoom:{track:MediaStreamTrack; range:ZoomRange; current:()=>boolean; pending:number|null; applying:boolean}|null=null;
  const pointers=new Map<number,{x:number;y:number}>();
  let pinch:{distance:number;zoom:number}|null=null;
  function layoutPreview(): void {
    const size=containSize(video.videoWidth,video.videoHeight,viewport.clientWidth,viewport.clientHeight);
    if (!size) return;
    media.style.width=`${size.width}px`; media.style.height=`${size.height}px`;
    media.style.setProperty('--scanner-frame-size',`${Math.min(size.width,size.height)*0.72}px`);
  }
  const resizeObserver=new ResizeObserver(layoutPreview);
  resizeObserver.observe(viewport);
  video.addEventListener('resize',layoutPreview);

  function renderZoom(value:number): void {
    previewRevision++;
    zoomSlider.value=String(value);
    const label=`${Number(value.toFixed(1))}×`;
    zoomReset.textContent=label;
    zoomSlider.setAttribute('aria-valuetext',label);
    video.style.transform=`scale(${softwareZoom})`;
  }
  function useSoftwareZoom(): void {
    hardwareZoom=null; softwareZoom=1;
    zoomLabel.textContent=text.imageZoom; zoomSlider.max='4'; zoomSlider.step='0.01';
    renderZoom(1);
  }
  function readZoom(track:MediaStreamTrack): number|undefined {
    const value=(track.getSettings() as ZoomSettings).zoom;
    return typeof value==='number' && Number.isFinite(value) && value>0 ? value : undefined;
  }
  async function applyZoom(track:MediaStreamTrack,value:number): Promise<number|undefined> {
    // applyConstraints replaces constraints, so retain the capture preferences and unrelated settings.
    const constraints=track.getConstraints() as MediaTrackConstraints & {zoom?:number};
    delete constraints.zoom;
    const advanced=constraints.advanced?.map(entry => {
      const retained={...entry} as ZoomConstraintSet; delete retained.zoom; return retained;
    }).filter(entry => Object.keys(entry).length>0) ?? [];
    await track.applyConstraints({...constraints,advanced:[...advanced,{zoom:value} as ZoomConstraintSet]});
    return readZoom(track);
  }
  async function configureZoom(stream:MediaStream,current:()=>boolean): Promise<void> {
    useSoftwareZoom();
    zoomSlider.disabled=false; zoomReset.disabled=false;
    const track=stream.getVideoTracks()[0];
    try {
      const range=(track.getCapabilities?.() as ZoomCapabilities|undefined)?.zoom;
      const initial=readZoom(track);
      if (range && initial !== undefined && Number.isFinite(range.min) && range.min>0 && Number.isFinite(range.max) && range.max>range.min) {
        // The widest available track view is the reset point, independent of a previous app's zoom.
        const actual=await applyZoom(track,range.min);
        if (!current()) return;
        if (actual !== undefined && Math.abs(actual-range.min)<Math.max(range.step ?? 0,0.01)) {
          const requested=softwareZoom;
          hardwareZoom={track,range,current,pending:null,applying:false};
          softwareZoom=1;
          zoomLabel.textContent=text.cameraZoom;
          zoomSlider.max=String(Math.min(4,range.max/range.min));
          zoomSlider.step=String(range.step && range.step>0 ? range.step/range.min : 0.01);
          setZoom(requested);
        }
      }
    } catch { /* Unsupported or rejected camera controls retain explicit image-only zoom. */ }
  }
  async function drainHardwareZoom(state:NonNullable<typeof hardwareZoom>): Promise<void> {
    if (state.applying) return;
    state.applying=true;
    try {
      while (state.pending !== null && state===hardwareZoom && state.current()) {
        const requested=state.pending; state.pending=null;
        const {range}=state;
        const raw=requested*range.min;
        const rounded=range.step && range.step>0 ? range.min+Math.round((raw-range.min)/range.step)*range.step : raw;
        const actual=await applyZoom(state.track,Math.min(range.max,Math.max(range.min,rounded)));
        if (state!==hardwareZoom || !state.current()) return;
        if (actual === undefined) throw new Error('Camera zoom unavailable');
        if (state.pending === null) renderZoom(actual/range.min);
      }
    } catch {
      if (state===hardwareZoom && state.current()) useSoftwareZoom();
    } finally { state.applying=false; }
  }
  function setZoom(value:number): void {
    if (closed || zoomSlider.disabled || !Number.isFinite(value)) return;
    const zoom=Math.min(Number(zoomSlider.max),Math.max(1,value));
    if (hardwareZoom) {
      hardwareZoom.pending=zoom; renderZoom(zoom); void drainHardwareZoom(hardwareZoom);
    } else { softwareZoom=zoom; renderZoom(zoom); }
  }
  zoomSlider.addEventListener('input',() => setZoom(Number(zoomSlider.value)));
  zoomReset.addEventListener('click',() => setZoom(1));
  function pointerDistance(): number {
    const [first,second]=[...pointers.values()];
    return first && second ? Math.hypot(first.x-second.x,first.y-second.y) : 0;
  }
  viewport.addEventListener('pointerdown',event => {
    if (event.pointerType==='mouse') return;
    pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    viewport.setPointerCapture(event.pointerId);
    if (pointers.size===2) pinch={distance:pointerDistance(),zoom:Number(zoomSlider.value)};
  });
  viewport.addEventListener('pointermove',event => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
    if (pinch && pinch.distance>0 && pointers.size===2) {
      event.preventDefault(); setZoom(pinch.zoom*pointerDistance()/pinch.distance);
    }
  });
  function releasePointer(event:PointerEvent): void { pointers.delete(event.pointerId); pinch=null; }
  viewport.addEventListener('pointerup',releasePointer);
  viewport.addEventListener('pointercancel',releasePointer);
  viewport.addEventListener('lostpointercapture',releasePointer);
  const Constructor=(globalThis as typeof globalThis & {BarcodeDetector?:NativeQrConstructor}).BarcodeDetector;
  void chooseNativeQrDetector(Constructor).then(detector => { if (!closed) native=detector; });

  function close(): void {
    if (closed) return;
    closed=true; lifecycle.close();
    resizeObserver.disconnect();
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
      const revision=previewRevision;
      const crop=zoomCrop(video.videoWidth,video.videoHeight,softwareZoom);
      if (!crop || !context) return;
      const scale=Math.min(1,1280/Math.max(crop.width,crop.height));
      canvas.width=Math.max(1,Math.round(crop.width*scale)); canvas.height=Math.max(1,Math.round(crop.height*scale));
      context.drawImage(video,crop.x,crop.y,crop.width,crop.height,0,0,canvas.width,canvas.height);
      let value:string|undefined;
      if (native) {
        try { value=(await decodeWithDeadline(native.detect(canvas)))[0]?.rawValue; }
        catch { native=null; }
        if (!current() || closed || revision!==previewRevision) return;
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
      const constraints:MediaTrackConstraints & {resizeMode:{ideal:string}}={
        facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080},resizeMode:{ideal:'none'},
      };
      return navigator.mediaDevices.getUserMedia({audio:false,video:constraints});
    },
    async (stream,current) => {
      if (!current()) return;
      video.srcObject=stream;
      await video.play();
      if (!current()) return;
      layoutPreview();
      // Optional device controls must never hold up QR decoding on a slow camera driver.
      void configureZoom(stream,current);
      void tick(current);
    },
    () => {
      clearTimeout(timer); hardwareZoom=null; pointers.clear(); pinch=null;
      zoomSlider.disabled=true; zoomReset.disabled=true;
      video.pause(); video.srcObject=null;
    },
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
