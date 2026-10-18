import './style.css';
import { installAmbient } from './ambient';
import mascot from './assets/mascot-original.jpeg';
import flower from './assets/flower.png';
import screwdriver from './assets/screwdriver.png';
import toolbox from './assets/toolbox.png';
import { icon, githubIcon, type IconName } from './icons';
import { showToast } from './toast';
import { validateForm, clearFieldError } from './validation';
import { messages, type Language } from './i18n';
import { shouldOpenAdmin } from './routing';
import { licenseHref } from './license-navigation';
import { RequestScope, StaleRequestError } from './request-scope';

type Identity = { name: string; school_code: string | null; role?: 'admin' };
type Cabinet = { id: number; code: string; name: string; group_name: string; location: string };
type Tool = { id: number; name: string; name_en: string; description: string; description_en: string; unit: string; unit_en: string; icon: string; icon_data: string | null; available_quantity: number };
type Loan = { id: number; tool_id: number; tool_name: string; tool_name_en: string; quantity: number; borrowed_at: string; unit: string; unit_en: string; icon: string; icon_data: string | null };
type View = 'loading' | 'scan' | 'login' | 'home' | 'borrow' | 'return' | 'unlock' | 'success';
const app = document.querySelector<HTMLDivElement>('#app')!;
const preview = location.pathname !== '/admin' && new URLSearchParams(location.search).get('preview') === '1';
const mobile = /^\/m(?:\/|$)/.test(location.pathname);
const adminPath = location.pathname === '/admin';
const mobileAdmin = (): boolean => mobile || (adminPath && new URLSearchParams(location.search).get('mobile') === '1') || matchMedia('(max-width:760px)').matches || /Android|iPhone|iPod|IEMobile|Opera Mini/i.test(navigator.userAgent);
const scanMatch = location.pathname.match(/^\/(?:m\/)?scan\/([^/]+)\/?$/);
let cabinetCode: string | null = null;
try { if (scanMatch) cabinetCode = decodeURIComponent(scanMatch[1]); } catch { /* An invalid QR is not a cabinet. */ }
let language: Language = 'zh';
let view: View = 'loading', mode: 'borrow' | 'return' = 'borrow';
let user: Identity | null = null, cabinet: Cabinet | null = null;
let tools: Tool[] = [], loans: Loan[] = [];
let selected = new Map<number, number>(), quantities = new Map<number, number>();
let disposeAmbient: (() => void) | undefined;
let busy = false, opening = false, previewOpened = false, adminMounted = false;
let listError = false, listLoading = false;
let loginBlockedUntil = 0;
let cooldownTimer: ReturnType<typeof setInterval> | undefined;
let demoTimer: ReturnType<typeof setTimeout> | undefined;
const requestScope = new RequestScope();
function invalidateRequests(): void { requestScope.invalidate(); listLoading = false; }
const t = (key: keyof typeof messages.zh): string => messages[language][key];
const esc = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]!));
const localized = (zh: string, en: string): string => language === 'en' && en ? en : zh;
const cabinetUrl = (): string => `${mobile ? '/m' : ''}/scan/${encodeURIComponent(cabinetCode ?? '')}`;
const endpoint = (suffix = ''): string => `/api/cabinets/${encodeURIComponent(cabinetCode ?? '')}${suffix}`;
const dataImage = (value: unknown): value is string => typeof value === 'string' && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value) && value.length < 710000;
function readIdentity(value: { user?: Identity }): Identity {
  if (!value.user || typeof value.user.name !== 'string' || (value.user.school_code !== null && typeof value.user.school_code !== 'string')) throw new Error(t('failed'));
  return { name: value.user.name, school_code: value.user.school_code, ...(value.user.role === 'admin' ? { role: 'admin' as const } : {}) };
}
async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const ticket = requestScope.begin();
  try {
    let response: Response;
    try { response = await fetch(url, { ...init, credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.any([ticket.signal, AbortSignal.timeout(15000)]) }); }
    catch { ticket.check(); throw new Error(t('network')); }
    ticket.check();
    if (!response.ok) {
      let message = response.status === 401 ? t('expired') : t('failed');
      try {
        const body = await response.json();
        if (body.detail?.code === 'HARDWARE_NOT_CONFIGURED') message = t('hardware');
        else if (body.detail?.code === 'CABINET_NOT_FOUND') message = t('invalidCabinet');
        else if (language === 'zh' && typeof body.detail?.message === 'string') message = body.detail.message;
      } catch { /* Retain translated fallback for non-JSON errors. */ }
      ticket.check();
      if (response.status === 401 && user) {
        invalidateRequests(); user = null; tools = []; loans = []; selected.clear(); quantities.clear(); busy = false; opening = false;
        void move('login'); showToast(message, 'error'); throw new StaleRequestError();
      }
      throw new Error(message);
    }
    const data = response.status === 204 ? undefined as T : await response.json() as T;
    ticket.check(); return data;
  } finally { ticket.release(); }
}
function errorToast(error: unknown): void { if (!(error instanceof StaleRequestError)) showToast(error instanceof Error ? error.message : t('failed'), 'error'); }
function wolf(): string {
  return `<span class="wolf" aria-hidden="true"><img src="${mascot}" alt="" decoding="async"></span>`;
}
function cabinetLabel(): string {
  if (!cabinet) return '';
  const standard = ['A', 'B'].includes(cabinet.code) && cabinet.name === `${cabinet.code}区`;
  return standard ? localized(cabinet.name, `Area ${cabinet.code}`) : `${cabinet.name}${cabinet.name === cabinet.code ? '' : ` · ${cabinet.code}`}`;
}
function languageButton(): string {
  return `<button class="text-button language-button" data-action="language" aria-label="${language === 'zh' ? 'Switch to English' : '切换为中文'}">${language === 'zh' ? 'EN' : '中文'}</button>`;
}
function logoutButton(): string {
  return user ? `<button class="text-button logout-button" data-action="logout" ${busy ? 'disabled' : ''}>${icon('logout')}<span>${t('logout')}</span></button>` : '';
}
function licenseSource(): string {
  const url = new URL(location.href);
  url.searchParams.delete('signedOut'); url.searchParams.delete('passwordChanged');
  url.searchParams.set('resume', view === 'unlock' ? mode : view);
  url.searchParams.set('lang', language);
  return url.pathname + url.search + url.hash;
}
function illustration(): string {
  return `<div class="login-illustration"><div class="illustration-title"><div class="illustration-meta"><span>${t('hall')}</span>${cabinet ? `<span class="illustration-cabinet">${esc(cabinetLabel())}</span>` : ''}</div><strong>${t('title')}</strong></div><div class="illustration-stage" aria-hidden="true"><img class="scene-flower" src="${flower}" alt=""><div class="scene-wolf">${wolf()}</div><img class="scene-toolbox" src="${toolbox}" alt=""><img class="scene-screwdriver" src="${screwdriver}" alt=""></div></div>`;
}
function toolArt(tool: {icon: string; icon_data: string | null}): string {
  const symbols: Record<string, IconName> = { tape: 'tape', hammer: 'hammer', pliers: 'pliers', toolbox: 'toolbox' };
  return dataImage(tool.icon_data) ? `<img src="${esc(tool.icon_data)}" alt="">` : tool.icon === 'screwdriver' ? `<img src="${screwdriver}" alt="">` : icon(symbols[tool.icon] ?? 'toolbox');
}
function heading(title: string): string { return `<div class="workspace-heading"><button class="back-button" data-action="home" aria-label="${t('back')}">${icon('back')}</button><h1 tabindex="-1">${esc(title)}</h1></div>`; }
function loginContent(): string {
  return `<section class="login-layout">${illustration()}<div class="login-panel"><div class="panel-heading"><h1 tabindex="-1">${t('login')}</h1>${languageButton()}</div><p class="login-hint">${t('loginHint')}</p>${!preview ? `<form id="login-form" method="post" novalidate><fieldset ${busy ? 'disabled' : ''}><div class="field"><label for="username">${t('username')}</label><div class="input-wrap">${icon('user')}<input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="128" placeholder="${t('accountHint')}" required></div></div><div class="field"><label for="password">${t('password')}</label><div class="input-wrap">${icon('key')}<input id="password" name="password" type="password" autocomplete="current-password" maxlength="256" placeholder="${t('passwordHint')}" required></div></div><button class="primary-button" id="login-button" type="submit">${t('login')} ${icon('arrow')}</button></fieldset></form>` : ''}${preview ? `<button class="secondary-button preview-entry" data-action="preview-login">${t('previewLogin')}</button>` : ''}</div></section>`;
}
function scanContent(): string {
  return `<section class="login-layout">${illustration()}<div class="scan-panel"><div class="panel-heading"><h1 tabindex="-1">${t('scan')}</h1>${languageButton()}</div>${logoutButton()}<p>${t('scanDetail')}</p><button class="primary-button" data-action="scan">${icon('scan')} ${t('startScan')}</button></div></section>`;
}
function homeContent(): string {
  return `<section class="home-layout"><h1 tabindex="-1">${t('welcome')}<span>${esc(user?.name)}</span></h1><div class="choice-layout"><button class="choice-card choice-borrow" data-action="borrow"><span class="choice-label">${t('borrow')} ${icon('arrow')}</span><span class="choice-art"><img src="${toolbox}" alt=""></span></button><button class="choice-card choice-return" data-action="return"><span class="choice-label">${t('return')} ${icon('arrow')}</span><span class="choice-art"><img src="${mascot}" alt=""></span></button></div></section>`;
}
function dock(): string {
  return `<div class="action-dock"><strong id="selection-count"></strong><button class="primary-button" id="unlock-button" data-action="unlock">${icon('lock')} ${t('unlock')}</button></div>`;
}
function empty(title: string): string { return `<div class="empty-state">${icon('toolbox')}<p>${title}</p></div>`; }
function borrowContent(): string {
  const rows = tools.map(tool => {
    const name = localized(tool.name, tool.name_en), unit = localized(tool.unit, tool.unit_en);
    const description = localized(tool.description, tool.description_en);
    const quantity = quantities.get(tool.id) ?? 1, unavailable = tool.available_quantity < 1;
    return `<div class="tool-row${selected.has(tool.id) ? ' is-selected' : ''}${unavailable ? ' is-unavailable' : ''}" data-row="${tool.id}"><label class="tool-select"><input type="checkbox" class="tool-checkbox" value="${tool.id}" ${selected.has(tool.id) ? 'checked' : ''} ${unavailable ? 'disabled' : ''}><span class="check-mark" aria-hidden="true">${icon('check')}</span><span class="tool-art">${toolArt(tool)}</span><span class="tool-info"><strong>${esc(name)}</strong>${description ? `<span class="tool-description">${esc(description)}</span>` : ''}<span>${t('available')} ${tool.available_quantity} ${esc(unit)}</span></span></label><div class="quantity-control"><button data-action="decrease" data-id="${tool.id}" aria-label="${esc(name)}: ${t('decrease')}" ${unavailable || quantity <= 1 ? 'disabled' : ''}>−</button><input type="number" inputmode="numeric" min="1" max="${tool.available_quantity}" value="${quantity}" data-quantity="${tool.id}" aria-label="${esc(name)}: ${t('quantity')}" ${unavailable ? 'disabled' : ''}><button data-action="increase" data-id="${tool.id}" aria-label="${esc(name)}: ${t('increase')}" ${unavailable || quantity >= tool.available_quantity ? 'disabled' : ''}>+</button></div><span class="tool-unit">${esc(unit)}</span></div>`;
  }).join('');
  return `<section class="workspace">${heading(t('choose'))}<div class="list-scroll">${listLoading ? empty(t('loading')) : listError ? `<div class="empty-state"><button class="secondary-button" data-action="borrow">${t('retry')}</button></div>` : rows ? `<div class="tool-list">${rows}</div>` : empty(t('emptyTools'))}</div>${dock()}</section>`;
}
function returnContent(): string {
  const rows = loans.map(loan => {
    const date = new Date(loan.borrowed_at).toLocaleString(language === 'zh' ? 'zh-CN' : 'en-GB', {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
    return `<label class="loan-row" data-row="${loan.id}"><input class="tool-checkbox" type="checkbox" value="${loan.id}" ${selected.has(loan.id) ? 'checked' : ''}><span class="check-mark" aria-hidden="true">${icon('check')}</span><span class="tool-art">${toolArt(loan)}</span><span class="tool-info"><strong>${esc(localized(loan.tool_name, loan.tool_name_en))}</strong><span>${t('borrowedAt')} ${esc(date)}</span></span><strong>${loan.quantity} ${esc(localized(loan.unit, loan.unit_en))}</strong></label>`;
  }).join('');
  return `<section class="workspace">${heading(t('return'))}<div class="list-scroll">${listLoading ? empty(t('loading')) : listError ? `<div class="empty-state"><button class="secondary-button" data-action="return">${t('retry')}</button></div>` : rows ? `<div class="tool-list">${rows}</div>` : empty(t('emptyLoans'))}</div>${rows ? dock() : ''}</section>`;
}
function unlockContent(): string {
  const status = opening ? t('opening') : previewOpened ? t('opened') : t('unconfirmed');
  return `<section class="workspace unlock-workspace">${heading(mode === 'borrow' ? t('borrow') : t('return'))}<div class="unlock-layout${opening ? ' is-opening' : ''}${previewOpened ? ' is-open' : ''}"><div class="cabinet-visual" aria-hidden="true"><span class="cabinet-label">${esc(cabinet?.code)}</span><div class="cabinet-door"><img src="${flower}" alt=""><span class="cabinet-handle"></span></div></div><div class="unlock-copy"><h2 aria-live="polite">${status}</h2>${previewOpened ? `<p>${t('within')} <strong>—</strong> ${t('seconds')}</p><p>${t('autoLock')}</p>` : ''}<div class="unlock-actions"><button class="primary-button" data-action="finish" ${opening ? 'disabled' : ''}>${t('finish')}</button><button class="secondary-button" data-action="reopen" ${opening ? 'disabled' : ''}>${icon('refresh')} ${t('reopen')}</button></div></div></div></section>`;
}
function render(): void {
  if (adminMounted) return;
  document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
  document.title = `${t('hall')} · ${t('title')}${cabinet ? ` · ${cabinet.code}` : ''}`;
  app.className = `student-app${mobile ? ' phone-preview' : ''}`;
  app.dataset.view = view;
  const content = view === 'loading' ? empty(t('loading')) : view === 'scan' ? scanContent() : view === 'login' ? loginContent() : view === 'home' ? homeContent() : view === 'borrow' ? borrowContent() : view === 'return' ? returnContent() : view === 'unlock' ? unlockContent() : `<section class="scan-panel">${wolf()}<h1 tabindex="-1">${t('demoDone')}</h1><p>${t('previewOnly')}</p><button class="primary-button" data-action="home">${t('home')}</button></section>`;
  if (!app.querySelector('.page-shell')) {
    disposeAmbient?.();
    app.innerHTML = `<main class="page-shell"><div class="page-tools"></div><div class="preview-slot"></div><div class="page-content"></div></main><footer class="site-footer"><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2025433394号-1</a><a class="license-link" href="${esc(licenseHref(licenseSource()))}">License</a><a class="github-link" href="https://github.com/Hylfrd/tongtongsuo" target="_blank" rel="noopener noreferrer">${githubIcon}</a></footer>`;
    disposeAmbient = installAmbient(app);
  }
  const showHeader = !['loading', 'login', 'scan'].includes(view);
  const pageTools = app.querySelector<HTMLElement>('.page-tools')!;
  pageTools.hidden = !showHeader;
  pageTools.innerHTML = showHeader ? `<div class="session-identity"><div class="product-context"><span class="product-hall">${t('hall')}</span><span class="product-name">${t('title')}</span></div><div class="session-controls">${cabinet ? `<strong class="session-cabinet">${esc(cabinetLabel())}</strong>` : ''}</div></div><div class="session-actions">${languageButton()}${logoutButton()}</div>` : '';
  app.querySelector('.preview-slot')!.innerHTML = preview ? `<p class="preview-note">${t('preview')}</p>` : '';
  app.querySelector('.page-content')!.innerHTML = content;
  app.querySelector('.github-link')!.setAttribute('aria-label', t('source'));
  app.querySelector('.license-link')!.setAttribute('href', licenseHref(licenseSource()));
  const loginForm = document.querySelector<HTMLFormElement>('#login-form');
  loginForm?.addEventListener('submit', event => void login(event));
  loginForm?.addEventListener('input', event => { if (event.target instanceof HTMLInputElement) clearFieldError(event.target); });
  for (const kind of ['copy', 'cut', 'paste', 'drop', 'dragstart']) loginForm?.addEventListener(kind, event => {
    if (!(event.target instanceof HTMLInputElement)) return;
    event.preventDefault();
    if (kind === 'paste' || kind === 'drop') showToast(language === 'zh' ? '请手动输入账号和密码。' : 'Please type your account and password.', 'info');
  });
  updateSelection(); updateLoginButton();
}
async function move(next: View): Promise<void> {
  const changed = view !== next;
  view = next;
  render();
  const panel = app.querySelector<HTMLElement>('.page-content');
  panel?.getAnimations().forEach(animation => animation.cancel());
  if (changed && panel && !document.hidden && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    panel.animate([{opacity:.7,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}], {duration:360,easing:'cubic-bezier(.16,1,.3,1)'});
  }
  document.querySelector<HTMLElement>('h1')?.focus({preventScroll:true});
}
function updateSelection(): void {
  const count = document.querySelector('#selection-count');
  if (count) count.textContent = `${t('selected')} ${[...selected.values()].reduce((a,b) => a+b, 0)} ${t('items')}`;
  const button = document.querySelector<HTMLButtonElement>('#unlock-button');
  if (button) button.disabled = busy || listLoading || selected.size === 0;
}
function setBusy(value: boolean): void {
  busy = value;
  const fields = document.querySelector<HTMLFieldSetElement>('#login-form fieldset');
  if (fields) fields.disabled = value;
  document.querySelectorAll<HTMLButtonElement>('.language-button,.logout-button').forEach(button => button.disabled = value);
  updateLoginButton(); updateSelection();
}
function updateLoginButton(): void {
  const button = document.querySelector<HTMLButtonElement>('#login-button');
  const seconds = Math.max(0, Math.ceil((loginBlockedUntil - Date.now()) / 1000));
  if (button) { button.disabled = busy || seconds > 0; button.textContent = busy ? t('checking') : seconds ? `${t('cooldown')} ${seconds} ${t('second')}` : t('login'); }
  if (!seconds && cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = undefined; }
}
async function login(event: Event): Promise<void> {
  event.preventDefault();
  if (busy || preview || Date.now() < loginBlockedUntil) return;
  const username = document.querySelector<HTMLInputElement>('#username')!;
  const password = document.querySelector<HTMLInputElement>('#password')!;
  if (!validateForm(event.currentTarget as HTMLFormElement)) return;
  invalidateRequests();
  const ticket = requestScope.begin();
  setBusy(true);
  try {
    const response = await fetch('/api/auth/login', {method:'POST', credentials:'same-origin', cache:'no-store', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:username.value.trim(),password:password.value}), signal:AbortSignal.any([ticket.signal,AbortSignal.timeout(45000)])});
    ticket.check();
    password.value = '';
    if (!response.ok) {
      const retry = response.headers.get('Retry-After');
      if (retry && /^\d+$/.test(retry)) { loginBlockedUntil = Date.now() + Math.min(3600, Number(retry)) * 1000; clearInterval(cooldownTimer); cooldownTimer = setInterval(updateLoginButton, 1000); }
      let message = t('authFailed');
      try { const body = await response.json(); if (language === 'zh' && typeof body.detail?.message === 'string') message = body.detail.message; } catch { /* Fallback retained. */ }
      throw new Error(message);
    }
    loginBlockedUntil = 0;
    const identity = readIdentity(await response.json());
    ticket.check(); user = identity;
    await enterAccount(true);
  } catch (error) { if (ticket.current()) errorToast(error instanceof TypeError || error instanceof DOMException ? new Error(t('network')) : error); }
  finally { password.value = ''; if (ticket.current()) setBusy(false); ticket.release(); }
}
async function enterAccount(freshLogin = false): Promise<void> {
  if (shouldOpenAdmin(user?.role, location.pathname, freshLogin) && user) {
    if (!adminPath) { location.assign(mobileAdmin() ? '/admin?mobile=1' : '/admin'); return; }
    document.documentElement.lang = 'zh-CN';
    document.title = '正雅苑 · 工具箱管理';
    const {mountAdmin} = await import('./admin');
    disposeAmbient?.(); disposeAmbient = undefined;
    adminMounted = true; app.className = ''; delete app.dataset.view;
    await mountAdmin(app, user, async () => { await request('/api/auth/logout', {method:'POST'}); location.assign('/?signedOut=1'); });
  } else if (adminPath) { location.assign('/'); }
  else {
    const resume = new URLSearchParams(location.search).get('resume');
    if (!freshLogin && cabinet && (resume === 'borrow' || resume === 'return')) await loadList(resume, true);
    else await move(cabinet ? 'home' : 'scan');
  }
}
async function logout(): Promise<void> {
  if (busy || opening) return;
  invalidateRequests();
  const version = requestScope.version;
  setBusy(true);
  try {
    if (!preview) await request('/api/auth/logout', {method:'POST'});
    if (!requestScope.isCurrent(version)) return;
    user = null; tools = []; loans = []; selected.clear(); quantities.clear();
    setBusy(false); move(cabinet || adminPath ? 'login' : 'scan'); showToast(t('loggedOut'), 'success');
  } catch { if (requestScope.isCurrent(version)) { render(); showToast(t('logoutFailed'), 'error'); } }
  finally { if (requestScope.isCurrent(version)) setBusy(false); }
}
function previewTools(): Tool[] {
  return [['螺丝刀','Screwdriver','screwdriver'],['卷尺','Tape measure','tape'],['羊角锤','Claw hammer','hammer'],['尖嘴钳','Needle-nose pliers','pliers']].map(([name,name_en,toolIcon], index) => ({id:index+1,name,name_en,description:'',description_en:'',unit:'把',unit_en:'pc',icon:toolIcon,icon_data:null,available_quantity:3-loans.filter(loan=>loan.tool_id===index+1).reduce((n,l)=>n+l.quantity,0)}));
}
async function loadList(next: 'borrow' | 'return', immediate = false): Promise<void> {
  if (!user || !cabinet || listLoading) return;
  const version = requestScope.version;
  mode = next; selected.clear(); quantities.clear(); listLoading = true; listError = false;
  if (immediate) await move(next);
  const trigger = app.querySelector<HTMLButtonElement>(`[data-action="${next}"]`);
  if (trigger) { trigger.disabled = true; trigger.setAttribute('aria-busy','true'); }
  try {
    if (preview) { if (next === 'borrow') tools = previewTools(); }
    else if (next === 'borrow') {
      const result = await request<{items:Tool[]}>(endpoint('/tools'));
      if (requestScope.isCurrent(version)) tools = result.items;
    } else {
      const result = await request<{items:Loan[]}>(endpoint('/loans/me'));
      if (requestScope.isCurrent(version)) loans = result.items;
    }
  } catch (error) { if (requestScope.isCurrent(version)) { listError = true; errorToast(error); } }
  finally { if (requestScope.isCurrent(version)) { listLoading = false; if (user && (view === 'home' || view === next)) await move(next); } }
}
async function unlock(): Promise<void> {
  if (busy || opening || !user || !cabinet || !selected.size) return;
  const version = requestScope.version;
  opening = true; previewOpened = false; await move('unlock');
  if (preview) { demoTimer = setTimeout(() => { opening = false; previewOpened = true; render(); }, 950); return; }
  try {
    await request(endpoint('/unlock'), {method:'POST'});
    // An accepted request alone cannot confirm the physical lock state.
    showToast(t('unconfirmed'), 'info');
  } catch (error) { errorToast(error); }
  finally { if (requestScope.isCurrent(version)) { opening = false; if (view === 'unlock') render(); } }
}
function finish(): void {
  if (opening) return;
  if (preview && previewOpened) {
    if (mode === 'borrow') {
      for (const [id,quantity] of selected) { const tool = tools.find(item=>item.id===id)!; loans.push({id:Date.now()+id,tool_id:id,tool_name:tool.name,tool_name_en:tool.name_en,quantity,borrowed_at:new Date().toISOString(),unit:tool.unit,unit_en:tool.unit_en,icon:tool.icon,icon_data:tool.icon_data}); }
    } else loans = loans.filter(loan=>!selected.has(loan.id));
    selected.clear(); move('success');
  } else { selected.clear(); move('home'); }
}
function setQuantity(id: number, raw: number): void {
  const tool = tools.find(item=>item.id===id); if (!tool || tool.available_quantity < 1) return;
  const value = Math.min(tool.available_quantity, Math.max(1, Number.isFinite(raw) ? Math.trunc(raw) : 1));
  quantities.set(id,value); if (selected.has(id)) selected.set(id,value);
  const input = app.querySelector<HTMLInputElement>(`[data-quantity="${id}"]`); if (input) input.value = String(value);
  const minus = app.querySelector<HTMLButtonElement>(`[data-action="decrease"][data-id="${id}"]`); if (minus) minus.disabled = value <= 1;
  const plus = app.querySelector<HTMLButtonElement>(`[data-action="increase"][data-id="${id}"]`); if (plus) plus.disabled = value >= tool.available_quantity;
  updateSelection();
}
app.addEventListener('input', event => {
  if (adminMounted || busy || listLoading || !(event.target instanceof HTMLInputElement)) return;
  const input = event.target;
  if (input.dataset.quantity && Number.isFinite(input.valueAsNumber)) setQuantity(Number(input.dataset.quantity), input.valueAsNumber);
});
app.addEventListener('change', event => {
  if (adminMounted || busy || listLoading || !(event.target instanceof HTMLInputElement)) return;
  const input = event.target;
  if (input.dataset.quantity) { setQuantity(Number(input.dataset.quantity), input.valueAsNumber); return; }
  if (!input.matches('.tool-checkbox')) return;
  const id = Number(input.value);
  if (input.checked) selected.set(id, mode === 'borrow' ? quantities.get(id) ?? 1 : loans.find(loan=>loan.id===id)?.quantity ?? 1); else selected.delete(id);
  input.closest('[data-row]')?.classList.toggle('is-selected', input.checked); updateSelection();
});
app.addEventListener('click', event => {
  if (adminMounted || !(event.target instanceof Element)) return;
  const button = event.target.closest<HTMLButtonElement>('button[data-action]');
  if (!button || button.disabled || busy || opening) return;
  const action = button.dataset.action;
  if (action === 'language') {
    const username = document.querySelector<HTMLInputElement>('#username')?.value;
    language = language === 'zh' ? 'en' : 'zh';
    render(); const input = document.querySelector<HTMLInputElement>('#username'); if (input && username) input.value = username;
    document.querySelector<HTMLButtonElement>('.language-button')?.focus();
  } else if (action === 'scan') {
    button.disabled = true;
    void import('./scanner').then(({openCabinetScanner}) => openCabinetScanner(language, path => location.assign(path))).catch(errorToast).finally(() => { button.disabled = false; });
  } else if (action === 'preview-login' && preview) { user = {name:t('previewUser'),school_code:null}; move('home'); }
  else if (action === 'logout') void logout();
  else if (action === 'home' && user) { selected.clear(); move('home'); }
  else if (action === 'borrow' || action === 'return') void loadList(action);
  else if (action === 'unlock' || action === 'reopen') void unlock();
  else if (action === 'finish') finish();
  else if (action === 'increase' || action === 'decrease') { const id = Number(button.dataset.id); setQuantity(id, (quantities.get(id) ?? 1) + (action === 'increase' ? 1 : -1)); }
});
async function initialize(): Promise<void> {
  if (adminPath && mobileAdmin()) {
    disposeAmbient?.(); disposeAmbient = undefined;
    document.documentElement.lang = 'zh-CN'; document.title = '正雅苑 · 工具箱管理';
    app.className = 'admin-desktop-only'; app.innerHTML = '<p>请使用电脑访问</p>'; return;
  }
  if (location.pathname === '/license') { disposeAmbient?.(); disposeAmbient = undefined; const {mountLicense} = await import('./license'); mountLicense(app); return; }
  const query = new URLSearchParams(location.search);
  if (query.has('resume')) language = query.get('lang') === 'en' ? 'en' : 'zh';
  invalidateRequests();
  const ticket = requestScope.begin();
  view = 'loading'; cabinet = null; render();
  try {
    try {
      if (cabinetCode) { const result = await request<Cabinet>(endpoint()); ticket.check(); cabinet = result; }
    } catch (error) { ticket.check(); errorToast(error); }
    if (!preview) {
      try {
        const response = await fetch('/api/auth/me', {credentials:'same-origin',cache:'no-store',signal:AbortSignal.any([ticket.signal,AbortSignal.timeout(8000)])});
        ticket.check();
        if (response.ok) { const identity = readIdentity(await response.json()); ticket.check(); user = identity; }
        else if (response.status === 401) user = null;
        else showToast(t('network'), 'error');
      } catch { ticket.check(); showToast(t('network'), 'error'); }
    }
    ticket.check();
    if (user) await enterAccount(); else move(adminPath || cabinet ? 'login' : 'scan');
    if (adminPath && !user && new URLSearchParams(location.search).get('passwordChanged') === '1') { showToast(t('passwordChanged'), 'success'); history.replaceState(null,'',location.pathname); }
    if (new URLSearchParams(location.search).has('signedOut')) { showToast(t('loggedOut'), 'success'); history.replaceState(null,'',location.pathname); }
  } catch (error) { errorToast(error); }
  finally { ticket.release(); }
}
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  if (adminMounted) { location.reload(); return; }
  busy = false; opening = false; listLoading = false;
  if (loginBlockedUntil > Date.now()) cooldownTimer = setInterval(updateLoginButton, 1000);
  void initialize();
});
window.addEventListener('pagehide', () => { invalidateRequests(); const password = document.querySelector<HTMLInputElement>('#password'); if (password) password.value = ''; clearTimeout(demoTimer); clearInterval(cooldownTimer); });
void initialize();
