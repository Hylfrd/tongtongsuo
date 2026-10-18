import './member-settings.css';
import { icon, githubIcon } from './icons';
import { showToast } from './toast';
import { licenseHref } from './license-navigation';
import { installAmbient } from './ambient';

type Mode = 'sso' | 'roster';
type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
type Member = {id: number; name: string; school_code: string; room_number: string | null; first_login_at: string | null; last_login_at: string | null};
type MemberPage = {items: Member[]; total: number; page: number; page_size: number};
const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]!));
const message = (error: unknown): string => error instanceof Error ? error.message : '请求失败，请重试。';

export function createMemberSettings(request: Request) {
  const element = document.createElement('section');
  element.className = 'adm-login-settings';
  element.setAttribute('aria-label', '登录方式');
  let loaded = false, loading = false, disposed = false;
  let pendingSave: Promise<void> | null = null;
  let mode: Mode = 'sso', savedMode: Mode = 'sso';
  element.innerHTML = '<div class="adm-loading">正在读取登录设置…</div>';

  function updateLinks(): void {
    element.querySelector<HTMLAnchorElement>('[data-members-link]')!.href = `/admin?section=members&source=${mode}`;
  }
  async function save(): Promise<void> {
    if (pendingSave) { await pendingSave; return save(); }
    if (disposed || savedMode === mode) return;
    const selected = mode;
    pendingSave = (async () => {
      try {
        const result = await request<{mode: Mode}>('/api/admin/login-settings', {method:'PUT', body:JSON.stringify({mode:selected})});
        savedMode = result.mode;
        if (!disposed && mode === selected) showToast('登录方式已保存。', 'success');
      } catch (error) {
        if (!disposed) {
          showToast(message(error), 'error');
          if (mode === selected) {
            mode = savedMode;
            element.querySelector<HTMLSelectElement>('[name="login_mode"]')!.value = mode;
            updateLinks();
          }
        }
      }
    })();
    await pendingSave;
    pendingSave = null;
    if (mode !== savedMode && !disposed) await save();
  }
  async function importFile(input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
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
      if (!disposed) showToast(`导入完成：新增 ${result.added} 人，更新 ${result.updated} 人。`, 'success');
    } catch (error) {
      if (!disposed) showToast(message(error), 'error');
    } finally {
      button.disabled = false;
      button.textContent = '导入 Excel';
    }
  }
  function render(): void {
    element.innerHTML = `<h2>登录方式</h2><label class="adm-field adm-login-mode">登录验证<select name="login_mode"><option value="sso" ${mode === 'sso' ? 'selected' : ''}>使用 SSO 登录</option><option value="roster" ${mode === 'roster' ? 'selected' : ''}>使用宿舍号和学号登录</option></select></label><div class="adm-member-actions"><a class="adm-outline" data-members-link href="/admin?section=members&source=${mode}">查看成员 ${icon('arrow')}</a><div data-roster-actions><a class="adm-text" href="/api/admin/members/template.xlsx" download="正雅苑成员导入模板.xlsx">下载导入模板</a><button type="button" class="adm-outline" data-import>导入 Excel</button><input type="file" accept=".xlsx" data-import-file hidden aria-label="导入成员 Excel"></div></div>`;
    element.querySelector<HTMLSelectElement>('[name="login_mode"]')!.addEventListener('change', event => {
      mode = (event.target as HTMLSelectElement).value as Mode;
      updateLinks();
      void save();
    });
    element.querySelector('[data-members-link]')!.addEventListener('click', async event => {
      const click = event as MouseEvent;
      if (click.button !== 0 || click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return;
      event.preventDefault();
      await save();
      if (!disposed) location.assign(`/admin?section=members&source=${mode}`);
    });
    element.querySelector('[data-import]')!.addEventListener('click', () => element.querySelector<HTMLInputElement>('[data-import-file]')!.click());
    element.querySelector('[data-import-file]')!.addEventListener('change', event => void importFile(event.target as HTMLInputElement));
  }
  async function load(): Promise<void> {
    if (loaded || loading || disposed) return;
    loading = true;
    try {
      const result = await request<{mode: Mode}>('/api/admin/login-settings');
      if (disposed) return;
      mode = savedMode = result.mode;
      loaded = true;
      render();
    } catch (error) {
      if (!disposed) {
        element.innerHTML = '<button type="button" class="adm-text" data-reload>重新读取登录设置</button>';
        element.querySelector('[data-reload]')!.addEventListener('click', () => void load());
        showToast(message(error), 'error');
      }
    } finally { loading = false; }
  }
  return {
    mount(target: Element): void { target.append(element); void load(); },
    dispose(): void { disposed = true; },
  };
}

