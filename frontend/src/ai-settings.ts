import './ai-settings.css';
import { showToast } from './toast';
import { clearFieldError, markFieldError, validateForm } from './validation';

type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
type Settings = {
  provider: string; base_url: string; api_key_configured: boolean;
  image_model: string; image_quality: string; image_background: string;
  timeout_seconds: number; prompt_template: string;
  rewrite_enabled: boolean; rewrite_model: string; rewrite_effort: string;
  review_enabled: boolean; review_model: string; review_effort: string;
};
type Job = {id: string; status: string; stage?: string; icon_data?: string; error?: string; review?: {approved: boolean; reason: string}; result?: {preview_expired?: boolean}};
const models = ['gpt-5.6-luna', 'gpt-6-astra', 'gpt-6-luna', 'gpt-6.1-sol'];
const efforts = ['low', 'mid', 'high', 'xhigh', 'max', 'ultra'];
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
const errorText = (error: unknown) => error instanceof Error ? error.message : '请求失败，请稍后再试。';
const validImage = (value: unknown): value is string => typeof value === 'string' && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value) && value.length < 710000;
function select(name: string, label: string, values: string[], selected: string): string {
  return `<label class="adm-field">${label}<select name="${name}">${values.map(value=>`<option value="${escape(value)}" ${value===selected?'selected':''}>${escape(value)}</option>`).join('')}</select></label>`;
}

