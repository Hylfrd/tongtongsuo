import './member-settings.css';
import { showToast } from './toast';
import { licenseHref } from './license-navigation';

type Mode = 'sso' | 'roster';
type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
type Member = {id: number; name: string; school_code: string; room_number: string | null; first_login_at: string | null; last_login_at: string | null};
type MemberPage = {items: Member[]; total: number; page: number; page_size: number};
const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
const message = (error: unknown): string => error instanceof Error ? error.message : '请求失败，请重试。';

export function createMembersPanel(request: Request) {
  const params = new URLSearchParams(location.search);
  const requestedSource = params.get('section') === 'members' ? params.get('source') : null;
  let source: Mode = requestedSource === 'roster' ? 'roster' : 'sso';
  let sourceChosen = requestedSource === 'roster' || requestedSource === 'sso';
  let query = params.get('section') === 'members' ? (params.get('q') ?? '').slice(0,120) : '';
  let page = params.get('section') === 'members' ? Math.min(1000000, Math.max(1, Number.parseInt(params.get('page') ?? '1',10) || 1)) : 1;
  let mode: Mode = 'sso', savedMode: Mode = 'sso';
  let active = false, disposed = false, loaded = false, serial = 0;
  let modeRevision = 0, settingsReadSerial = 0;
  let initializing: Promise<boolean> | null = null, pendingSave: Promise<void> | null = null;
  const pageSize = 50;
  const element = document.createElement('section');
  element.className = 'adm-members-panel';
  element.innerHTML = `<div class="adm-member-mode-row"><h2 id="member-login-mode-label">登录方式</h2><label class="adm-filter adm-member-mode"><select name="login_mode" aria-labelledby="member-login-mode-label" disabled><option value="sso">使用 SSO 登录</option><option value="roster">使用宿舍号和学号登录</option></select></label></div><div class="adm-section-heading adm-member-heading"><h2>成员列表 <span data-member-count></span></h2><div class="adm-member-actions"><a class="adm-text" href="/api/admin/members/template.xlsx" download="正雅苑成员导入模板.xlsx">下载导入模板</a><button type="button" class="adm-outline" data-import>导入 Excel</button><input type="file" accept=".xlsx" data-import-file hidden aria-label="导入成员 Excel"></div></div><div class="adm-member-toolbar"><form class="adm-member-search" novalidate><label class="adm-filter">搜索成员<input type="search" name="q" placeholder="姓名、学号或房号" maxlength="120" value="${escape(query)}"></label><button type="submit" class="adm-button">查询</button></form><label class="adm-filter adm-member-source">名单来源<select name="member_source"><option value="roster">导入名单</option><option value="sso">SSO 登录</option></select></label></div><div data-member-results aria-live="polite"></div><nav class="adm-member-pagination" aria-label="成员列表分页"></nav>`;
  const modeSelect = element.querySelector<HTMLSelectElement>('[name="login_mode"]')!;
  const sourceSelect = element.querySelector<HTMLSelectElement>('[name="member_source"]')!;
  const form = element.querySelector<HTMLFormElement>('.adm-member-search')!;
  const results = element.querySelector<HTMLElement>('[data-member-results]')!;
  const pagination = element.querySelector<HTMLElement>('.adm-member-pagination')!;
  sourceSelect.value = source;

  async function save(): Promise<void> {
    if (pendingSave) { await pendingSave; if (!disposed && mode !== savedMode) await save(); return; }
    if (disposed || savedMode === mode) return;
    const selected = mode;
    pendingSave = (async () => {
      try {
        const result = await request<{mode: Mode}>('/api/admin/login-settings', {method:'PUT', body:JSON.stringify({mode:selected})});
        if (disposed) return;
        savedMode = result.mode;
        if (mode === selected) showToast('登录方式已保存。', 'success');
      } catch (error) {
        if (disposed) return;
        showToast(message(error), 'error');
        if (mode === selected) { mode = savedMode; modeSelect.value = mode; }
      }
    })();
    await pendingSave;
    pendingSave = null;
    if (mode !== savedMode && !disposed) await save();
  }

  function updateUrl(): void {
    if (!active || disposed || new URLSearchParams(location.search).get('section') !== 'members') return;
    const url = new URL(location.href);
    url.search = new URLSearchParams({section:'members',source,...(query ? {q:query} : {}),...(page > 1 ? {page:String(page)} : {})}).toString();
    history.replaceState(null,'',url);
    element.closest('.admin-shell')?.querySelector('.adm-license')?.setAttribute('href',licenseHref(url.pathname+url.search));
  }
  function date(value: string | null): string { return value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '—'; }
  function loanLink(member: Member): string {
    return `/admin?${new URLSearchParams({section:'loans',q:member.school_code,school_code:member.school_code,status:'all',sort:'borrowed_at',direction:'desc'})}`;
  }
  async function loadMembers(): Promise<void> {
    if (!active || disposed) return;
    const version = ++serial;
    results.setAttribute('aria-busy','true');
    if (!results.innerHTML) results.innerHTML = '<div class="adm-loading">正在读取成员…</div>';
    updateUrl();
    try {
      const result = await request<MemberPage>(`/api/admin/members?${new URLSearchParams({source,q:query,page:String(page),page_size:String(pageSize)})}`);
      if (disposed || !active || version !== serial) return;
      const maxPage = Math.max(1,Math.ceil(result.total/pageSize));
      if (page > maxPage) { page = maxPage; await loadMembers(); return; }
      element.querySelector('[data-member-count]')!.textContent = `${result.total} 人`;
      results.innerHTML = result.items.length ? `<div class="adm-table-scroll" tabindex="0" role="region" aria-label="成员列表"><table class="adm-table adm-member-table"><thead><tr><th>姓名</th><th>学号</th><th>房号</th><th>首次登录</th><th>最近登录</th><th>借还记录</th></tr></thead><tbody>${result.items.map(member => `<tr><td><strong>${escape(member.name)}</strong></td><td>${escape(member.school_code)}</td><td>${escape(member.room_number || '—')}</td><td>${escape(date(member.first_login_at))}</td><td>${escape(date(member.last_login_at))}</td><td><a class="adm-text" href="${escape(loanLink(member))}">查看借阅记录</a></td></tr>`).join('')}</tbody></table></div>` : `<div class="adm-empty"><h3>${query ? '没有匹配的成员' : '暂无成员'}</h3>${query ? '' : `<p>${source === 'roster' ? '导入成员 Excel 后会显示在这里。' : '成员首次使用 SSO 登录后会显示在这里。'}</p>`}</div>`;
      pagination.innerHTML = result.total > pageSize ? `<button type="button" class="adm-outline" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${page} / ${maxPage} 页</span><button type="button" class="adm-outline" data-page="${page + 1}" ${page >= maxPage ? 'disabled' : ''}>下一页</button>` : '';
      pagination.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(button => button.addEventListener('click', () => { page = Number(button.dataset.page); void loadMembers(); }));
    } catch (error) {
      if (!disposed && active && version === serial) {
        element.querySelector('[data-member-count]')!.textContent = '';
        results.innerHTML = '<div class="adm-empty"><h3>未能读取成员</h3><button type="button" class="adm-text" data-reload>重新读取</button></div>';
        pagination.innerHTML = '';
        results.querySelector('[data-reload]')!.addEventListener('click',()=>void refresh());
        showToast(message(error), 'error');
      }
    } finally { if (!disposed && version === serial) results.removeAttribute('aria-busy'); }
  }

  async function initialize(): Promise<boolean> {
    if (disposed) return false;
    if (loaded) return true;
    if (initializing) return initializing;
    initializing = (async () => {
      try {
        const result = await request<{mode: Mode}>('/api/admin/login-settings');
        if (disposed) return false;
        mode = savedMode = result.mode;
        modeSelect.value = mode;
        modeSelect.disabled = false;
        if (!sourceChosen) source = mode;
        sourceSelect.value = source;
        loaded = true;
        return true;
      } catch (error) {
        if (!disposed && active) showToast(message(error), 'error');
        return false;
      } finally { initializing = null; }
    })();
    return initializing;
  }
  async function refresh(): Promise<void> {
    if (disposed || !active) return;
    if (!loaded) {
      await initialize();
    } else if (!pendingSave) {
      const revision = modeRevision, readSerial = ++settingsReadSerial;
      try {
        const result = await request<{mode: Mode}>('/api/admin/login-settings');
        if (!disposed && revision === modeRevision && readSerial === settingsReadSerial && !pendingSave) {
          mode = savedMode = result.mode;
          modeSelect.value = mode;
        }
      } catch (error) {
        if (!disposed && active && readSerial === settingsReadSerial) showToast(message(error), 'error');
      }
    }
    if (active && !disposed) await loadMembers();
  }
  async function importFile(input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0];
    input.value = '';
    if (!file || disposed) return;
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 2 * 1024 * 1024) {
      showToast('请选择不超过 2 MB 的 .xlsx 文件。', 'error');
      return;
    }
    const button = element.querySelector<HTMLButtonElement>('[data-import]')!;
    button.disabled = true;
    button.textContent = '正在导入…';
    try {
      const result = await request<{added: number; updated: number; total: number}>('/api/admin/members/import', {
        method:'POST', body:file, headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},
        signal:AbortSignal.timeout(60000),
      });
      if (disposed) return;
      showToast(`导入完成：新增 ${result.added} 人，更新 ${result.updated} 人。`, 'success');
      sourceChosen = true; source = 'roster'; sourceSelect.value = source; page = 1;
      await loadMembers();
    } catch (error) {
      if (!disposed) showToast(message(error), 'error');
    } finally {
      if (!disposed) { button.disabled = false; button.textContent = '导入 Excel'; }
    }
  }
  modeSelect.addEventListener('change', () => { modeRevision++; mode = modeSelect.value as Mode; void save(); });
  sourceSelect.addEventListener('change', () => { sourceChosen = true; source = sourceSelect.value as Mode; page = 1; void loadMembers(); });
  form.addEventListener('submit', event => {
    event.preventDefault(); query = (form.elements.namedItem('q') as HTMLInputElement).value.trim(); page = 1; void loadMembers();
  });
  element.querySelector('[data-import]')!.addEventListener('click', () => element.querySelector<HTMLInputElement>('[data-import-file]')!.click());
  element.querySelector('[data-import-file]')!.addEventListener('change', event => void importFile(event.target as HTMLInputElement));
  return {
    mount(target: Element): void { if (disposed) return; active = true; target.append(element); void refresh(); },
    refresh,
    leave(): void { active = false; serial++; results.removeAttribute('aria-busy'); },
    dispose(): void { disposed = true; active = false; serial++; },
  };
}
