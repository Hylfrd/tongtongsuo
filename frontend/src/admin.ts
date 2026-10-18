import './admin.css';
import { showToast } from './toast';
import { validateForm, clearFieldError, markFieldError } from './validation';
import mascot from './assets/mascot-original.jpeg';
import screwdriver from './assets/screwdriver.png';
import { icon, githubIcon, type IconName } from './icons';

type Cabinet = { id: number; code: string; name: string; group_name: string; location: string; notes: string; lock_count: number; tool_count: number };
type Gateway = { id: number; code: string; name: string; provider_gateway_id: string | null; notes: string; status: string };
type Lock = { id: number; code: string; name: string; provider_lock_id: string | null; cabinet_id: number | null; gateway_id: number | null; notes: string; status: string };
type Tool = { id: number; cabinet_id: number; name: string; name_en: string; description: string; description_en: string; unit: string; unit_en: string; notes: string; quantity: number; icon: string; icon_data: string | null; available_quantity: number; borrowed_quantity: number; borrowers: {name: string; school_code: string | null; quantity: number; borrowed_at: string}[] };
type Loan = { id: number; user_name: string; school_code: string | null; cabinet_id: number; cabinet_name: string; cabinet_code: string; tool_id: number; tool_name: string; quantity: number; borrowed_at: string; returned_at: string | null; days: number };
type Overview = { cabinet_count: number; lock_count: number; gateway_count: number; tool_count: number; active_loan_count: number; hardware_configured: boolean };
type View = 'overview' | 'devices' | 'tools' | 'loans';
type Resource = 'cabinets' | 'locks' | 'gateways' | 'tools';
const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
const formatDate = (value: string | null): string => value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '—';
const imageData = (value: unknown): value is string => typeof value === 'string' && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value) && value.length < 710000;
const toolIcons: Record<string, {label: string; icon: IconName}> = {screwdriver: {label:'螺丝刀', icon:'key'}, tape:{label:'卷尺',icon:'tape'}, hammer:{label:'锤子',icon:'hammer'}, pliers:{label:'钳子',icon:'pliers'}, toolbox:{label:'工具箱',icon:'toolbox'}};