export async function mountMembers(root: HTMLElement, request: Request): Promise<void> {
  const params = new URLSearchParams(location.search);
  const source: Mode = params.get('source') === 'roster' ? 'roster' : 'sso';
  let query = (params.get('q') ?? '').slice(0,120);
  let page = Math.min(1000000, Math.max(1, Number.parseInt(params.get('page') ?? '1',10) || 1));
  let serial = 0, disposed = false;
  const pageSize = 50;
  root.innerHTML = `<div class="admin-shell"><header class="adm-sidebar adm-member-banner"><h1>成员列表</h1><a class="adm-outline" href="/admin?section=other">${icon('back')}返回</a></header><div class="adm-workspace"><main class="adm-main"><form class="adm-member-search" novalidate><label class="adm-filter">搜索成员<input type="search" name="q" placeholder="姓名、学号或房号" maxlength="120" value="${escape(query)}"></label><button type="submit" class="adm-button">查询</button></form><div class="adm-section-heading"><h2>${source === 'roster' ? '已导入成员' : 'SSO 登录成员'} <span data-member-count></span></h2></div><div data-member-results aria-live="polite"></div><nav class="adm-member-pagination" aria-label="成员列表分页"></nav></main><footer class="adm-footer"><div><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2025433394号-1</a><a class="adm-license" href="${escape(licenseHref(location.pathname + location.search))}">License</a><a href="https://github.com/Hylfrd/tongtongsuo" target="_blank" rel="noopener noreferrer" aria-label="在 GitHub 查看项目">${githubIcon}</a></div></footer></div></div>`;
  const disposeBackground = installAmbient(root.querySelector<HTMLElement>('.admin-shell')!);
  window.addEventListener('pagehide', () => { disposed = true; disposeBackground(); }, {once:true});
  const form = root.querySelector<HTMLFormElement>('.adm-member-search')!;
  const results = root.querySelector<HTMLElement>('[data-member-results]')!;
  const pagination = root.querySelector<HTMLElement>('.adm-member-pagination')!;
  function date(value: string | null): string { return value ? new Date(value).toLocaleString('zh-CN', {hour12:false}) : '—'; }
  function loanLink(member: Member): string {
    return `/admin?${new URLSearchParams({section:'loans',q:member.school_code,school_code:member.school_code,status:'all',sort:'borrowed_at',direction:'desc'})}`;
  }
  async function load(): Promise<void> {
    const version = ++serial;
    results.setAttribute('aria-busy','true');
    if (!results.innerHTML) results.innerHTML = '<div class="adm-loading">正在读取成员…</div>';
    try {
      const result = await request<MemberPage>(`/api/admin/members?${new URLSearchParams({source,q:query,page:String(page),page_size:String(pageSize)})}`);
      if (disposed || version !== serial) return;
      const maxPage = Math.max(1,Math.ceil(result.total/pageSize));
      if (page > maxPage) { page = maxPage; void load(); return; }
      const url = new URL(location.href);
      url.search = new URLSearchParams({section:'members',source,...(query ? {q:query} : {}),...(page > 1 ? {page:String(page)} : {})}).toString();
      history.replaceState(null,'',url);
      root.querySelector('.adm-license')!.setAttribute('href',licenseHref(url.pathname+url.search));
      root.querySelector('[data-member-count]')!.textContent = `${result.total} 人`;
      results.innerHTML = result.items.length ? `<div class="adm-table-scroll" tabindex="0" role="region" aria-label="成员列表"><table class="adm-table adm-member-table"><thead><tr><th>姓名</th><th>学号</th><th>房号</th><th>首次登录</th><th>最近登录</th><th>借还记录</th></tr></thead><tbody>${result.items.map(member => `<tr><td><strong>${escape(member.name)}</strong></td><td>${escape(member.school_code)}</td><td>${escape(member.room_number || '—')}</td><td>${escape(date(member.first_login_at))}</td><td>${escape(date(member.last_login_at))}</td><td><a class="adm-text" href="${escape(loanLink(member))}">查看借阅记录</a></td></tr>`).join('')}</tbody></table></div>` : `<div class="adm-empty"><h3>${query ? '没有匹配的成员' : '暂无成员'}</h3>${query ? '' : `<p>${source === 'roster' ? '在其他设置中导入成员 Excel。' : '成员首次使用 SSO 登录后会显示在这里。'}</p>`}</div>`;
      pagination.innerHTML = result.total > pageSize ? `<button type="button" class="adm-outline" data-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>上一页</button><span>第 ${page} / ${maxPage} 页</span><button type="button" class="adm-outline" data-page="${page + 1}" ${page >= maxPage ? 'disabled' : ''}>下一页</button>` : '';
      pagination.querySelectorAll<HTMLButtonElement>('[data-page]').forEach(button => button.addEventListener('click', () => { page = Number(button.dataset.page); void load(); }));
    } catch (error) {
      if (!disposed && version === serial) {
        results.innerHTML = '<div class="adm-empty"><h3>未能读取成员</h3><button type="button" class="adm-text" data-reload>重新读取</button></div>';
        pagination.innerHTML = '';
        results.querySelector('[data-reload]')!.addEventListener('click',()=>void load());
        showToast(message(error), 'error');
      }
    } finally { if (version === serial) results.removeAttribute('aria-busy'); }
  }
  form.addEventListener('submit',event => {
    event.preventDefault(); query = (form.elements.namedItem('q') as HTMLInputElement).value.trim(); page = 1; void load();
  });
  await load();
}
