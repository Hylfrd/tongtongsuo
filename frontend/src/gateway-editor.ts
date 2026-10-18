import './gateway-editor.css';
import { icon } from './icons';
import { showToast } from './toast';
import { clearFieldError, markFieldError, validateForm } from './validation';
import { confirmDialog } from './confirm-dialog';

type Request = <T>(url:string, init?:RequestInit) => Promise<T>;
export type GatewayEditorRecord = {id:number; code:string; name:string; provider_gateway_id:string|null; notes:string};
export type GatewayHardwareSettings = {
  settings:{cloud_name:string|null};
  capabilities:{cloud_name:boolean; cloud_initialize?:boolean; wifi_setup?:boolean; time_sync?:boolean; timezone?:boolean};
  readonly:{
    provider_gateway_id:string; name?:string|null; gateway_mac?:string|null; version?:number|null; model?:string|null;
    network_name?:string|null; network_mac?:string|null; lock_count?:number|null; status:'online'|'offline'|'unknown';
    locks:{provider_lock_id:string; name?:string|null; cloud_name?:string|null; lock_mac?:string|null; rssi:number|null; updated_at:string|null}[];
  };
  observed_at:string|null; message?:string;
};
type Callbacks = {onSaved:(record:GatewayEditorRecord)=>void|Promise<void>; onClose:()=>void; onCloudUpdated?:(settings:GatewayHardwareSettings)=>void};
const esc = (value:unknown):string => String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));
const formatDate = (value:string|null|undefined):string => value&&Number.isFinite(new Date(value).getTime())?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—';
const errorText = (error:unknown):string => error instanceof TypeError?'网络连接失败，请稍后重试。':error instanceof Error&&!['TimeoutError','AbortError'].includes(error.name)?error.message:'设备响应超时，请查询当前配置后再试。';

