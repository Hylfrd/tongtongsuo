import './lock-editor.css';
import { icon } from './icons';
import { showToast } from './toast';
import { clearFieldError, markFieldError, validateForm } from './validation';
import { confirmDialog } from './confirm-dialog';

type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
export type LockEditorRecord = {id:number; code:string; name:string; cabinet_id:number|null; provider_lock_id:string|null; notes:string};
type Cabinet = {id:number; code:string; name:string};
type Schedule = {weekdays:number[]; all_day:boolean; start_minute:number; end_minute:number};
type Values = {
  auto_lock_seconds:number|null; passage_mode:boolean|null; passage_auto_unlock:boolean|null; passage_schedules:Schedule[]|null;
  sound_enabled:boolean|null; sound_volume:number|null; open_direction:'left'|'right'|null;
  privacy_lock:boolean|null; tamper_alert:boolean|null; reset_button:boolean|null; door_alarm_seconds:number|null;
};
export type LockHardwareSettings = {
  settings:Values; capabilities:Partial<Record<keyof Values|'auto_lock_disable'|'ic_card'|'fingerprint'|'remote_unlock'|'remote_unlock_setting',boolean>>;
  supported?:Partial<Record<keyof Values,boolean>>;
  readonly:{provider_lock_id:string; lock_name?:string; lock_alias?:string; lock_mac?:string; battery?:number|null; model?:string; hardware?:string; firmware?:string; timezone_offset?:number; gateways?:{provider_gateway_id:string; name?:string; cloud_name?:string}[]};
  observed_at:string|null; message?:string;
};
type Callbacks = {onSaved:(record:LockEditorRecord)=>void|Promise<void>; onClose:()=>void; onCloudUpdated?:(settings:LockHardwareSettings)=>void};
const esc = (value:unknown):string => String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));
const same = (a:unknown,b:unknown) => JSON.stringify(a)===JSON.stringify(b);
const errorText = (error:unknown):string => error instanceof TypeError ? '网络连接失败，请稍后重试。' : error instanceof Error && error.name!=='TimeoutError' && error.name!=='AbortError' ? error.message : '设备响应超时，请查询当前配置后再试。';
const time = (minutes:number):string => `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`;
const minutes = (value:string):number => { const parts=value.split(':').map(Number); return parts[0]*60+parts[1]; };
const labels:Record<Exclude<keyof Values,'passage_schedules'>,string> = {auto_lock_seconds:'自动闭锁（秒）',passage_mode:'常开模式',passage_auto_unlock:'时段开始自动开锁',sound_enabled:'提示音',sound_volume:'音量',open_direction:'开门方向',privacy_lock:'反锁功能',tamper_alert:'防撬报警',reset_button:'重置键',door_alarm_seconds:'门未关报警（秒）'};

