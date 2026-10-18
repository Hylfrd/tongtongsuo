import './device-onboarding.css';
import { icon } from './icons';
import { showToast } from './toast';

type Options = {kind:'locks'|'gateways'; sync:()=>Promise<void>; openSettings?:()=>void};
type BrowserBluetooth = {getAvailability?:()=>Promise<boolean>};
let active=false;
let serial=0;

/** Add devices already initialized by TTLock; browser checks never scan or write BLE. */
export async function openDeviceOnboarding(options:Options):Promise<void> {
  if(active)return;
  active=true;
  return new Promise<void>(resolve=>{
    const dialog=document.createElement('dialog');
    const lifetime=new AbortController();
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    const gateway=options.kind==='gateways';
    const titleId=`device-onboarding-${++serial}`;
    let done=false, closing=false, syncing=false, checking=false;
    let closeTimer:ReturnType<typeof setTimeout>|undefined;
    let afterClose:(()=>void)|undefined;
    dialog.className='adm-dialog adm-onboarding-dialog';
    dialog.setAttribute('aria-labelledby',titleId);
    dialog.innerHTML=`<header class="adm-dialog-head"><h2 id="${titleId}">添加${gateway?'网关':'锁'}</h2><button type="button" class="adm-dialog-close" data-close aria-label="关闭"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></header>
      <p class="adm-onboarding-intro">先在通通锁 App 完成首次添加，再同步到工具箱。</p>
      <ol class="adm-onboarding-steps">${gateway?
        '<li>在 G2 网关旁接通电源，通过 App 蓝牙发现网关。</li><li>在 App 中选择 Wi-Fi 并完成配网，与锁使用同一通通锁账号。</li><li>完成后，回到这里同步设备。</li>':
        '<li>在锁旁打开通通锁 App，通过蓝牙添加锁。</li><li>使用后台“其他”中已连接的同一通通锁账号。</li><li>完成后，回到这里同步设备。</li>'}</ol>
      <div class="adm-onboarding-bluetooth"><button type="button" class="adm-outline" data-bluetooth>检查蓝牙</button><p>网页可检查蓝牙支持情况，首次添加仍需通通锁 App。</p></div>
      <div class="adm-onboarding-actions">${options.openSettings?'<button type="button" class="adm-text" data-settings>通通锁账号设置</button>':''}<button type="button" class="adm-button" data-sync>${icon('refresh')}<span>从通通锁账户同步</span></button></div>`;
    const syncButton=dialog.querySelector<HTMLButtonElement>('[data-sync]')!;
    const bluetoothButton=dialog.querySelector<HTMLButtonElement>('[data-bluetooth]')!;
    function update() {
      if(done)return;
      for(const button of dialog.querySelectorAll<HTMLButtonElement>('button'))button.disabled=syncing||closing;
      bluetoothButton.disabled=syncing||checking||closing;
      bluetoothButton.textContent=checking?'正在检查…':'检查蓝牙';
      syncButton.querySelector('span')!.textContent=syncing?'正在同步…':'从通通锁账户同步';
      syncButton.classList.toggle('is-loading',syncing);
      syncButton.setAttribute('aria-busy',String(syncing));
    }
    function finish(restoreFocus=true) {
      if(done)return;
      done=true;
      if(closeTimer)clearTimeout(closeTimer);
      lifetime.abort();
      if(dialog.open)dialog.close();
      dialog.remove();
      active=false;
      if(restoreFocus&&previous?.isConnected)previous.focus({preventScroll:true});
      resolve();
      afterClose?.();
    }
    function close() {
      if(done||closing||syncing)return;
      closing=true;update();
      dialog.classList.add('is-closing');
      const duration=getComputedStyle(dialog).animationDuration.split(',')[0].trim();
      const milliseconds=Number.parseFloat(duration)*(duration.endsWith('ms')?1:1000);
      closeTimer=setTimeout(()=>finish(),Number.isFinite(milliseconds)&&milliseconds>0?milliseconds+60:0);
    }
    dialog.querySelector('[data-close]')!.addEventListener('click',close,{signal:lifetime.signal});
    dialog.querySelector('[data-settings]')?.addEventListener('click',()=>{afterClose=options.openSettings;close();},{signal:lifetime.signal});
    syncButton.addEventListener('click',async()=>{
      if(syncing||done||closing)return;
      syncing=true;update();
      try {
        await options.sync();
        if(!done){syncing=false;close();}
      } catch(error) {
        if(!done)showToast(error instanceof Error?error.message:'同步失败，请检查通通锁账号后重试。','error');
      } finally { syncing=false;update(); }
    },{signal:lifetime.signal});
    bluetoothButton.addEventListener('click',async()=>{
      if(checking||syncing||done)return;
      if(!window.isSecureContext){showToast('蓝牙检查需要 HTTPS，请通过网站正式地址访问。','error');return;}
      const bluetooth=(navigator as Navigator&{bluetooth?:BrowserBluetooth}).bluetooth;
      if(!bluetooth){showToast('当前浏览器不支持网页蓝牙，请使用通通锁 App 添加设备。','info');return;}
      if(!bluetooth.getAvailability){showToast('当前浏览器无法检查蓝牙状态，请在系统设置中确认。','info');return;}
      checking=true;update();
      try {
        const available=await bluetooth.getAvailability();
        if(done||closing)return;
        showToast(available?'浏览器具备蓝牙访问能力；设备首次添加仍需通通锁 App。':'当前未报告可用蓝牙，请检查系统蓝牙和浏览器权限。',available?'success':'info');
      } catch { if(!done&&!closing)showToast('无法读取蓝牙状态，请在系统设置中确认。','error'); }
      finally { checking=false;update(); }
    },{signal:lifetime.signal});
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();},{signal:lifetime.signal});
    dialog.addEventListener('close',()=>finish(),{signal:lifetime.signal});
    dialog.addEventListener('animationend',event=>{if(closing&&event.target===dialog)finish();},{signal:lifetime.signal});
    window.addEventListener('pagehide',()=>{afterClose=undefined;finish(false);},{signal:lifetime.signal});
    window.addEventListener('popstate',()=>{afterClose=undefined;finish(false);},{signal:lifetime.signal});
    document.body.append(dialog);
    try { dialog.showModal();syncButton.focus({preventScroll:true}); } catch { finish(); }
  });
}