export function createGatewayEditor(request:Request,callbacks:Callbacks) {
  const panel=document.createElement('section');
  panel.className='adm-gateway-editor';
  panel.setAttribute('aria-labelledby','gateway-editor-title');
  const lifetime=new AbortController();
  let recordLifetime=new AbortController();
  let record:GatewayEditorRecord|undefined,hardware:GatewayHardwareSettings|undefined;
  let disposed=false,loading=false,localSaving=false,cloudSaving=false,cloudTouched=false;
  let generation=0;
  let localForm:HTMLFormElement,cloudForm:HTMLFormElement;
  const field=(name:string)=>panel.querySelector<HTMLInputElement|HTMLTextAreaElement>(`[name="${name}"]`)!;
  const signal=(timeout=120000)=>AbortSignal.any([lifetime.signal,recordLifetime.signal,AbortSignal.timeout(timeout)]);
  const busy=()=>loading||localSaving||cloudSaving;
  const canRename=()=>!!hardware?.capabilities.cloud_name;
  function localValues() { return {code:field('code').value.trim(),name:field('name').value.trim(),notes:field('notes').value.trim()}; }
  function localDirty():boolean { return !!record&&!!localForm&&Object.entries(localValues()).some(([key,value])=>value!==record![key as keyof GatewayEditorRecord]); }
  function cloudDirty():boolean { return !!hardware&&!!cloudForm&&cloudTouched&&field('cloud_name').value.trim()!==(hardware.settings.cloud_name??''); }
  function dirty():boolean { return localDirty()||cloudDirty(); }
  function updateBusy() {
    if(disposed||!record)return;
    const query=panel.querySelector<HTMLButtonElement>('[data-query]')!;
    query.disabled=busy()||!record.provider_gateway_id;
    query.classList.toggle('is-loading',loading);
    query.setAttribute('aria-busy',String(loading));
    panel.querySelector<HTMLButtonElement>('[data-collapse]')!.disabled=localSaving||cloudSaving;
    for(const input of localForm.querySelectorAll<HTMLInputElement|HTMLTextAreaElement>('input,textarea'))input.disabled=localSaving;
    const localSave=localForm.querySelector<HTMLButtonElement>('[type="submit"]')!;
    localSave.disabled=busy()||!localDirty();
    localSave.textContent=localSaving?'正在保存…':'保存基本信息';
    field('cloud_name').disabled=loading||cloudSaving||!canRename();
    const cloudSave=cloudForm.querySelector<HTMLButtonElement>('[type="submit"]')!;
    cloudSave.disabled=busy()||!canRename()||!cloudDirty();
    cloudSave.textContent=cloudSaving?'正在应用…':'保存平台名称';
  }
  function renderReadonly() {
    if(!record||disposed)return;
    const read=hardware?.readonly;
    panel.querySelector('[data-device-info]')!.innerHTML=[
      ['通通锁 ID',read?.provider_gateway_id||record.provider_gateway_id],['网关类型',read?.model],
      ['网关 MAC',read?.gateway_mac],['在线状态',read?{online:'在线',offline:'离线',unknown:'未知'}[read.status]:'—'],
      ['网络名称（SSID）',read?.network_name],['网络 MAC',read?.network_mac],
      ['配对锁数量',typeof read?.lock_count==='number'?String(read.lock_count):null],
    ].map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value||'—')}</dd></div>`).join('');
    const locks=read?.locks;
    panel.querySelector('[data-paired-locks]')!.innerHTML=!hardware?'<p class="adm-gateway-empty">查询后显示配对锁</p>':locks?.length?`<div class="adm-table-scroll" tabindex="0" role="region" aria-label="网关配对锁列表"><table class="adm-table"><thead><tr><th>锁 / 通通锁 ID</th><th>锁 MAC</th><th>信号（RSSI）</th><th>信号更新时间</th></tr></thead><tbody>${locks.map(lock=>`<tr><td><strong>${esc(lock.name||lock.cloud_name||'未命名锁')}</strong><small>${esc(lock.provider_lock_id)}</small></td><td>${esc(lock.lock_mac||'—')}</td><td>${typeof lock.rssi==='number'?`${lock.rssi} dBm`:'—'}</td><td>${esc(formatDate(lock.updated_at))}</td></tr>`).join('')}</tbody></table></div>`:'<p class="adm-gateway-empty">尚未发现配对锁</p>';
    panel.querySelector('[data-updated]')!.textContent=hardware?.observed_at?`更新于 ${formatDate(hardware.observed_at)}`:record.provider_gateway_id?'尚未查询':'尚未关联通通锁网关';
  }
  async function refresh({automatic=false}:{automatic?:boolean}={}):Promise<void> {
    if(disposed||!record?.provider_gateway_id||busy()||automatic&&dirty())return;
    const version=generation,preserveDraft=cloudDirty(),draft=field('cloud_name').value;
    loading=true;updateBusy();
    try {
      const result=await request<GatewayHardwareSettings>(`/api/admin/ttlock/gateways/${record.id}/settings`,{signal:signal()});
      if(disposed||version!==generation)return;
      hardware=result;
      field('cloud_name').value=preserveDraft?draft:result.settings.cloud_name??'';
      cloudTouched=preserveDraft;
      renderReadonly();callbacks.onCloudUpdated?.(result);
      if(!automatic)showToast(preserveDraft?'已查询，未保存的修改已保留。':'配置查询完成。','success');
    } catch(error) {
      if(!disposed&&version===generation) {
        if(!hardware)panel.querySelector('[data-updated]')!.textContent='查询未完成';
        if(!automatic||!hardware)showToast(errorText(error),'error');
      }
    } finally { if(!disposed&&version===generation){loading=false;updateBusy();} }
  }
  async function saveLocal() {
    if(disposed||busy()||!record||!validateForm(localForm))return;
    const values=localValues();
    if(!values.code||!values.name) { markFieldError(field(!values.code?'code':'name'));showToast('请填写内部编号和网关名称。','error');return; }
    const patch=Object.fromEntries(Object.entries(values).filter(([key,value])=>value!==record![key as keyof GatewayEditorRecord]));
    if(!Object.keys(patch).length)return;
    const version=generation;
    localSaving=true;updateBusy();
    try {
      const result=await request<GatewayEditorRecord>(`/api/admin/gateways/${record.id}`,{method:'PATCH',body:JSON.stringify(patch),signal:signal(60000)});
      if(disposed||version!==generation)return;
      record={...record,...result};
      panel.querySelector('#gateway-editor-title')!.textContent=record.name;
      await callbacks.onSaved(record);
      if(!disposed&&version===generation)showToast('网关的基本信息已保存。','success');
    } catch(error) { if(!disposed&&version===generation)showToast(errorText(error),'error'); }
    finally { if(!disposed&&version===generation){localSaving=false;updateBusy();} }
  }
  async function saveCloud() {
    if(disposed||busy()||!record||!hardware||!canRename()||!cloudDirty()||!validateForm(cloudForm))return;
    const name=field('cloud_name').value.trim();
    if(!name||/[\x00-\x1f\x7f]/.test(name)){markFieldError(field('cloud_name'));showToast('请填写有效的平台名称。','error');return;}
    const version=generation;
    cloudSaving=true;updateBusy();
    try {
      const result=await request<GatewayHardwareSettings>(`/api/admin/ttlock/gateways/${record.id}/settings`,{method:'PATCH',body:JSON.stringify({cloud_name:name}),signal:signal()});
      if(disposed||version!==generation)return;
      hardware=result;cloudTouched=false;
      field('cloud_name').value=result.settings.cloud_name??'';
      renderReadonly();callbacks.onCloudUpdated?.(result);
      showToast(result.message||'网关名称已更新。','success');
    } catch(error) { if(!disposed&&version===generation)showToast(errorText(error),'error'); }
    finally { if(!disposed&&version===generation){cloudSaving=false;updateBusy();} }
  }
  function build() {
    if(!record)return;
    panel.innerHTML=`<header class="adm-gateway-editor-heading"><div><h2 id="gateway-editor-title" tabindex="-1">${esc(record.name)}</h2><span data-updated>尚未查询</span></div><div class="adm-gateway-editor-actions"><button type="button" class="adm-refresh-button" data-query>${icon('refresh')}<span>查询配置</span></button><button type="button" class="adm-outline" data-collapse>收起</button></div></header><div class="adm-gateway-basics"><div><form novalidate data-local-form><h3>基本信息</h3><div class="adm-form-grid"><label class="adm-field">内部编号<input name="code" value="${esc(record.code)}" required maxlength="64"></label><label class="adm-field">网关名称<input name="name" value="${esc(record.name)}" required maxlength="120"></label><label class="adm-field adm-wide">备注<textarea name="notes" rows="2" maxlength="2000">${esc(record.notes)}</textarea></label></div><div class="adm-gateway-save"><button type="submit" class="adm-outline">保存基本信息</button></div></form><form novalidate class="adm-gateway-cloud" data-cloud-form><h3>通通锁配置</h3><label class="adm-field">平台名称<input name="cloud_name" required maxlength="120" disabled></label><p>修改后同步到通通锁 App，本地名称独立保留。</p><div class="adm-gateway-save"><button type="submit" class="adm-button" disabled>保存平台名称</button></div></form></div><section class="adm-gateway-info"><h3>设备与网络</h3><dl data-device-info></dl><p>Wi-Fi 配网请在通通锁 App 中完成。</p></section></div><section class="adm-gateway-locks"><h3>配对锁</h3><div data-paired-locks></div></section>`;
    localForm=panel.querySelector<HTMLFormElement>('[data-local-form]')!;
    cloudForm=panel.querySelector<HTMLFormElement>('[data-cloud-form]')!;
    renderReadonly();updateBusy();
  }
  panel.addEventListener('submit',event=>{
    event.preventDefault();
    if(event.target===localForm)void saveLocal();
    else if(event.target===cloudForm)void saveCloud();
  },{signal:lifetime.signal});
  function onEdit(event:Event) {
    if(event.target instanceof HTMLInputElement||event.target instanceof HTMLTextAreaElement) {
      clearFieldError(event.target);
      if(event.target.name==='cloud_name')cloudTouched=true;
      updateBusy();
    }
  }
  panel.addEventListener('input',onEdit,{signal:lifetime.signal});
  panel.addEventListener('change',onEdit,{signal:lifetime.signal});
  panel.addEventListener('click',async event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-query],[data-collapse]');
    if(!button||button.disabled)return;
    if(button.hasAttribute('data-query')){void refresh();return;}
    if(disposed||localSaving||cloudSaving)return;
    if(dirty()&&!await confirmDialog({title:'放弃未保存的修改？',message:'收起后，本次尚未保存的网关配置将被丢弃。',confirmLabel:'放弃修改',cancelLabel:'继续编辑',danger:true}))return;
    if(!disposed&&!localSaving&&!cloudSaving)callbacks.onClose();
  },{signal:lifetime.signal});
  window.addEventListener('beforeunload',event=>{if(dirty()||localSaving||cloudSaving){event.preventDefault();event.returnValue='';}},{signal:lifetime.signal});
  return {
    mount(target:Element,next:GatewayEditorRecord) {
      if(disposed)return;
      if(!record||record.id!==next.id) {
        recordLifetime.abort();recordLifetime=new AbortController();generation++;
        record={...next};hardware=undefined;loading=false;localSaving=false;cloudSaving=false;cloudTouched=false;
        build();
      }
      target.append(panel);
      if(!hardware&&!loading)void refresh({automatic:true});
    },
    refresh,
    isDirty:dirty,
    isBusy:busy,
    dispose() {disposed=true;lifetime.abort();recordLifetime.abort();panel.remove();},
  };
}