/** Keep one draft mounted so navigation and in-flight saves never discard edits. */
export function createAISettings(request: Request) {
  const panel = document.createElement('section');
  panel.className = 'adm-ai-settings';
  panel.setAttribute('aria-label','图标生成设置');
  let loaded = false, loading = false, dirty = false, disposed = false;
  let revision = 0;
  let settings: Settings | undefined;
  let disposeTest: (()=>void) | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<boolean> | undefined;
  let flush: ()=>Promise<boolean> = async()=>false;
  const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty || inFlight) { event.preventDefault(); event.returnValue = ''; } };
  window.addEventListener('beforeunload',beforeUnload);

  function render() {
    if (!settings || disposed) return;
    const s = settings;
    panel.innerHTML = `<form novalidate class="adm-ai-form"><div class="adm-ai-heading"><h2>图标生成</h2></div>
      <div class="adm-form-grid">
        <label class="adm-field">模型提供商<select name="provider"><option value="sub2api" ${s.provider==='sub2api'?'selected':''}>Sub2API</option><option value="custom" ${s.provider==='custom'?'selected':''}>自定义兼容接口</option></select></label>
        <label class="adm-field">API 地址<input name="base_url" type="url" value="${escape(s.base_url)}" required maxlength="500" autocomplete="off" spellcheck="false"></label>
        <label class="adm-field adm-wide">API Key<input name="api_key" type="password" placeholder="${s.api_key_configured?'************':'暂未配置'}" autocomplete="new-password" maxlength="4096" spellcheck="false"></label>
        <label class="adm-field adm-wide">生图模型<input name="image_model" value="${escape(s.image_model)}" required maxlength="100" autocomplete="off" spellcheck="false"></label>
        ${select('image_quality','图片质量',['low','medium','high','xhigh','max','auto'],s.image_quality)}
        ${select('image_background','图片背景',['transparent','opaque','auto'],s.image_background)}
      </div>
      <div class="adm-ai-process-grid">${(['rewrite','review'] as const).map(kind=>`<fieldset class="adm-ai-process"><legend><label class="adm-checkbox"><input type="checkbox" name="${kind}_enabled" ${s[`${kind}_enabled`]?'checked':''}>${kind==='rewrite'?'重写提示词':'图片审查'}</label></legend><div class="adm-form-grid">${select(`${kind}_model`,'模型',models,s[`${kind}_model`])}${select(`${kind}_effort`,'思考强度',efforts,s[`${kind}_effort`])}</div></fieldset>`).join('')}</div>
      <details class="adm-ai-advanced"><summary>高级设置</summary><div class="adm-form-grid"><label class="adm-field">请求超时（秒）<input name="timeout_seconds" type="number" min="30" max="900" step="1" value="${s.timeout_seconds}" required></label><label class="adm-field adm-wide">预设提示词<textarea name="prompt_template" rows="8" required minlength="20" maxlength="8000">${escape(s.prompt_template)}</textarea><small>{name} 为工具名称（必需），{description} 为描述。</small></label></div><div class="adm-ai-test"><h3>开发测试</h3><p>发送一次真实生图请求，会消耗提供商额度。</p><label class="adm-field">测试工具名称<input name="test_name" value="螺丝刀" maxlength="120" autocomplete="off"></label><div data-ai-test></div></div></details></form>`;
    const form = panel.querySelector<HTMLFormElement>('form')!;
    const field = (name: string) => form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
    const connectionFields = new Set(['provider','base_url','api_key']);
    const textNames = ['provider','base_url','image_model','image_quality','image_background','prompt_template','rewrite_model','rewrite_effort','review_model','review_effort'];
    const stopTimer = () => { if(timer)clearTimeout(timer);timer=undefined; };
    const editingKey = () => document.activeElement===field('api_key') && field('api_key').value.length>0;
    const changedConnection = () => settings!.api_key_configured && (field('provider').value!==settings!.provider || field('base_url').value.trim().replace(/\/$/,'')!==settings!.base_url.replace(/\/$/,''));
    function update() {
      for (const kind of ['rewrite','review']) {
        const enabled = (field(`${kind}_enabled`) as HTMLInputElement).checked;
        field(`${kind}_model`).disabled = !enabled;
        field(`${kind}_effort`).disabled = !enabled;
      }
    }
    function payload(): Record<string,unknown> {
      const body: Record<string,unknown> = {};
      for(const name of textNames)body[name]=field(name).value.trim();
      body.timeout_seconds=Number(field('timeout_seconds').value);
      body.rewrite_enabled=(field('rewrite_enabled') as HTMLInputElement).checked;
      body.review_enabled=(field('review_enabled') as HTMLInputElement).checked;
      const key=field('api_key').value.trim();
      if(key)body.api_key=key;
      return body;
    }
    function schedule(delay=800) {
      stopTimer();
      if(disposed || editingKey())return;
      timer=setTimeout(()=>{timer=undefined;void save(false);},delay);
    }
    async function save(explicit: boolean): Promise<boolean> {
      if(disposed)return false;
      if(inFlight)return inFlight;
      if(!dirty)return true;
      if(editingKey() && !explicit)return false;
      const key=field('api_key').value.trim();
      if(changedConnection() && !key) {
        if(explicit){markFieldError(field('api_key'));showToast('更换接口后，请重新填写 API Key。','error');}
        return false;
      }
      const invalid=[...form.elements].filter((el): el is HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement =>
        (el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) && el.name!=='test_name' && el.willValidate && !el.validity.valid);
      if(invalid.length){
        invalid.forEach(markFieldError);
        if(explicit){if(panel.querySelector('details :invalid'))panel.querySelector('details')!.open=true;validateForm(form);}
        else showToast('请检查标红的设置项。','error');
        return false;
      }
      if(key && !/^[\x21-\x7e]+$/.test(key)){markFieldError(field('api_key'));showToast('API Key 不能包含空白或非 ASCII 字符。','error');return false;}
      if(!field('prompt_template').value.includes('{name}')){markFieldError(field('prompt_template'));panel.querySelector('details')!.open=true;showToast('预设提示词需要包含 {name}。','error');return false;}
      const body=payload();
      if(!key && Object.entries(body).every(([name,value])=>settings![name as keyof Settings]===value)){dirty=false;return true;}
      const savedRevision=revision, keyDraft=field('api_key').value;
      inFlight=(async()=>{
        try {
          const saved=await request<Settings>('/api/admin/ai/settings',{method:'PUT',body:JSON.stringify(body)});
          if(disposed)return false;
          settings=saved;
          (field('api_key') as HTMLInputElement).placeholder=saved.api_key_configured?'************':'暂未配置';
          if(key && field('api_key').value===keyDraft)field('api_key').value='';
          dirty=revision!==savedRevision;
          showToast('图标生成设置已保存。','success');
          return true;
        } catch(error){if(!disposed)showToast(errorText(error),'error');return false;}
        finally {
          inFlight=undefined;
          // A failed request is retried only after another edit or explicit test click.
          if(!disposed && revision!==savedRevision && !timer)schedule();
        }
      })();
      return inFlight;
    }
    flush=async()=>{
      stopTimer();
      if(inFlight && !await inFlight)return false;
      stopTimer();
      while(dirty && !disposed){if(!await save(true))return false;stopTimer();}
      return !disposed;
    };
    const changed = (event: Event) => {
      const el=event.target;
      if(!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement) || el.name==='test_name')return;
      if(event.type==='input' && (el instanceof HTMLSelectElement || el instanceof HTMLInputElement && el.type==='checkbox'))return;
      dirty=true;revision++;clearFieldError(el);update();
      if(el.name==='api_key') {stopTimer();if(document.activeElement!==el)schedule(0);return;}
      schedule(event.type==='change'?0:800);
    };
    form.addEventListener('input',changed);
    form.addEventListener('change',changed);
    form.addEventListener('focusout',event=>{
      const el=event.target;
      if(!(el instanceof HTMLInputElement || el instanceof HTMLSelectElement || el instanceof HTMLTextAreaElement))return;
      if(el.name==='api_key' && dirty)schedule(0);
      const next=event.relatedTarget;
      if(connectionFields.has(el.name) && !(next instanceof HTMLElement && connectionFields.has(next.getAttribute('name')??'')) && changedConnection() && !field('api_key').value.trim()) {
        markFieldError(field('api_key'));showToast('更换接口后，请重新填写 API Key。','error');
      }
    });
    form.addEventListener('submit',event=>{event.preventDefault();void flush();});
    disposeTest = attachIconGenerator(panel.querySelector('[data-ai-test]')!,request,{
      endpoint:'/api/admin/ai/test',label:'生成测试图',
      details:()=>({name:field('test_name').value.trim(),description:''}),
      canStart:()=>flush(),
    }).dispose;
    update();
  }
  async function load() {
    if(loading || inFlight || disposed)return;
    loading=true;panel.setAttribute('aria-busy','true');
    if(!loaded)panel.innerHTML='<p class="adm-ai-help">正在读取图标生成设置…</p>';
    try {const result=await request<Settings>('/api/admin/ai/settings');if(disposed)return;settings=result;loaded=true;dirty=false;disposeTest?.();render();}
    catch(error){if(disposed)return;showToast(errorText(error),'error');if(!loaded){panel.innerHTML='<button class="adm-outline" type="button">重新读取图标设置</button>';panel.querySelector('button')!.addEventListener('click',()=>void load());}}
    finally{loading=false;panel.removeAttribute('aria-busy');}
  }
  return {
    mount(target: Element) {target.append(panel);if(!loaded)void load();},
    dispose() {disposed=true;if(timer)clearTimeout(timer);disposeTest?.();panel.querySelector<HTMLFormElement>('form')?.reset();panel.replaceChildren();window.removeEventListener('beforeunload',beforeUnload);},
  };
}