export function createLockEditor(request:Request,callbacks:Callbacks) {
  const panel=document.createElement('section');
  panel.className='adm-lock-editor';
  panel.setAttribute('aria-labelledby','lock-editor-title');
  const lifetime=new AbortController();
  let record:LockEditorRecord|undefined, hardware:LockHardwareSettings|undefined;
  let disposed=false, loading=false, localSaving=false, cloudSaving=false, editRevision=0;
  const touched=new Set<keyof Values>();
  let localForm:HTMLFormElement, cloudForm:HTMLFormElement;
  const signal=(timeout=120000)=>AbortSignal.any([lifetime.signal,AbortSignal.timeout(timeout)]);
  const field=(name:string)=>panel.querySelector<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>(`[name="${name}"]`)!;
  const canWrite=(key:keyof Values)=>!!hardware?.capabilities[key] && hardware.settings[key]!==null && hardware.settings[key]!==undefined;
  const busy=()=>loading||localSaving||cloudSaving;
  function localValues() {
    return {code:field('code').value.trim(),name:field('name').value.trim(),cabinet_id:field('cabinet_id').value?Number(field('cabinet_id').value):null,notes:field('notes').value.trim()};
  }
  function localDirty():boolean { return !!record && !!localForm && Object.entries(localValues()).some(([key,value])=>value!==record![key as keyof LockEditorRecord]); }
  function schedules():Schedule[] {
    return [...panel.querySelectorAll<HTMLElement>('[data-schedule]')].map(row=>({
      weekdays:[...row.querySelectorAll<HTMLInputElement>('[data-weekday]:checked')].map(input=>Number(input.value)),
      all_day:row.querySelector<HTMLInputElement>('[data-all-day]')!.checked,
      start_minute:row.querySelector<HTMLInputElement>('[data-all-day]')!.checked?0:minutes(row.querySelector<HTMLInputElement>('[data-start]')!.value),
      end_minute:row.querySelector<HTMLInputElement>('[data-all-day]')!.checked?1440:minutes(row.querySelector<HTMLInputElement>('[data-end]')!.value),
    }));
  }
  function changes():Partial<Values> {
    const patch:Record<string,unknown>={};
    if(!hardware)return patch;
    for(const key of Object.keys(labels) as Exclude<keyof Values,'passage_schedules'>[]) {
      if(!touched.has(key)||!canWrite(key))continue;
      const value=field(key).value;
      const parsed=key==='open_direction'?value:['auto_lock_seconds','sound_volume','door_alarm_seconds'].includes(key)?Number(value):value==='true';
      if(!same(parsed,hardware.settings[key]))patch[key]=parsed;
    }
    if(touched.has('passage_schedules')&&canWrite('passage_schedules')) {
      const draft=schedules();
      if(!same(draft,hardware.settings.passage_schedules))patch.passage_schedules=draft;
    }
    return patch;
  }
  function dirty():boolean { return localDirty()||Object.keys(changes()).length>0; }
  function updateBusy() {
    if(disposed)return;
    panel.querySelector<HTMLButtonElement>('[data-query]')!.disabled=busy()||!record?.provider_lock_id;
    panel.querySelector<HTMLButtonElement>('[data-query]')!.classList.toggle('is-loading',loading);
    panel.querySelector<HTMLButtonElement>('[data-collapse]')!.disabled=localSaving||cloudSaving;
    for(const input of localForm.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement>('input:not([readonly]),select,textarea')) input.disabled=localSaving;
    localForm.querySelector<HTMLButtonElement>('[type="submit"]')!.disabled=localSaving||!localDirty();
    localForm.querySelector<HTMLButtonElement>('[type="submit"]')!.textContent=localSaving?'正在保存…':'保存基本信息';
    if(cloudForm) {
      for(const input of cloudForm.querySelectorAll<HTMLInputElement|HTMLSelectElement|HTMLButtonElement>('[data-cloud-field]')) {
        const key=input.dataset.cloudField as keyof Values;
        input.disabled=loading||cloudSaving||!canWrite(key);
      }
      for(const input of cloudForm.querySelectorAll<HTMLInputElement|HTMLButtonElement>('[data-schedules] input,[data-schedules] button')) input.disabled=loading||cloudSaving||!canWrite('passage_schedules');
      for(const row of cloudForm.querySelectorAll<HTMLElement>('[data-schedule]')) {
        const allDay=row.querySelector<HTMLInputElement>('[data-all-day]')!.checked;
        for(const input of row.querySelectorAll<HTMLInputElement>('[data-start],[data-end]')) input.disabled=loading||cloudSaving||!canWrite('passage_schedules')||allDay;
      }
      const add=cloudForm.querySelector<HTMLButtonElement>('[data-add-schedule]');
      if(add)add.disabled=loading||cloudSaving||!canWrite('passage_schedules')||schedules().length>=5;
      const save=cloudForm.querySelector<HTMLButtonElement>('[type="submit"]')!;
      save.disabled=loading||cloudSaving||!Object.keys(changes()).length;
      save.textContent=cloudSaving?'正在应用…':'保存设备配置';
    }
  }
  function control(key:Exclude<keyof Values,'passage_schedules'>):string {
    const known=canWrite(key), value=hardware?.settings[key];
    const attrs=`name="${key}" data-cloud-field="${key}" ${known?'':'disabled'}`;
    let input:string;
    if(key==='auto_lock_seconds'||key==='door_alarm_seconds') {
      const min=key==='door_alarm_seconds'?0:hardware?.capabilities.auto_lock_disable?-1:1;
      input=`<input type="number" ${attrs} value="${known?esc(value):''}" placeholder="—" min="${min}" max="${key==='door_alarm_seconds'?65535:32767}" step="1" required>`;
    } else {
      const options=key==='open_direction'?[['left','左开门'],['right','右开门']]:key==='sound_volume'?[[1,'1'],[2,'2'],[3,'3'],[4,'4'],[5,'5']]:[['true','开启'],['false','关闭']];
      input=`<select ${attrs}>${known?'':'<option value="">不可设置</option>'}${options.map(([option,label])=>`<option value="${option}" ${String(value)===String(option)?'selected':''}>${label}</option>`).join('')}</select>`;
    }
    return `<label class="adm-field ${known?'':'is-unavailable'}">${labels[key]}${input}${key==='auto_lock_seconds'&&known&&hardware?.capabilities.auto_lock_disable?'<small>-1 为关闭自动闭锁</small>':''}${key==='door_alarm_seconds'&&known?'<small>0 为关闭报警</small>':''}${key==='door_alarm_seconds'&&!known&&hardware?.supported?.door_alarm_seconds?'<small>请在 App 中设置</small>':''}</label>`;
  }
  function renderSchedules(items:Schedule[]) {
    const target=panel.querySelector<HTMLElement>('[data-schedules]')!;
    target.innerHTML=items.map((item,index)=>`<div class="adm-lock-schedule" data-schedule="${index}"><div class="adm-lock-weekdays">${['一','二','三','四','五','六','日'].map((day,i)=>`<label><input type="checkbox" data-weekday value="${i+1}" ${item.weekdays.includes(i+1)?'checked':''}><span>${day}</span></label>`).join('')}</div><div class="adm-lock-times"><label class="adm-checkbox"><input type="checkbox" data-all-day ${item.all_day?'checked':''}>全天</label><label class="adm-field"><input type="time" data-start value="${time(item.start_minute)}" required aria-label="开始时间"></label><span>至</span><label class="adm-field"><input type="text" inputmode="numeric" data-end value="${time(item.end_minute)}" pattern="(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00" maxlength="5" required aria-label="结束时间"></label><button type="button" class="adm-text" data-remove-schedule="${index}" aria-label="删除时段 ${index+1}">删除</button></div></div>`).join('') || '<p class="adm-lock-empty">未设置时段</p>';
  }
  function renderReadonly() {
    if(!record||disposed)return;
    const read=hardware?.readonly;
    const gatewayText=read?.gateways?.map(gateway=>`${gateway.cloud_name||gateway.name||'未命名网关'} · ${gateway.provider_gateway_id}`).join('、')||'—';
    panel.querySelector('[data-device-info]')!.innerHTML=[['通通锁 ID',read?.provider_lock_id||record.provider_lock_id],['电量',typeof read?.battery==='number'?`${read.battery}%`:null],['型号',read?.model],['固件',read?.firmware],['硬件版本',read?.hardware],['关联网关',gatewayText]].map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value||'—')}</dd></div>`).join('');
    const capabilities=hardware?Object.entries(hardware.capabilities).filter(([key,value])=>value&&['ic_card','fingerprint','remote_unlock'].includes(key)).map(([key])=>({ic_card:'IC 卡',fingerprint:'指纹',remote_unlock:'远程开锁'}[key])).join('、'):'';
    panel.querySelector('[data-capabilities]')!.textContent=capabilities?`已识别能力：${capabilities}`:'';
    panel.querySelector('[data-updated]')!.textContent=hardware?.observed_at?`更新于 ${new Date(hardware.observed_at).toLocaleString('zh-CN',{hour12:false})}`:'尚未查询';
  }
  function renderHardware() {
    if(!record||disposed)return;
    touched.clear();
    renderReadonly();
    const target=panel.querySelector<HTMLElement>('[data-hardware]')!;
    target.innerHTML=`<form novalidate><div class="adm-lock-config"><section><h3>闭锁与常开</h3><div class="adm-lock-controls">${control('auto_lock_seconds')}${control('passage_mode')}${control('passage_auto_unlock')}</div></section><section><h3>声音与安全</h3><div class="adm-lock-controls">${(['sound_enabled','sound_volume','open_direction','reset_button','privacy_lock','tamper_alert','door_alarm_seconds'] as const).map(control).join('')}</div></section></div><section class="adm-lock-passage"><div class="adm-lock-subheading"><h3>常开时间段</h3><button type="button" class="adm-text" data-add-schedule>添加时段</button></div><div data-schedules></div></section><div class="adm-lock-save"><button type="submit" class="adm-button">保存设备配置</button></div></form>`;
    cloudForm=target.querySelector('form')!;
    renderSchedules(hardware?.settings.passage_schedules||[]);
    updateBusy();
  }
  async function refresh({automatic=false}:{automatic?:boolean}={}):Promise<void> {
    if(disposed||!record||!record.provider_lock_id||busy())return;
    if(automatic&&dirty())return;
    const preserveDraft=Object.keys(changes()).length>0;
    loading=true;updateBusy();
    const revision=editRevision;
    try {
      const result=await request<LockHardwareSettings>(`/api/admin/ttlock/locks/${record.id}/settings`,{signal:signal()});
      if(disposed)return;
      const drafts=new Map([...touched].filter(key=>key!=='passage_schedules').map(key=>[key,field(key).value]));
      const scheduleDraft=touched.has('passage_schedules')?schedules():null;
      hardware=result;
      renderHardware();
      if(preserveDraft||revision!==editRevision) {
        for(const [key,value] of drafts)if(canWrite(key)){field(key).value=value;touched.add(key);}
        if(scheduleDraft&&canWrite('passage_schedules')){renderSchedules(scheduleDraft);touched.add('passage_schedules');}
      }
      callbacks.onCloudUpdated?.(result);
      if(!automatic)showToast(preserveDraft?'已查询，未保存的修改已保留。':'配置查询完成。','success');
    } catch(error) {
      if(!disposed) {
        if(!hardware)panel.querySelector('[data-updated]')!.textContent='查询未完成';
        if(!automatic||!hardware)showToast(errorText(error),'error');
      }
    }
    finally { loading=false;updateBusy(); }
  }
  async function saveLocal() {
    if(disposed||localSaving||!record||!validateForm(localForm))return;
    const values=localValues();
    if(!values.code||!values.name) { markFieldError(field(!values.code?'code':'name'));showToast('请填写内部编号和锁名称。','error');return; }
    const patch=Object.fromEntries(Object.entries(values).filter(([key,value])=>value!==record![key as keyof LockEditorRecord]));
    if(!Object.keys(patch).length)return;
    localSaving=true;updateBusy();
    try {
      const result=await request<LockEditorRecord>(`/api/admin/locks/${record.id}`,{method:'PATCH',body:JSON.stringify(patch),signal:signal(60000)});
      if(disposed)return;
      record={...record,...result};
      panel.querySelector('#lock-editor-title')!.textContent=record.name;
      await callbacks.onSaved(record);
      showToast('锁的基本信息已保存。','success');
    } catch(error) { if(!disposed)showToast(errorText(error),'error'); }
    finally { localSaving=false;updateBusy(); }
  }
  async function saveCloud() {
    if(disposed||busy()||!record||!hardware||!validateForm(cloudForm))return;
    const patch=changes();
    if(!Object.keys(patch).length)return;
    if(patch.auto_lock_seconds===0) { markFieldError(field('auto_lock_seconds'));showToast('自动闭锁时间不能为 0。','error');return; }
    const draft=patch.passage_schedules??hardware.settings.passage_schedules;
    const enabled=patch.passage_mode??hardware.settings.passage_mode;
    if(draft?.some(row=>!row.weekdays.length||!Number.isFinite(row.start_minute)||!Number.isFinite(row.end_minute)||row.start_minute>=row.end_minute)) { showToast('请为每个时段选择星期，并确保结束时间晚于开始时间。','error');return; }
    if(enabled&&(!draft||!draft.length)) { showToast('开启常开模式前，请添加至少一个时段。','error');return; }
    cloudSaving=true;updateBusy();
    try {
      const result=await request<LockHardwareSettings>(`/api/admin/ttlock/locks/${record.id}/settings`,{method:'PATCH',body:JSON.stringify(patch),signal:signal(600000)});
      if(disposed)return;
      hardware=result;renderHardware();callbacks.onCloudUpdated?.(result);
      showToast(result.message||'设备配置已保存并重新读取。','success');
    } catch(error) { if(!disposed)showToast(errorText(error),'error'); }
    finally { cloudSaving=false;updateBusy(); }
  }
  function build(cabinets:Cabinet[]) {
    if(!record)return;
    panel.innerHTML=`<header class="adm-lock-editor-heading"><div><h2 id="lock-editor-title" tabindex="-1">${esc(record.name)}</h2><span data-updated>尚未查询</span></div><div class="adm-lock-editor-actions"><button type="button" class="adm-refresh-button" data-query>${icon('refresh')}<span>查询配置</span></button><button type="button" class="adm-outline" data-collapse>收起</button></div></header><div class="adm-lock-basics"><form novalidate data-local-form><h3>基本信息</h3><div class="adm-form-grid"><label class="adm-field">内部编号<input name="code" value="${esc(record.code)}" required maxlength="64"></label><label class="adm-field">锁名称<input name="name" value="${esc(record.name)}" required maxlength="120"></label><label class="adm-field">所在柜子<select name="cabinet_id"><option value="">暂不分配</option>${cabinets.map(c=>`<option value="${c.id}" ${record!.cabinet_id===c.id?'selected':''}>${esc(c.name===c.code||c.name===`${c.code}区`?c.name:`${c.code} · ${c.name}`)}</option>`).join('')}</select></label><label class="adm-field">备注<textarea name="notes" rows="2" maxlength="2000">${esc(record.notes)}</textarea></label></div><div class="adm-lock-save"><button type="submit" class="adm-outline">保存基本信息</button></div></form><section class="adm-lock-info"><h3>设备信息</h3><dl data-device-info></dl><p data-capabilities></p></section></div><div data-hardware></div>`;
    localForm=panel.querySelector<HTMLFormElement>('[data-local-form]')!;
    localForm.addEventListener('submit',event=>{event.preventDefault();void saveLocal();},{signal:lifetime.signal});
    panel.querySelector('[data-query]')!.addEventListener('click',()=>void refresh(),{signal:lifetime.signal});
    panel.querySelector('[data-collapse]')!.addEventListener('click',async()=>{
      if(disposed||localSaving||cloudSaving)return;
      if(dirty()&&!await confirmDialog({title:'放弃未保存的修改？',message:'收起后，本次尚未保存的锁配置将被丢弃。',confirmLabel:'放弃修改',cancelLabel:'继续编辑',danger:true}))return;
      if(!disposed&&!localSaving&&!cloudSaving)callbacks.onClose();
    },{signal:lifetime.signal});
    renderHardware();
  }
  function onEdit(event:Event) {
    if(event.target instanceof HTMLInputElement||event.target instanceof HTMLTextAreaElement||event.target instanceof HTMLSelectElement)clearFieldError(event.target);
    if(cloudForm?.contains(event.target as Node)) {
      const input=event.target as HTMLElement;
      const key=input.closest('[data-schedule]')?'passage_schedules':input.dataset.cloudField as keyof Values|undefined;
      if(key)touched.add(key);
      editRevision++;
    }
    updateBusy();
  }
  panel.addEventListener('input',onEdit,{signal:lifetime.signal});
  panel.addEventListener('change',onEdit,{signal:lifetime.signal});
  panel.addEventListener('submit',event=>{if(event.target===cloudForm){event.preventDefault();void saveCloud();}},{signal:lifetime.signal});
  panel.addEventListener('click',event=>{
    const button=(event.target as Element).closest<HTMLButtonElement>('[data-add-schedule],[data-remove-schedule]');
    if(!button||button.disabled||!canWrite('passage_schedules'))return;
    const draft=schedules();
    if(button.hasAttribute('data-add-schedule')) { if(draft.length>=5)return;draft.push({weekdays:[1,2,3,4,5],all_day:false,start_minute:540,end_minute:1020}); }
    else draft.splice(Number(button.dataset.removeSchedule),1);
    touched.add('passage_schedules');editRevision++;renderSchedules(draft);updateBusy();
  },{signal:lifetime.signal});
  window.addEventListener('beforeunload',event=>{if(dirty()||localSaving||cloudSaving){event.preventDefault();event.returnValue='';}},{signal:lifetime.signal});
  return {
    mount(target:Element,next:LockEditorRecord,cabinets:Cabinet[]) {
      if(disposed)return;
      if(!record||record.id!==next.id) { record={...next};hardware=undefined;build(cabinets); }
      target.append(panel);
      if(!hardware&&!loading)void refresh({automatic:true});
    },
    refresh,
    isDirty:dirty,
    isBusy:busy,
    dispose() { disposed=true;lifetime.abort();panel.remove(); },
  };
}
