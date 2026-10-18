import './style.css';
import mascot from './assets/mascot.png';
import logo from './assets/zheng-logo.png';
import flower from './assets/flower.png';
import screwdriver from './assets/screwdriver.png';
import toolbox from './assets/toolbox.png';
import { icon, githubIcon, type IconName } from './icons';
import { showToast } from './toast';

type Identity = { name: string; school_code: string | null; role?: 'admin' };
type View = 'login' | 'home' | 'borrow' | 'return' | 'unlock' | 'success';
type Tool = { id: string; name: string; detail: string; symbol: IconName };
type Loan = { toolId: string; borrowedAt: Date };
const catalog: Tool[] = [
  { id: 'screwdriver', name: '螺丝刀', detail: '', symbol: 'key' },
  { id: 'tape', name: '卷尺', detail: '', symbol: 'tape' },
  { id: 'hammer', name: '羊角锤', detail: '', symbol: 'hammer' },
  { id: 'pliers', name: '尖嘴钳', detail: '', symbol: 'pliers' },
];
const preview = new URLSearchParams(location.search).get('preview') === '1';
const app = document.querySelector<HTMLDivElement>('#app')!;
let user: Identity | null = null;
let view: View = 'login';
let mode: 'borrow' | 'return' = 'borrow';
let busy = !preview;
let opening = false;
let notice = preview ? '' : '正在检查登录状态…';
let noticeError = false;
let selected = new Set<string>();
let loans: Loan[] = [];
let completedCount = 0;
let demoTimer: ReturnType<typeof setTimeout> | undefined;
let cooldownTimer: ReturnType<typeof setInterval> | undefined;
let loginBlockedUntil = 0;
let lockReference: string | null = null;
const scanMatch = location.pathname.match(/^\/scan\/([^/]+)\/?$/);
if (scanMatch) {
  try {
    const decoded = decodeURIComponent(scanMatch[1]);
    if (/^[A-Za-z0-9_-]{1,128}$/.test(decoded)) lockReference = decoded;
  } catch { /* Invalid scan links do not permit an unlock request. */ }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
function readIdentity(value: unknown): Identity {
  if (!isRecord(value) || !isRecord(value.user)) throw new Error('Invalid identity');
  const candidate = value.user;
  if (typeof candidate.name !== 'string' || !candidate.name.trim() ||
      (candidate.school_code !== null && typeof candidate.school_code !== 'string')) throw new Error('Invalid identity');
  return { name: candidate.name, school_code: candidate.school_code, ...(candidate.role === 'admin' ? { role: 'admin' as const } : {}) };
}
async function responseMessage(response: Response, fallback: string): Promise<string> {
  try {
    const value: unknown = await response.json();
    if (isRecord(value) && isRecord(value.detail) && typeof value.detail.message === 'string') return value.detail.message;
  } catch { /* Proxies can return an HTML error page. */ }
  if (response.status === 429) return '尝试过于频繁，请稍后再试。';
  return fallback;
}
function setNotice(message: string, error = false): void {
  if (error) {
    showToast(message, 'error');
    message = '';
    error = false;
  }
  notice = message;
  noticeError = error;
  const element = document.querySelector<HTMLElement>('#message');
  if (element) {
    element.textContent = message;
    element.classList.toggle('is-error', error);
    element.hidden = !message;
  }
}
function updateLoginButton(): void {
  const button = document.querySelector<HTMLButtonElement>('#login-button');
  const seconds = Math.max(0, Math.ceil((loginBlockedUntil - Date.now()) / 1000));
  if (button) {
    button.disabled = busy || seconds > 0;
    if (busy) button.textContent = '正在验证…';
    else if (seconds) button.textContent = `稍后重试（${seconds} 秒）`;
    else button.innerHTML = `登录 ${icon('arrow')}`;
  }
  if (!seconds && cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = undefined; }
}
function acceptLoginCooldown(response: Response): void {
  const retry = response.headers.get('Retry-After');
  if (!retry || !/^\d+$/.test(retry)) return;
  const seconds = Math.min(3600, Number(retry));
  if (seconds <= 0) return;
  loginBlockedUntil = Date.now() + seconds * 1000;
  if (cooldownTimer) clearInterval(cooldownTimer);
  cooldownTimer = setInterval(updateLoginButton, 1000);
  updateLoginButton();
}
function move(next: View): void {
  view = next;
  notice = '';
  noticeError = false;
  render();
  document.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'instant' });
}
function art(): string {
  return `<div class="art-stage" aria-hidden="true"><div class="art-orbit"></div><img class="art-flower" src="${flower}" alt=""><img class="art-mascot" src="${mascot}" alt=""><img class="art-toolbox" src="${toolbox}" alt=""><img class="art-screwdriver" src="${screwdriver}" alt=""></div>`;
}
function toolArt(tool: Tool): string {
  return tool.id === 'screwdriver' ? `<img src="${screwdriver}" alt="" class="tool-image">` : icon(tool.symbol);
}
function loginContent(): string {
  return `<div class="login-layout"><section class="login-story"><h1 class="story-heading" tabindex="-1">正雅苑<br>共享工具箱</h1>${art()}</section><section class="login-panel" aria-labelledby="login-title"><div class="panel-heading"><span class="panel-symbol">${icon('toolbox')}</span><h2 id="login-title">登录</h2></div>${preview ? `<div class="preview-entry"><p>无需学校账号，可预览借还流程。</p><button class="primary-button" data-action="demo-login">进入体验 ${icon('arrow')}</button></div>` : `<form id="login-form" method="post" action="/api/auth/login" aria-describedby="privacy-description"><fieldset id="login-fields" ${busy ? 'disabled' : ''}><div class="field"><label for="username">登录账号</label><div class="input-wrap">${icon('user')}<input id="username" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" maxlength="128" placeholder="学校账号 / 管理员账号" required></div></div><div class="field"><label for="password">密码</label><div class="input-wrap">${icon('key')}<input id="password" name="password" type="password" autocomplete="current-password" maxlength="256" placeholder="请输入密码" required></div></div><button class="primary-button" id="login-button" type="submit">登录 ${icon('arrow')}</button></fieldset></form>`}<p class="form-message" id="message" role="status" aria-live="polite"></p><div class="privacy-note"><p id="privacy-description">${icon('lock')} 不保存明文密码。</p><details><summary>身份验证与隐私说明</summary><p>这是正雅苑共享工具箱项目页面。学生账号由学校验证，管理员账号由本站验证。学生账号密码仅临时用于向学校验证身份，会话仅保留姓名和学校人员编号；管理员密码以不可逆哈希校验，不会发送至学校。身份验证成功不代表已获得开柜权限；目前实际借还及开锁服务尚未接入。</p></details></div></section></div>`;
}
function homeContent(): string {
  return `<section class="home-layout"><div class="page-intro"><h1 class="welcome-heading" tabindex="-1">欢迎，<span data-user-name></span>。</h1></div><div class="choice-layout"><button class="choice-card choice-borrow" data-action="borrow"><span class="choice-content"><span class="choice-index">01</span><span class="choice-title">借用工具</span><span class="choice-arrow">${icon('arrow')}</span></span><span class="choice-art"><img src="${toolbox}" alt=""></span></button><button class="choice-card choice-return" data-action="return"><span class="choice-content"><span class="choice-index">02</span><span class="choice-title">归还工具</span><span class="choice-arrow">${icon('arrow')}</span></span><span class="choice-art"><img src="${mascot}" alt=""></span></button></div><p class="section-note">${icon('info')} ${preview ? '演示借还记录只在当前页面有效，刷新后清空。' : '工具箱正在准备中，目前可登录和预选，实际借还暂未开放。'}</p><p class="form-message" id="message" role="status" aria-live="polite"></p></section>`;
}
function workspaceHeader(title: string, copy: string): string {
  return `<div class="workspace-heading"><button class="back-button" data-action="home" aria-label="返回借还选择">${icon('back')}</button><div><span class="step-label">${mode === 'borrow' ? '借用工具' : '归还工具'} / 01</span><h1 tabindex="-1">${title}</h1><p>${copy}</p></div></div>`;
}
function actionDock(label: string): string {
  return `<div class="action-dock"><div class="selection-summary"><strong id="selection-count">已选 0 件</strong><span>${preview ? '仅演示，不会开启真实柜门' : '实际开锁服务尚未接入'}</span></div><button class="primary-button" id="unlock-button" data-action="unlock" disabled>${icon('lock')} ${label}</button></div>`;
}
function borrowContent(): string {
  return `<section class="workspace">${workspaceHeader('选择工具', '勾选本次借用的物品。')}<p class="section-note">${icon('info')} 以下为设计示例，品类与实际库存待确认。</p><div class="catalog-grid">${catalog.map(tool => {
    const borrowed = preview && loans.some(loan => loan.toolId === tool.id);
    return `<label class="tool-card${borrowed ? ' is-unavailable' : ''}"><input class="tool-checkbox" type="checkbox" value="${tool.id}" ${selected.has(tool.id) ? 'checked' : ''} ${borrowed ? 'disabled' : ''}><span class="selection-mark">${icon('check')}</span><span class="tool-art tool-art-${tool.id}">${toolArt(tool)}</span><span class="tool-info"><span class="tool-name">${tool.name}</span><span class="tool-detail">${borrowed ? '本次已借出' : tool.detail}</span></span></label>`;
  }).join('')}</div><p class="form-message" id="message" role="status" aria-live="polite"></p>${actionDock('开锁取用')}</section>`;
}
function returnContent(): string {
  const rows = preview ? loans.map(loan => {
    const tool = catalog.find(item => item.id === loan.toolId)!;
    const date = loan.borrowedAt.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
    return `<label class="loan-row"><input class="tool-checkbox" type="checkbox" value="${tool.id}" ${selected.has(tool.id) ? 'checked' : ''}><span class="loan-icon">${toolArt(tool)}</span><span class="loan-info"><span class="tool-name">${tool.name}</span><span class="loan-date">${icon('clock')} 演示借用时间 ${date}</span></span><span class="selection-mark">${icon('check')}</span></label>`;
  }).join('') : '';
  return `<section class="workspace">${workspaceHeader('归还工具', '勾选本次归还的物品。')}${rows ? `<div class="loan-list">${rows}</div>` : `<div class="empty-state">${icon('toolbox')}<h2>${preview ? '还没有借用的工具' : '借还记录尚未接入'}</h2><p>${preview ? '先去借用页体验一下，再回来归还。' : '暂时无法查询你的借用记录，实际归还功能尚未开放。'}</p><button class="secondary-button" data-action="${preview ? 'borrow' : 'home'}">${preview ? '去看看工具' : '返回借还选择'} ${icon('arrow')}</button></div>`}<p class="form-message" id="message" role="status" aria-live="polite"></p>${rows ? actionDock('开锁归还') : ''}</section>`;
}
function unlockContent(): string {
  return `<section class="unlock-layout ${opening ? 'is-opening' : 'is-open'}"><div class="cabinet-visual" aria-hidden="true"><div class="cabinet-top">正雅苑 · 共享工具箱</div><div class="cabinet-door"><img src="${flower}" alt=""><span class="cabinet-handle"></span></div><span class="cabinet-foot foot-left"></span><span class="cabinet-foot foot-right"></span></div><div class="unlock-copy"><span class="status-pill">${icon(opening ? 'lock' : 'open')} ${opening ? '正在开锁 · 演示' : '已开锁 · 演示'}</span><h1 tabindex="-1">${opening ? '正在开锁' : mode === 'borrow' ? '已开锁' : '已开锁'}</h1><p>${opening ? '正在演示柜门打开过程，不会发送真实开锁指令。' : `请在 <strong class="seconds-placeholder">—</strong> 秒内开柜。<br>${mode === 'borrow' ? '取出勾选的工具后，请关好柜门。' : '放回勾选的工具后，请关好柜门。'}`}</p><p class="section-note">关门自动落锁 · 开柜时限待配置</p><div class="unlock-actions"><button class="primary-button" data-action="finish" ${opening ? 'disabled' : ''}>${icon('check')} 完成</button><button class="secondary-button" data-action="reopen" ${opening ? 'disabled' : ''}>${icon('refresh')} 重新打开柜门</button></div><p class="form-message" id="message" role="status" aria-live="polite"></p></div></section>`;
}
function successContent(): string {
  return `<section class="success-layout"><div class="success-burst" aria-hidden="true"><span style="--n:0"></span><span style="--n:1"></span><span style="--n:2"></span><span style="--n:3"></span><span style="--n:4"></span><span style="--n:5"></span><span style="--n:6"></span><span style="--n:7"></span></div><div class="success-symbol">${icon('check')}</div><h1 tabindex="-1">${mode === 'borrow' ? '借用完成' : '归还完成'}</h1><p>已${mode === 'borrow' ? '演示借用' : '演示归还'} ${completedCount} 件工具。${mode === 'borrow' ? '' : ''}</p><p class="section-note">仅更新本页演示记录，未产生真实借还记录。</p><button class="primary-button" data-action="home">返回首页 ${icon('arrow')}</button><p class="form-message" id="message" role="status" aria-live="polite"></p></section>`;
}
function render(): void {
  app.dataset.view = view;
  app.innerHTML = `${preview ? '<div class="preview-banner" role="note"><strong>设计演示</strong><span>虚拟身份与借还记录 · 不会验证学校账号或操作真实柜锁</span></div>' : ''}<header class="site-header"><a class="brand" href="${preview ? '/?preview=1' : '/'}" aria-label="正雅苑共享工具箱首页"><img class="brand-mark" src="${logo}" alt="正雅苑标志"><span><span class="brand-name">正雅苑<span class="brand-divider"> / </span>共享工具箱</span><span class="brand-subtitle">ZHENGYA</span></span></a><div class="header-actions">${user ? `<button class="text-button" data-action="logout" ${busy ? 'disabled' : ''}>${icon('logout')} 退出</button>` : ''}</div></header><main class="page-shell">${view === 'login' ? loginContent() : view === 'home' ? homeContent() : view === 'borrow' ? borrowContent() : view === 'return' ? returnContent() : view === 'unlock' ? unlockContent() : successContent()}</main><footer class="site-footer"><span>正雅苑 · 共享工具箱</span><div class="footer-links"><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2025433394号-1</a><a class="github-link" href="https://github.com/Hylfrd/tongtongsuo" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 查看项目源码">${githubIcon}<span>GitHub</span></a></div></footer>`;
  document.querySelectorAll<HTMLElement>('[data-user-name]').forEach(element => { element.textContent = user?.name ?? ''; });
  setNotice(notice, noticeError);
  updateSelection();
  updateLoginButton();
  document.querySelector<HTMLFormElement>('#login-form')?.addEventListener('submit', event => { void login(event); });
}
function updateSelection(): void {
  const count = document.querySelector<HTMLElement>('#selection-count');
  if (count) count.textContent = `已选 ${selected.size} 件`;
  const button = document.querySelector<HTMLButtonElement>('#unlock-button');
  if (button) button.disabled = selected.size === 0 || busy;
}
function setBusy(value: boolean): void {
  busy = value;
  const fields = document.querySelector<HTMLFieldSetElement>('#login-fields');
  if (fields) fields.disabled = value;
  document.querySelectorAll<HTMLButtonElement>('[data-action]').forEach(button => { button.disabled = value; });
  document.querySelectorAll<HTMLInputElement>('.tool-checkbox').forEach(input => {
    input.disabled = value || (view === 'borrow' && preview && loans.some(loan => loan.toolId === input.value));
  });
  document.querySelector<HTMLFormElement>('#login-form')?.setAttribute('aria-busy', String(value));
  updateSelection();
  updateLoginButton();
}
async function login(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (busy || preview || Date.now() < loginBlockedUntil) return;
  const username = document.querySelector<HTMLInputElement>('#username')!;
  const password = document.querySelector<HTMLInputElement>('#password')!;
  const button = document.querySelector<HTMLButtonElement>('#login-button')!;
  if (!username.value.trim()) { setNotice('请输入学校账号。', true); username.focus(); return; }
  setBusy(true);
  button.textContent = '正在验证…';
  setNotice('正在验证账号，请稍候。');
  try {
    const response = await fetch('/api/auth/login', { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: username.value.trim(), password: password.value }), signal: AbortSignal.timeout(45000) });
    password.value = '';
    if (!response.ok) {
      acceptLoginCooldown(response);
      setNotice(await responseMessage(response, '账号或密码未通过验证，请检查后重试。'), true);
      return;
    }
    loginBlockedUntil = 0;
    if (cooldownTimer) { clearInterval(cooldownTimer); cooldownTimer = undefined; }
    user = readIdentity(await response.json());
    username.value = '';
    busy = false;
    move('home');
  } catch {
    setNotice('未能确认登录结果，请检查网络后重试。密码已清空，请重新输入。', true);
  } finally {
    password.value = '';
    button.innerHTML = `登录 ${icon('arrow')}`;
    setBusy(false);
  }
}
async function logout(): Promise<void> {
  if (busy || opening) return;
  setBusy(true);
  try {
    if (!preview) {
      const response = await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error('Logout failed');
    }
    user = null;
    loans = [];
    selected.clear();
    busy = false;
    move('login');
    setNotice('已退出共享工具箱。');
  } catch { setNotice('暂时无法确认退出，请检查网络后再次退出。', true); }
  finally { setBusy(false); }
}
async function unlock(): Promise<void> {
  if (busy || !user || selected.size === 0) return;
  if (preview) { startDemoOpening(); return; }
  if (!lockReference) { setNotice('请通过工具柜上的二维码进入。实际开锁及借还记录服务尚未开放。', true); return; }
  setBusy(true);
  setNotice('正在检查开锁服务…');
  try {
    const response = await fetch(`/api/locks/${encodeURIComponent(lockReference)}/unlock`, { method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000) });
    if (response.status === 401) {
      user = null;
      selected.clear();
      busy = false;
      move('login');
      setNotice('登录状态已失效，请重新登录。', true);
    } else {
      // A successful HTTP status does not prove that the physical door opened.
      setNotice(response.ok ? '尚未接入柜锁状态确认，无法确认开锁；未生成借还记录。' : await responseMessage(response, '开锁服务暂不可用，未生成借还记录。'), true);
    }
  } catch { setNotice('无法确认柜锁状态，请勿假定已开锁；未生成借还记录。', true); }
  finally { setBusy(false); }
}
function startDemoOpening(): void {
  if (!preview || opening || busy || !user || !selected.size) return;
  opening = true;
  move('unlock');
  document.querySelector<HTMLButtonElement>('[data-action="logout"]')?.setAttribute('disabled', '');
  demoTimer = setTimeout(() => {
    opening = false;
    render();
    document.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
  }, 1100);
}
function finishDemo(): void {
  if (!preview || view !== 'unlock' || opening || busy || !user) return;
  completedCount = selected.size;
  if (mode === 'borrow') {
    const borrowedAt = new Date();
    for (const toolId of selected) if (!loans.some(loan => loan.toolId === toolId)) loans.push({ toolId, borrowedAt });
  } else {
    loans = loans.filter(loan => !selected.has(loan.toolId));
  }
  selected.clear();
  move('success');
}
app.addEventListener('change', event => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement) || !input.matches('.tool-checkbox') || busy) return;
  if (input.checked) selected.add(input.value); else selected.delete(input.value);
  updateSelection();
  setNotice('');
});
app.addEventListener('click', event => {
  if (!(event.target instanceof Element)) return;
  const button = event.target.closest<HTMLButtonElement>('button[data-action]');
  if (!button || button.disabled || busy || opening) return;
  const action = button.dataset.action;
  if (action === 'demo-login' && preview) { user = { name: '苑舍同学', school_code: null }; move('home'); }
  else if (action === 'logout') void logout();
  else if (action === 'home' && user) { selected.clear(); move('home'); }
  else if ((action === 'borrow' || action === 'return') && user) { mode = action; selected.clear(); move(action); }
  else if (action === 'unlock') void unlock();
  else if (action === 'reopen') startDemoOpening();
  else if (action === 'finish') finishDemo();
});
async function restoreSession(): Promise<void> {
  if (preview) return;
  try {
    const response = await fetch('/api/auth/me', { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (response.ok) { user = readIdentity(await response.json()); busy = false; move('home'); }
    else if (response.status === 401) setNotice('');
    else setNotice('暂时无法检查登录状态，可尝试重新登录。', true);
  } catch { setNotice('暂时无法检查登录状态，请确认网络后再登录。', true); }
  finally { setBusy(false); }
}
window.addEventListener('pagehide', () => {
  const password = document.querySelector<HTMLInputElement>('#password');
  if (password) password.value = '';
  if (demoTimer) clearTimeout(demoTimer);
  if (cooldownTimer) clearInterval(cooldownTimer);
});
render();
void restoreSession();
