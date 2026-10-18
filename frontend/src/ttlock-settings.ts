import './ttlock-settings.css';
import { showToast } from './toast';
import { clearFieldError, markFieldError, validateForm } from './validation';

type Request = <T>(url: string, init?: RequestInit) => Promise<T>;
type Settings = {client_id: string; client_secret_configured: boolean; connected: boolean; username: string; expires_at: string | null};
const message = (error: unknown): string => error instanceof TypeError ? '网络连接失败，请稍后重试。' : error instanceof Error && error.name !== 'TimeoutError' && error.name !== 'AbortError' ? error.message : '请求超时，请稍后重试。';

/** Retain one panel so navigation does not discard drafts or duplicate account connections. */
export function createTTLockSettings(request: Request) {
  const panel = document.createElement('section');
  panel.className = 'adm-ttlock-settings';
  panel.setAttribute('aria-labelledby', 'ttlock-settings-title');
  panel.innerHTML = `<div class="adm-ttlock-heading"><h2 id="ttlock-settings-title">通通锁</h2><span data-connection-state role="status">正在读取…</span></div>
    <form data-app-settings class="adm-form-grid" novalidate>
      <label class="adm-field">Client ID<input name="client_id" required maxlength="128" autocomplete="off" spellcheck="false" disabled></label>
      <label class="adm-field">Client Secret<input name="client_secret" type="password" maxlength="256" autocomplete="new-password" spellcheck="false" placeholder="暂未配置" disabled></label>
    </form>
    <form data-account-connect novalidate>
      <p class="adm-ttlock-help">使用已添加锁和 G2 网关的通通锁账号。</p>
      <div class="adm-form-grid"><label class="adm-field">通通锁账号<input name="username" required maxlength="128" autocomplete="username" spellcheck="false" disabled></label><label class="adm-field">通通锁密码<input name="password" type="password" required maxlength="256" autocomplete="current-password" disabled></label></div>
      <div class="adm-ttlock-actions"><button type="submit" class="adm-button" disabled>连接账号</button></div>
    </form>`;
  const appForm = panel.querySelector<HTMLFormElement>('[data-app-settings]')!;
  const accountForm = panel.querySelector<HTMLFormElement>('[data-account-connect]')!;
  const field = (name: string) => panel.querySelector<HTMLInputElement>(`[name="${name}"]`)!;
  const clientId = field('client_id'), secret = field('client_secret'), username = field('username'), password = field('password');
  const button = accountForm.querySelector<HTMLButtonElement>('button')!;
  const state = panel.querySelector<HTMLElement>('[data-connection-state]')!;
  const lifetime = new AbortController();
  let settings: Settings | undefined, disposed = false, connecting = false;
  let initializing: Promise<void> | undefined, saving: Promise<boolean> | undefined;
  let blurTimer: ReturnType<typeof setTimeout> | undefined;

  const dirty = () => !!settings && (clientId.value.trim() !== settings.client_id || !!secret.value.trim());
  const signal = () => AbortSignal.any([lifetime.signal, AbortSignal.timeout(60000)]);
  function update() {
    if (disposed) return;
    const connected = settings?.connected && !dirty();
    state.textContent = !settings ? '未能读取配置' : connected ? `已连接${settings.username ? ` · ${settings.username}` : ''}` : '尚未连接';
    state.classList.toggle('is-connected', !!connected);
    button.textContent = connecting ? '正在连接…' : connected ? '重新连接' : '连接账号';
    for (const input of [clientId, secret, username, password]) input.disabled = !settings || connecting;
    button.disabled = !settings || connecting;
    secret.placeholder = settings?.client_secret_configured ? '************' : '暂未配置';
  }
  function validSettings() {
    if (!clientId.value.trim()) {
      markFieldError(clientId);
      showToast('请填写 Client ID。', 'error');
      return false;
    }
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(clientId.value.trim())) {
      markFieldError(clientId);
      showToast('请检查 Client ID，不能包含空格或特殊符号。', 'error');
      return false;
    }
    if (secret.value && (!/^[\x21-\x7e]+$/.test(secret.value.trim()) || secret.value.trim().length > 256)) {
      markFieldError(secret);
      showToast('请检查 Client Secret，不能包含空白或非英文字符。', 'error');
      return false;
    }
    return true;
  }
  async function save(): Promise<boolean> {
    if (saving) {
      const success = await saving;
      return success && !disposed ? save() : false;
    }
    if (disposed || !settings) return false;
    if (!dirty()) return true;
    if (!validSettings()) return false;
    const id = clientId.value.trim(), key = secret.value.trim();
    const body: Record<string,string> = {};
    if (id !== settings.client_id) body.client_id = id;
    if (key) body.client_secret = key;
    saving = (async () => {
      try {
        const result = await request<Settings>('/api/admin/ttlock/settings', {method:'PUT', body:JSON.stringify(body), signal:signal()});
        if (disposed) return false;
        settings = result;
        if (clientId.value.trim() === id) clientId.value = result.client_id;
        if (secret.value.trim() === key) secret.value = '';
        update();
        showToast('通通锁配置已保存。', 'success');
        return true;
      } catch (error) {
        if (!disposed) showToast(message(error), 'error');
        return false;
      }
    })();
    const success = await saving;
    saving = undefined;
    return success;
  }
  async function initialize() {
    if (settings || disposed) return;
    if (initializing) return initializing;
    initializing = (async () => {
      try {
        settings = await request<Settings>('/api/admin/ttlock/settings', {signal:signal()});
        if (disposed) return;
        clientId.value = settings.client_id;
        username.value = settings.username || '';
      } catch (error) {
        if (!disposed) showToast(message(error), 'error');
      } finally { if (!disposed) update(); }
    })();
    await initializing;
    initializing = undefined;
  }
  function beforeUnload(event: BeforeUnloadEvent) {
    if (dirty() || saving || connecting) { event.preventDefault(); event.returnValue = ''; }
  }
  window.addEventListener('beforeunload', beforeUnload, {signal:lifetime.signal});
  panel.addEventListener('input', event => {
    if (event.target instanceof HTMLInputElement) clearFieldError(event.target);
    update();
  }, {signal:lifetime.signal});
  appForm.addEventListener('focusout', () => {
    if (blurTimer) clearTimeout(blurTimer);
    blurTimer = setTimeout(() => {
      blurTimer = undefined;
      if (document.activeElement !== secret && document.activeElement !== clientId && !connecting) void save();
    }, 0);
  }, {signal:lifetime.signal});
  appForm.addEventListener('submit', event => { event.preventDefault(); void save(); }, {signal:lifetime.signal});
  accountForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (connecting || disposed || !settings || !validSettings() || !validateForm(accountForm)) return;
    connecting = true;
    update();
    try {
      if (!await save()) return;
      if (!settings.client_secret_configured) {
        markFieldError(secret);
        showToast('请填写 Client Secret。', 'error');
        return;
      }
      const result = await request<Settings>('/api/admin/ttlock/connect', {method:'POST', body:JSON.stringify({username:username.value.trim(), password:password.value}), signal:signal()});
      if (disposed) return;
      settings = result;
      username.value = result.username || username.value;
      showToast('通通锁账号已连接。', 'success');
    } catch (error) {
      if (!disposed) {
        markFieldError(username);
        markFieldError(password);
        showToast(message(error), 'error');
      }
    } finally {
      password.value = '';
      connecting = false;
      update();
    }
  }, {signal:lifetime.signal});

  return {
    mount(target: Element) { if (!disposed) { target.append(panel); void initialize(); } },
    dispose() {
      disposed = true;
      lifetime.abort();
      if (blurTimer) clearTimeout(blurTimer);
      secret.value = '';
      password.value = '';
      panel.remove();
    },
  };
}