export async function mountAdmin(root: HTMLElement, user: {name: string}, onLogout: () => Promise<void>): Promise<void> {
  let view: View = 'overview';
  let cabinets: Cabinet[] = [], locks: Lock[] = [], gateways: Gateway[] = [], tools: Tool[] = [], loans: Loan[] = [];
  let overview: Overview | null = null;
  let toolCabinet = '', deviceTab: 'cabinets' | 'locks' | 'gateways' = 'cabinets';
  let loaded = false, forbidden = false, loanRequest = 0;
  let passwordChangePending = false;
  let filters = {q:'', cabinet_id:'', status:'all', sort:'borrowed_at', direction:'desc'};
  const titles: Record<View, string> = {overview:'概览',devices:'柜子与设备',tools:'工具管理',loans:'借还记录'};

  async function request<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, {signal:AbortSignal.timeout(15000), ...init, credentials:'same-origin', cache:'no-store', headers:{...(init?.body ? {'Content-Type':'application/json'} : {}), ...init?.headers}});
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) forbidden = true;
      let message = response.status === 401 ? '登录已过期，请重新登录。' : response.status === 403 ? '当前账号没有管理权限。' : `请求未完成（${response.status}），请稍后重试。`;
      try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; else if (body.detail && typeof body.detail.message === 'string') message = body.detail.message; else if (Array.isArray(body.detail)) message = '填写内容不符合要求，请检查字段长度、格式和数量。'; } catch { /* Keep a useful message for proxy errors. */ }
      throw new Error(message);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  function notice(message: string, error = false): void {
    showToast(message, error ? 'error' : 'success');
  }
  function errorMessage(error: unknown): string {
    if (error instanceof DOMException && error.name === 'TimeoutError') return '请求超时，请重试。';
    if (error instanceof TypeError) return '网络连接失败，请稍后再试。';
    return error instanceof Error ? error.message : '请求失败，请重试。';
  }
  function fail(error: unknown): void {
    notice(errorMessage(error),true);
    if (forbidden) { root.innerHTML = `<main class="admin-auth-error"><img src="${mascot}" alt="正雅苑"><h1>需要重新验证身份</h1><p>${escape(error instanceof Error ? error.message : '无法确认管理权限。')}</p><button class="adm-button" id="admin-reauth">返回登录</button></main>`; root.querySelector('#admin-reauth')?.addEventListener('click',()=>void onLogout().catch(fail)); return; }
  }
  function compactCabinetCode(code: string, name: string): boolean { return name === code || name === `${code}区`; }
  function itemName(item: {name:string;code?:string}): string { return item.code && !compactCabinetCode(item.code,item.name) ? `${item.code} · ${item.name}` : item.name; }
  function cabinetName(id: number | null): string { const cabinet = cabinets.find(item=>item.id===id); return cabinet ? itemName(cabinet) : '尚未分配柜子'; }
  function gatewayName(id: number | null): string { return gateways.find(item=>item.id===id)?.name ?? '未绑定网关'; }
  function options(items: {id:number;name:string;code?:string}[], selected: unknown, blank: string): string { return `<option value="">${blank}</option>` + items.map(item=>`<option value="${item.id}" ${String(selected)===String(item.id)?'selected':''}>${escape(itemName(item))}</option>`).join(''); }
  function empty(title: string, copy: string, action?: string): string { return `<div class="adm-empty">${icon('toolbox')}<h3>${title}</h3>${copy ? `<p>${copy}</p>` : ''}${action ?? ''}</div>`; }
  function controls(kind: Resource, id: number): string { return `<div class="adm-row-actions"><button class="adm-text" data-edit="${kind}" data-id="${id}">编辑</button><button class="adm-text adm-danger" data-delete="${kind}" data-id="${id}">删除</button></div>`; }
  function toolArt(tool: Tool): string { return imageData(tool.icon_data) ? `<img src="${escape(tool.icon_data)}" alt="" loading="lazy">` : tool.icon === 'screwdriver' ? `<img src="${screwdriver}" alt="" loading="lazy">` : icon(toolIcons[tool.icon]?.icon ?? 'toolbox'); }
  function captureFocus(): string | undefined {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || !root.contains(element)) return undefined;
    if (element.id) return `#${CSS.escape(element.id)}`;
    for (const attribute of ['data-edit', 'data-add', 'data-device-tab', 'data-view']) {
      const value = element.getAttribute(attribute);
      if (value !== null) return `[${attribute}="${CSS.escape(value)}"]${element.dataset.id ? `[data-id="${CSS.escape(element.dataset.id)}"]` : ''}`;
    }
    return undefined;
  }
  function restoreFocus(selector?: string): void {
    if (!selector) return;
    const target = root.querySelector<HTMLElement>(selector) ?? root.querySelector<HTMLElement>('#admin-page-title');
    target?.focus({preventScroll:true});
  }
  function navigate(next: View): void {
    if (view === next) return;
    view = next;
    shell('#admin-page-title', true);
    window.scrollTo({top:0,behavior:'instant'});
    if (view === 'loans') void loadLoans();
  }
  function shell(focusSelector?: string, animate = false): void {
    const currentContent = root.querySelector<HTMLElement>('#admin-content');
    if (currentContent) {
      root.querySelector('#admin-page-title')!.textContent = titles[view];
      root.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button=>{
        if (button.dataset.view === view) button.setAttribute('aria-current','page');
        else button.removeAttribute('aria-current');
      });
      currentContent.innerHTML = loaded ? content() : '<div class="adm-loading">正在读取管理数据…</div>';
      if (loaded) currentContent.removeAttribute('aria-busy');
      bindContent();
      restoreFocus(focusSelector);
      currentContent.getAnimations().forEach(animation=>animation.cancel());
      if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
        currentContent.animate([{opacity:.65,transform:'translateY(3px)'},{opacity:1,transform:'translateY(0)'}],{duration:340,easing:'cubic-bezier(.16,1,.3,1)'});
      }
      return;
    }
    root.innerHTML = `<div class="admin-shell"><header class="adm-sidebar"><a class="adm-brand" href="/"><img src="${mascot}" alt="正雅苑小狼"><span>正雅苑<small>共享工具箱</small></span></a><nav class="adm-nav" aria-label="管理导航">${([['overview','概览','toolbox'],['devices','柜子与设备','lock'],['tools','工具管理','key'],['loans','借还记录','clock']] as [View,string,IconName][]).map(([key,label,symbol])=>`<button data-view="${key}" ${view===key?'aria-current="page"':''}>${icon(symbol)}<span>${label}</span>${view===key?'<i></i>':''}</button>`).join('')}</nav><div class="adm-account-actions"><button class="adm-logout" id="admin-password">${icon('key')}<span>修改密码</span></button><button class="adm-logout" id="admin-logout">${icon('logout')}<span>退出管理</span></button></div></header><div class="adm-workspace"><main class="adm-main"><div class="adm-page-heading"><div><h1 id="admin-page-title" tabindex="-1">${titles[view]}</h1></div><button class="adm-icon-button" id="admin-refresh" aria-label="刷新数据">${icon('refresh')}<span>刷新</span></button></div><div id="admin-content" ${loaded?'':'aria-busy="true"'}>${loaded ? content() : '<div class="adm-loading">正在读取管理数据…</div>'}</div></main><footer class="adm-footer"><span>正雅苑 · 共享工具箱</span><div><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2025433394号-1</a><a href="https://github.com/Hylfrd/tongtongsuo" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 查看项目">${githubIcon}</a></div></footer></div></div>`;
    root.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.view as View)));
    root.querySelector('#admin-password')?.addEventListener('click',changePassword);
    root.querySelector('#admin-logout')?.addEventListener('click',()=>void onLogout().catch(fail));
    root.querySelector('#admin-refresh')?.addEventListener('click',()=>void refresh());
    bindContent();
    restoreFocus(focusSelector);
  }
  function content(): string {
    if(view==='overview') {
      const stats = overview!;
      return `<section class="adm-stats" aria-label="当前管理概况"><div><strong>${stats.cabinet_count}</strong><span>共享柜子</span></div><div><strong>${stats.tool_count}</strong><span>工具种类</span></div><div><strong>${stats.active_loan_count}</strong><span>未归还记录</span></div><div><strong>${stats.lock_count}<small> / ${stats.gateway_count}</small></strong><span>锁 / 网关</span></div></section><div class="adm-section-heading"><h2>柜子一览</h2><button class="adm-text" data-add="cabinets">添加柜子 ${icon('arrow')}</button></div>${cabinetCards()}`;
    }
    if(view==='devices') return `<div class="adm-tabs" aria-label="设备类别">${([['cabinets','柜子'],['locks','锁'],['gateways','网关']] as [Resource,string][]).map(([key,label])=>`<button data-device-tab="${key}" aria-pressed="${deviceTab===key}">${label}<span>${key==='cabinets'?cabinets.length:key==='locks'?locks.length:gateways.length}</span></button>`).join('')}</div><div class="adm-section-heading"><h2>${deviceTab==='cabinets'?'柜子与分组':deviceTab==='locks'?'锁设备':'网关设备'}</h2><button class="adm-button" data-add="${deviceTab}">添加${deviceTab==='cabinets'?'柜子':deviceTab==='locks'?'锁':'网关'}</button></div>${deviceTab==='cabinets'?cabinetCards():deviceTable(deviceTab)}`;
    if(view==='tools') {
      const visible = tools.filter(tool=>!toolCabinet || String(tool.cabinet_id)===toolCabinet);
      return `<div class="adm-toolbar"><label class="adm-filter">所在柜子<select id="admin-tool-cabinet">${options(cabinets,toolCabinet,'全部柜子')}</select></label><button class="adm-button" data-add="tools">添加工具 ${icon('toolbox')}</button></div>${visible.length?`<div class="adm-tools">${visible.map(tool=>`<article class="adm-tool"><div class="adm-tool-art">${toolArt(tool)}</div><div class="adm-tool-body"><span class="adm-meta">${escape(cabinetName(tool.cabinet_id))}</span><h2>${escape(tool.name)}</h2>${tool.description ? `<p>${escape(tool.description)}</p>` : ''}<div class="adm-inventory"><strong>${tool.available_quantity}<small>${escape(tool.unit || '件')}可借</small></strong><span>总计 ${tool.quantity} ${escape(tool.unit || '件')} · 借出 ${tool.borrowed_quantity} ${escape(tool.unit || '件')}</span></div>${tool.borrowers.length?`<details class="adm-borrowers"><summary>查看 ${tool.borrowers.length} 位借用人</summary>${tool.borrowers.map(person=>`<p><strong>${escape(person.name)}</strong> ${escape(person.school_code)} · ${person.quantity} ${escape(tool.unit || '件')}<br><span>${escape(formatDate(person.borrowed_at))}</span></p>`).join('')}</details>`:''}${tool.notes?`<p class="adm-tool-note">备注：${escape(tool.notes)}</p>`:''}${controls('tools',tool.id)}</div></article>`).join('')}</div>`:empty('暂无工具',cabinets.length ? '' : '请先添加柜子。',`<button class="adm-text" data-add="tools">添加工具 ${icon('arrow')}</button>`)}`;
    }
    return `<form class="adm-loan-filters" id="admin-loan-filters" novalidate><label class="adm-filter adm-search">搜索<input type="search" name="q" value="${escape(filters.q)}" placeholder="姓名、人员编号或工具名称" maxlength="100"></label><label class="adm-filter">柜子<select name="cabinet_id">${options(cabinets,filters.cabinet_id,'全部柜子')}</select></label><label class="adm-filter">归还情况<select name="status">${[['all','全部记录'],['active','尚未归还'],['returned','已归还']].map(([key,label])=>`<option value="${key}" ${filters.status===key?'selected':''}>${label}</option>`).join('')}</select></label><label class="adm-filter">排序<select name="sort">${[['borrowed_at','借用时间'],['returned_at','归还时间'],['days','借用天数'],['user_name','使用人'],['cabinet_name','柜子'],['tool_name','工具']].map(([key,label])=>`<option value="${key}" ${filters.sort===key?'selected':''}>${label}</option>`).join('')}</select></label><label class="adm-filter">顺序<select name="direction"><option value="desc" ${filters.direction==='desc'?'selected':''}>降序</option><option value="asc" ${filters.direction==='asc'?'selected':''}>升序</option></select></label><button class="adm-button" type="submit">查询</button></form><div class="adm-section-heading"><h2>借还明细 <span id="admin-loan-count">${loans.length} 条</span></h2><button class="adm-outline" id="admin-export">导出 Excel ${icon('arrow')}</button></div><div id="admin-loan-result">${loanTable()}</div>`;
  }
  function cabinetCards(): string {
    if(!cabinets.length) return empty('暂无柜子','',`<button class="adm-outline" data-add="cabinets">添加柜子</button>`);
    const groups = new Map<string,Cabinet[]>();
    cabinets.forEach(cabinet=>{const group=cabinet.group_name || '未分组'; groups.set(group,[...(groups.get(group)??[]),cabinet]);});
    return '<div class="adm-cabinet-groups">' + [...groups].map(([group,items])=>`<section class="adm-cabinet-group">${items.length > 1 || items[0].name !== group ? `<h3>${escape(group)}<span>${items.length} 个柜子</span></h3>` : ''}<div class="adm-cabinets">${items.map(cabinet=>`<article class="adm-cabinet"><div class="adm-cabinet-top"><h2>${escape(cabinet.name)}</h2>${icon('toolbox')}</div>${!compactCabinetCode(cabinet.code,cabinet.name) ? `<span class="adm-cabinet-code">${escape(cabinet.code)}</span>` : ''}${cabinet.location ? `<p>${escape(cabinet.location)}</p>` : ''}<div class="adm-cabinet-summary"><span>${cabinet.lock_count} 把锁</span><span>${cabinet.tool_count} 种工具</span></div>${locks.filter(lock=>lock.cabinet_id===cabinet.id).map(lock=>`<div class="adm-bound-lock">${icon('lock')}<span>${escape(lock.code)} · ${escape(lock.name)}</span><span>状态未知</span></div>`).join('')}${cabinet.notes?`<p class="adm-tool-note">${escape(cabinet.notes)}</p>`:''}<div class="adm-scan-links"><a href="/scan/${encodeURIComponent(cabinet.code)}" target="_blank" rel="noopener">柜子入口 ${icon('arrow')}</a><button class="adm-text" data-copy-scan="${escape(cabinet.code)}">复制链接</button><a href="/m/scan/${encodeURIComponent(cabinet.code)}" target="_blank" rel="noopener">手机预览</a></div>${controls('cabinets',cabinet.id)}</article>`).join('')}</div></section>`).join('') + '</div>';
  }
  function deviceTable(kind: 'locks'|'gateways'): string {
    const items = kind==='locks'?locks:gateways;
    if(!items.length) return empty(kind==='locks'?'暂无锁':'暂无网关','');
    return `<div class="adm-table-scroll" tabindex="0" role="region" aria-label="${kind==='locks'?'锁':'网关'}设备列表"><table class="adm-table"><thead><tr><th>设备 / 编号</th><th>${kind==='locks'?'柜子 / 网关':'云端设备编号'}</th><th>状态</th><th>设备操作</th><th>管理</th></tr></thead><tbody>${items.map(item=>`<tr><td><strong>${escape(item.name)}</strong><small>${escape(item.code)}</small></td><td>${kind==='locks'?`${escape(cabinetName((item as Lock).cabinet_id))}<small>${escape(gatewayName((item as Lock).gateway_id))}</small>`:escape((item as Gateway).provider_gateway_id || '未配置')}</td><td><span class="adm-badge">未知</span></td><td><div class="adm-device-actions">${kind==='locks'?`<button class="adm-outline" data-admin-action="unlock" data-id="${item.id}">${icon('open')}开锁</button><button class="adm-outline" data-admin-action="lock" data-id="${item.id}">${icon('lock')}关锁</button>`:''}<button class="adm-text" data-admin-action="status" data-kind="${kind}" data-id="${item.id}">请求状态</button></div></td><td>${controls(kind,item.id)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function loanTable(): string { return loans.length ? `<div class="adm-table-scroll" tabindex="0" role="region" aria-label="借还记录"><table class="adm-table adm-loan-table"><thead><tr><th>使用人 / 编号</th><th>柜子</th><th>工具 / 数量</th><th>借用时间</th><th>归还时间</th><th>借用天数</th><th>状态</th></tr></thead><tbody>${loans.map(loan=>`<tr><td><strong>${escape(loan.user_name)}</strong><small>${escape(loan.school_code || '无人员编号')}</small></td><td>${escape(loan.cabinet_name)}${!compactCabinetCode(loan.cabinet_code,loan.cabinet_name) ? `<small>${escape(loan.cabinet_code)}</small>` : ''}</td><td>${escape(loan.tool_name)}<small>${loan.quantity} ${escape(tools.find(tool=>tool.id===loan.tool_id)?.unit || '件')}</small></td><td>${escape(formatDate(loan.borrowed_at))}</td><td>${escape(formatDate(loan.returned_at))}</td><td>${loan.days} 天</td><td><span class="adm-badge ${loan.returned_at?'is-returned':'is-active'}">${loan.returned_at?'已归还':'借用中'}</span></td></tr>`).join('')}</tbody></table></div>` : empty('暂无借还记录',''); }
  function loanQuery(): string { return new URLSearchParams(Object.entries(filters).filter(([,value])=>!!value)).toString(); }
  async function loadLoans(): Promise<void> {
    const version=++loanRequest;
    const target=root.querySelector<HTMLElement>('#admin-loan-result');
    if(target) target.setAttribute('aria-busy','true');
    try {const result=await request<{items:Loan[]}>(`/api/admin/loans?${loanQuery()}`); if(version!==loanRequest || view!=='loans')return; loans=result.items; const current=root.querySelector('#admin-loan-result'); if(current){current.innerHTML=loanTable();current.removeAttribute('aria-busy');} const count=root.querySelector('#admin-loan-count'); if(count) count.textContent=`${loans.length} 条`;}
    catch(error){if(version===loanRequest){if(target) target.removeAttribute('aria-busy'); fail(error);}}
  }
  function wireValidation(form: HTMLFormElement): void {
    const clear = (event: Event) => {
      const target = event.target;
      if (target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement) clearFieldError(target);
    };
    form.addEventListener('input',clear);
    form.addEventListener('change',clear);
  }
  function bindContent(): void {
    root.querySelectorAll<HTMLButtonElement>('[data-go]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.go as View)));
    root.querySelectorAll<HTMLButtonElement>('[data-device-tab]').forEach(button=>button.addEventListener('click',()=>{deviceTab=button.dataset.deviceTab as 'cabinets'|'locks'|'gateways';shell(`[data-device-tab="${deviceTab}"]`, true);}));
    root.querySelectorAll<HTMLButtonElement>('[data-add]').forEach(button=>button.addEventListener('click',()=>edit(button.dataset.add as Resource)));
    root.querySelectorAll<HTMLButtonElement>('[data-edit]').forEach(button=>button.addEventListener('click',()=>edit(button.dataset.edit as Resource,Number(button.dataset.id))));
    root.querySelectorAll<HTMLButtonElement>('[data-delete]').forEach(button=>button.addEventListener('click',()=>void remove(button.dataset.delete as Resource,Number(button.dataset.id),button)));
    root.querySelectorAll<HTMLButtonElement>('[data-copy-scan]').forEach(button=>button.addEventListener('click',()=>{const url = new URL(`/scan/${encodeURIComponent(button.dataset.copyScan!)}`,location.origin).href; void navigator.clipboard.writeText(url).then(()=>notice('柜子链接已复制。')).catch(()=>showToast('复制失败，请打开柜子入口后复制地址。','error'));}));
    root.querySelectorAll<HTMLButtonElement>('[data-admin-action]').forEach(button=>button.addEventListener('click',()=>void deviceAction(button)));
    root.querySelector<HTMLSelectElement>('#admin-tool-cabinet')?.addEventListener('change',event=>{toolCabinet=(event.target as HTMLSelectElement).value;shell('#admin-tool-cabinet');});
    const loanForm = root.querySelector<HTMLFormElement>('#admin-loan-filters');
    if (loanForm) wireValidation(loanForm);
    loanForm?.addEventListener('submit',event=>{event.preventDefault();if (!validateForm(event.currentTarget as HTMLFormElement)) return;const form=new FormData(event.currentTarget as HTMLFormElement);filters={q:String(form.get('q')??'').trim(),cabinet_id:String(form.get('cabinet_id')??''),status:String(form.get('status')),sort:String(form.get('sort')),direction:String(form.get('direction'))};void loadLoans();});
    root.querySelector<HTMLButtonElement>('#admin-export')?.addEventListener('click',event=>void exportLoans(event.currentTarget as HTMLButtonElement));
  }
  async function refresh(focusSelector = captureFocus()): Promise<void> {
    const button=root.querySelector<HTMLButtonElement>('#admin-refresh');if(button)button.disabled=true;
    try {
      const [a,b,c,d,e]=await Promise.all([request<Overview>('/api/admin/overview'),request<{items:Cabinet[]}>('/api/admin/cabinets'),request<{items:Lock[]}>('/api/admin/locks'),request<{items:Gateway[]}>('/api/admin/gateways'),request<{items:Tool[]}>('/api/admin/tools')]);
      overview=a;cabinets=b.items;locks=c.items;gateways=d.items;tools=e.items;loaded=true;shell(focusSelector);if(view==='loans')await loadLoans();
    } catch(error){if(!loaded){const target=root.querySelector('#admin-content');if(target){target.innerHTML=empty('加载失败','点击右上角刷新重试。');target.removeAttribute('aria-busy');}}fail(error);} finally {if(button){button.disabled=false;if(button.isConnected)restoreFocus(focusSelector);}}
  }
  async function deviceAction(button: HTMLButtonElement): Promise<void> {
    const {adminAction:action,id,kind}=button.dataset;
    if(action!=='status' && !window.confirm(`确认向此设备发送${action==='unlock'?'开锁':'关锁'}请求？`))return;
    button.disabled=true;
    try{await request(kind==='gateways'?`/api/admin/gateways/${id}/status`:`/api/admin/locks/${id}/actions`,{method:'POST',body:JSON.stringify(kind==='gateways'?{}:{action})});notice('请求已提交；请核实设备实际状态。');}
    catch(error){fail(error);}finally{button.disabled=false;}
  }
  async function remove(kind: Resource,id:number,button:HTMLButtonElement): Promise<void> {
    const noun={cabinets:'柜子',locks:'锁',gateways:'网关',tools:'工具'}[kind];
    if(!window.confirm(`确认删除这个${noun}？有关联或未归还记录时，系统可能拒绝删除。历史借还记录将保留。`))return;
    button.disabled=true;
    try{await request(`/api/admin/${kind}/${id}`,{method:'DELETE'});await refresh(`[data-add="${kind}"]`);notice(`${noun}已删除。`);}catch(error){fail(error);}finally{button.disabled=false;}
  }
  async function exportLoans(button:HTMLButtonElement): Promise<void> {
    button.disabled=true;
    try{const response=await fetch(`/api/admin/loans/export.xlsx?${loanQuery()}`,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(30000)});if(!response.ok){if(response.status===401||response.status===403)forbidden=true;throw new Error('导出未完成，请确认登录状态后重试。');}const file=await response.blob();const url=URL.createObjectURL(file);const link=document.createElement('a');link.href=url;const disposition=response.headers.get('Content-Disposition')??'';let filename='';try{const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i);const plain=disposition.match(/filename="([^"]+)"/i);filename=encoded?decodeURIComponent(encoded[1]):plain?.[1]??'';}catch{/* Use a safe local filename. */}link.download=/^[^\/\\\x00-\x1f]{1,160}\.xlsx$/i.test(filename)?filename:`zhengya-loans-${new Date().toISOString().slice(0,10)}.xlsx`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notice('Excel 已导出。');}catch(error){fail(error);}finally{button.disabled=false;}
  }
  function changePassword(): void {
    if (passwordChangePending) {
      showToast('密码正在保存，请稍候。','info');
      return;
    }
    const dialog = document.createElement('dialog');
    dialog.className = 'adm-dialog adm-password-dialog';
    dialog.setAttribute('aria-labelledby','admin-password-title');
    dialog.innerHTML = `<form class="adm-edit-form" novalidate><div class="adm-dialog-head"><h2 id="admin-password-title">修改密码</h2><button type="button" class="adm-dialog-close" aria-label="关闭">×</button></div><div class="adm-password-fields"><label class="adm-field">当前密码<input name="current_password" type="password" autocomplete="current-password" minlength="5" maxlength="256" required></label><label class="adm-field">新密码<input name="new_password" type="password" autocomplete="new-password" minlength="5" maxlength="256" required></label><label class="adm-field">确认新密码<input name="confirm_password" type="password" autocomplete="new-password" minlength="5" maxlength="256" required></label></div><div class="adm-form-actions"><button type="button" class="adm-outline" data-cancel>取消</button><button type="submit" class="adm-button">保存密码</button></div></form>`;
    const form = dialog.querySelector<HTMLFormElement>('form')!;
    wireValidation(form);
    const submit = form.querySelector<HTMLButtonElement>('[type="submit"]')!;
    const previousFocus = document.activeElement as HTMLElement | null;
    let timer: ReturnType<typeof setInterval> | undefined;
    let busy = false, closed = false, cooldownUntil = 0;
    const updateSubmit = () => {
      const seconds = Math.max(0,Math.ceil((cooldownUntil - Date.now()) / 1000));
      submit.disabled = busy || seconds > 0;
      submit.textContent = busy ? '正在保存…' : seconds ? `${seconds} 秒后重试` : '保存密码';
      if (!seconds && timer) { clearInterval(timer); timer = undefined; }
    };
    dialog.addEventListener('close',()=>{
      closed = true;
      if (timer) clearInterval(timer);
      form.reset();
      dialog.remove();
      if (previousFocus?.isConnected) previousFocus.focus({preventScroll:true});
    });
    dialog.querySelector('.adm-dialog-close')?.addEventListener('click',()=>dialog.close());
    dialog.querySelector('[data-cancel]')?.addEventListener('click',()=>dialog.close());
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if (busy || cooldownUntil > Date.now() || !validateForm(form)) return;
      const current = form.elements.namedItem('current_password') as HTMLInputElement;
      const password = form.elements.namedItem('new_password') as HTMLInputElement;
      const confirm = form.elements.namedItem('confirm_password') as HTMLInputElement;
      if (password.value !== confirm.value) {
        markFieldError(confirm);
        showToast('两次输入的新密码不一致。','error');
        confirm.focus();
        return;
      }
      busy = true;
      passwordChangePending = true;
      updateSubmit();
      try {
        const response = await fetch('/api/auth/admin/password',{
          method:'POST', credentials:'same-origin', cache:'no-store', signal:AbortSignal.timeout(15000),
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({current_password:current.value,new_password:password.value,confirm_password:confirm.value}),
        });
        if (!response.ok) {
          const retryAfter = response.headers.get('Retry-After');
          if (retryAfter) {
            const numeric = Number(retryAfter);
            const delay = Number.isFinite(numeric) ? numeric * 1000 : Date.parse(retryAfter) - Date.now();
            if (delay > 0) {
              cooldownUntil = Date.now() + delay;
              if (!closed && !timer) timer = setInterval(updateSubmit,250);
            }
          }
          let message = response.status === 429 ? '尝试过于频繁，请稍后重试。' : response.status === 401 ? '登录已过期，请重新登录。' : '密码修改失败，请重试。';
          try {
            const result = await response.json();
            if (!closed && result.detail?.code === 'CURRENT_PASSWORD_INVALID') markFieldError(current);
            if (!closed && result.detail?.code === 'PASSWORD_MISMATCH') markFieldError(confirm);
            if (typeof result.detail === 'string') message = result.detail;
            else if (typeof result.detail?.message === 'string') message = result.detail.message;
            else if (Array.isArray(result.detail)) message = '密码需要 5–256 个字符。';
          } catch { /* Keep the fallback without logging password data. */ }
          throw new Error(message);
        }
        form.reset();
        if (!closed) dialog.close();
        location.assign('/admin?passwordChanged=1');
      } catch(error) {
        showToast(errorMessage(error),'error');
      } finally {
        busy = false;
        passwordChangePending = false;
        if (!closed) updateSubmit();
      }
    });
    root.append(dialog);
    dialog.showModal();
  }
  function edit(kind:Resource,id?:number):void {
    if(kind==='tools'&&!cabinets.length){notice('请先在“柜子与设备”中添加一个柜子。',true);return;}
    const source: Record<string,unknown>=(id?({cabinets,locks,gateways,tools}[kind] as unknown as Record<string,unknown>[]).find(item=>item.id===id):undefined)??{};
    const noun={cabinets:'柜子',locks:'锁',gateways:'网关',tools:'工具'}[kind];
    const text=(name:string,label:string,required=false,max=120,type='text')=>`<label class="adm-field">${label}${required?' <span aria-hidden="true">*</span>':''}<input name="${name}" type="${type}" value="${escape(source[name])}" maxlength="${max}" ${required?'required':''} ${type==='number'?'min="0" max="100000" step="1"':''}></label>`;
    const area=(name:string,label:string)=>`<label class="adm-field adm-wide">${label}<textarea name="${name}" rows="3" maxlength="2000">${escape(source[name])}</textarea></label>`;
    let fields=kind==='tools'?`<label class="adm-field adm-wide">所在柜子 <span aria-hidden="true">*</span><select name="cabinet_id" required>${options(cabinets,source.cabinet_id??toolCabinet,'请选择柜子')}</select></label>${text('name','工具名称',true)}${text('name_en','英文名称（选填）')}<label class="adm-field">总数量 <span aria-hidden="true">*</span><input name="quantity" type="number" min="0" max="100000" step="1" value="${escape(source.quantity??1)}" required></label><label class="adm-field">单位 <span aria-hidden="true">*</span><input name="unit" value="${escape(source.unit || '件')}" maxlength="16" required list="admin-tool-units"><datalist id="admin-tool-units"><option value="件"><option value="把"><option value="个"><option value="套"><option value="卷"><option value="副"></datalist></label>${text('unit_en','英文单位（选填）',false,32)}<label class="adm-field">工具图标<select name="icon">${Object.entries(toolIcons).map(([key,value])=>`<option value="${key}" ${source.icon===key?'selected':''}>${value.label}</option>`).join('')}</select></label><label class="adm-field">自定义图片<input name="image" type="file" accept="image/png,image/jpeg,image/webp"><small>PNG / JPEG / WebP，最大 512 KB</small></label>${source.icon_data?'<label class="adm-checkbox adm-wide"><input type="checkbox" name="clear_image">移除现有自定义图片</label>':''}${area('description','描述 / 使用说明')}${area('description_en','英文描述（选填）')}${area('notes','备注')}`:`${text('code','内部编号',true,64)}${text('name',`${noun}名称`,true)}${kind==='cabinets'?`<label class="adm-field">柜子分组<input name="group_name" value="${escape(source.group_name)}" maxlength="120" list="admin-cabinet-groups"><datalist id="admin-cabinet-groups">${[...new Set(['A区','B区',...cabinets.map(cabinet=>cabinet.group_name).filter(Boolean)])].map(group=>`<option value="${escape(group)}">`).join('')}</datalist></label>${text('location','所在位置')}`:kind==='locks'?`<label class="adm-field">所在柜子<select name="cabinet_id">${options(cabinets,source.cabinet_id,'暂不分配')}</select></label><label class="adm-field">绑定网关<select name="gateway_id">${options(gateways,source.gateway_id,'暂不绑定')}</select></label>${text('provider_lock_id','通通锁设备编号（可留空）',false,64)}`:text('provider_gateway_id','通通锁网关编号（可留空）',false,64)}${area('notes','备注')}`;
    const dialog=document.createElement('dialog');dialog.className='adm-dialog';dialog.setAttribute('aria-labelledby','admin-dialog-title');dialog.innerHTML=`<form class="adm-edit-form" novalidate><div class="adm-dialog-head"><div><h2 id="admin-dialog-title">${id?'编辑':'添加'}${noun}</h2></div><button type="button" class="adm-dialog-close" aria-label="关闭">×</button></div><div class="adm-form-grid">${fields}</div><div class="adm-form-actions"><button type="button" class="adm-outline" data-cancel>取消</button><button type="submit" class="adm-button">保存${noun}</button></div></form>`;
    root.append(dialog);const previousFocus=document.activeElement as HTMLElement|null;
    const returnFocusSelector = id ? `[data-edit="${kind}"][data-id="${id}"]` : `[data-add="${kind}"]`;
    dialog.addEventListener('close',()=>{dialog.remove();if(previousFocus?.isConnected)previousFocus.focus({preventScroll:true});});dialog.querySelector('.adm-dialog-close')?.addEventListener('click',()=>dialog.close());dialog.querySelector('[data-cancel]')?.addEventListener('click',()=>dialog.close());
    wireValidation(dialog.querySelector<HTMLFormElement>('form')!);
    dialog.querySelector('form')!.addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget as HTMLFormElement;if(!validateForm(form))return;const data=new FormData(form);const submit=form.querySelector<HTMLButtonElement>('[type="submit"]')!;submit.disabled=true;
      try{
        const payload:Record<string,unknown>={};data.forEach((value,key)=>{if(typeof value==='string'&&key!=='clear_image')payload[key]=value.trim();});
        if(kind==='locks'){payload.cabinet_id=payload.cabinet_id?Number(payload.cabinet_id):null;payload.gateway_id=payload.gateway_id?Number(payload.gateway_id):null;payload.provider_lock_id=payload.provider_lock_id||null;}
        if(kind==='gateways')payload.provider_gateway_id=payload.provider_gateway_id||null;
        if(kind==='tools'){
          payload.quantity=Number(payload.quantity);payload.cabinet_id=Number(payload.cabinet_id);payload.icon_data=data.has('clear_image')?null:source.icon_data??null;
          const file=data.get('image');if(file instanceof File && file.size){if(file.size>512*1024||!['image/png','image/jpeg','image/webp'].includes(file.type))throw new Error('请选择小于 512 KB 的 PNG、JPEG 或 WebP 图片。');payload.icon_data=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('图片读取失败，请重新选择。'));reader.readAsDataURL(file);});}
        }
        await request(`/api/admin/${kind}${id?`/${id}`:''}`,{method:id?'PATCH':'POST',body:JSON.stringify(payload)});dialog.close();await refresh(returnFocusSelector);notice(`${noun}已保存。`);
      }catch(error){if(forbidden){dialog.close();fail(error);}else showToast(errorMessage(error),'error');}finally{submit.disabled=false;}
    });dialog.showModal();
  }
  shell();await refresh();
}