export function attachIconGenerator(target: Element, request: Request, options: {
  endpoint?: string; label?: string; details: ()=>{name:string;description:string;tool_id?:number};
  apply?: (image:string)=>void; openSettings?:()=>void; canStart?:()=>boolean | Promise<boolean>;
}) {
  target.classList.add('adm-ai-generator');
  target.innerHTML=`<div class="adm-ai-generator-actions"><button type="button" class="adm-outline" data-generate>${options.label??'生成 icon'}</button>${options.openSettings?'<button type="button" class="adm-text" data-settings>生图设置</button>':''}</div><p class="adm-ai-job-state" role="status" aria-live="polite"></p><div class="adm-ai-result" hidden><img alt="生成的工具图标"><div><p data-review></p>${options.apply?'<button type="button" class="adm-outline" data-apply>使用此图标</button>':''}</div></div>`;
  const button=target.querySelector<HTMLButtonElement>('[data-generate]')!;
  const status=target.querySelector<HTMLElement>('[role="status"]')!;
  const result=target.querySelector<HTMLElement>('.adm-ai-result')!;
  const apply=target.querySelector<HTMLButtonElement>('[data-apply]');
  let disposed=false,busy=false,unknown=false,jobId='',requestId='',candidate='', timer:ReturnType<typeof setTimeout>|undefined;
  let submitted:Record<string,unknown>|undefined;
  const stages:Record<string,string>={preparing:'正在准备…',rewriting:'正在优化提示词…',generating:'正在生成图标…',reviewing:'正在审查图片…',processing:'正在处理图标…'};
  function failed(error:unknown){if(disposed)return;busy=false;unknown=true;button.disabled=false;button.textContent='检查生成任务';status.textContent='连接中断，任务可能仍在运行；检查时会沿用同一次请求。';showToast(errorText(error),'error');}
  function receive(job:Job){
    if(disposed)return;jobId=job.id;
    const done=['succeeded','failed','rejected'].includes(job.status);
    if(!done){status.textContent=stages[job.stage??'']??'等待生成…';timer=setTimeout(()=>void poll(),1500);return;}
    busy=false;unknown=false;button.disabled=false;button.textContent='重新生成';
    if(job.result?.preview_expired){candidate='';result.hidden=true;if(apply)apply.hidden=true;status.textContent='预览已过期，请重新生成';return;}
    const accepted=job.status==='succeeded' && job.review?.approved!==false;
    candidate=validImage(job.icon_data)?job.icon_data:'';
    status.textContent=accepted?'图标已生成。':job.status==='rejected'?'图片未通过审查。':'图标生成失败。';
    if(candidate){result.hidden=false;result.querySelector('img')!.src=candidate;result.querySelector('[data-review]')!.textContent=job.review?.reason || (options.apply?'预览满意后使用，保存工具时生效。':'测试完成，图片不会关联到工具。');}
    if(apply){apply.hidden=!accepted || !candidate;apply.disabled=false;apply.textContent='使用此图标';}
    if(!accepted)showToast(job.error || job.review?.reason || '图片生成未成功，请检查提供商配置。','error');
  }
  async function poll(){try{receive(await request<Job>(`/api/admin/ai/icon-jobs/${encodeURIComponent(jobId)}`));}catch(error){failed(error);}}
  button.addEventListener('click',async()=>{
    if(busy || disposed)return;
    busy=true;button.disabled=true;
    try {if(await options.canStart?.()===false){busy=false;button.disabled=false;return;}}
    catch(error){busy=false;button.disabled=false;showToast(errorText(error),'error');return;}
    if(disposed)return;
    if(!unknown){
      const details=options.details();if(!details.name){busy=false;button.disabled=false;showToast('请先填写工具名称。','error');return;}
      // Verify configuration before the first paid request; never discover models automatically.
      button.disabled=true;
      try {const s=await request<Settings>('/api/admin/ai/settings');if(!s.api_key_configured){status.textContent='请先在“其他”中配置 API Key。';showToast('请先配置生图 API Key。','info');busy=false;button.disabled=false;return;}}
      catch(error){showToast(errorText(error),'error');busy=false;button.disabled=false;return;}
      if(disposed)return;requestId=crypto.randomUUID();submitted={...details,request_id:requestId};jobId='';candidate='';result.hidden=true;
    }
    busy=true;button.disabled=true;button.textContent='正在生成…';status.textContent='正在提交任务…';
    if(unknown && jobId){void poll();return;}
    try{receive(await request<Job>(options.endpoint??'/api/admin/ai/icon-jobs',{method:'POST',body:JSON.stringify(submitted)}));}
    catch(error){failed(error);}
  });
  apply?.addEventListener('click',()=>{if(candidate){options.apply?.(candidate);apply.disabled=true;apply.textContent='已选择';status.textContent='已选择生成图标，保存工具后生效。';}});
  target.querySelector('[data-settings]')?.addEventListener('click',()=>options.openSettings?.());
  return {
    clearSelection(){if(apply){apply.disabled=false;apply.textContent='使用此图标';}status.textContent=candidate?'已切换为上传图片；生成图仍可重新选择。':'';},
    dispose(){disposed=true;if(timer)clearTimeout(timer);if(busy || unknown)showToast('已停止查看生成进度，后台任务可能仍在继续。','info');},
  };
}
