import './style.css';

type Identity = { name: string; school_code: string | null };

const entry = document.querySelector<HTMLParagraphElement>('#entry')!;
const connection = document.querySelector<HTMLElement>('#connection')!;
const authentication = document.querySelector<HTMLElement>('#authentication')!;
const message = document.querySelector<HTMLParagraphElement>('#status-message')!;
const retry = document.querySelector<HTMLButtonElement>('#retry')!;
const form = document.querySelector<HTMLFormElement>('#login-form')!;
const fields = document.querySelector<HTMLFieldSetElement>('#login-fields')!;
const username = document.querySelector<HTMLInputElement>('#username')!;
const password = document.querySelector<HTMLInputElement>('#password')!;
const login = document.querySelector<HTMLButtonElement>('#login')!;
const identity = document.querySelector<HTMLElement>('#identity')!;
const studentName = document.querySelector<HTMLElement>('#student-name')!;
const schoolCode = document.querySelector<HTMLElement>('#school-code')!;
const logout = document.querySelector<HTMLButtonElement>('#logout')!;
const authStatus = document.querySelector<HTMLElement>('#auth-status')!;
const authError = document.querySelector<HTMLElement>('#auth-error')!;
let authPending = true;
let healthPending = false;

const match = window.location.pathname.match(/^\/scan\/([^/]+)\/?$/);
if (match) {
  try {
    entry.textContent = `柜锁编号：${decodeURIComponent(match[1])}`;
  } catch {
    entry.textContent = '二维码地址格式不正确，请重新扫码。';
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function readIdentity(value: unknown): Identity {
  if (!isRecord(value) || !isRecord(value.user)) throw new Error('Invalid identity');
  const user = value.user;
  if (typeof user.name !== 'string' || !user.name.trim() ||
      (user.school_code !== null && typeof user.school_code !== 'string')) {
    throw new Error('Invalid identity');
  }
  return { name: user.name, school_code: user.school_code };
}

function showUser(user: Identity | null): void {
  form.hidden = user !== null;
  identity.hidden = user === null;
  studentName.textContent = user?.name ?? '';
  schoolCode.textContent = user?.school_code || '学校未提供';
  authStatus.textContent = user ? '学校身份验证成功。' : '';
}

function setPending(pending: boolean): void {
  authPending = pending;
  fields.disabled = pending;
  logout.disabled = pending;
  form.setAttribute('aria-busy', String(pending));
}

function clearError(): void {
  authError.textContent = '';
  authError.hidden = true;
}

function showError(text: string): void {
  authError.textContent = text;
  authError.hidden = false;
  authStatus.textContent = '';
  authError.focus();
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (isRecord(data) && isRecord(data.detail) &&
        typeof data.detail.message === 'string' && data.detail.message.trim()) {
      return data.detail.message;
    }
  } catch { /* A proxy or network error may return a non-JSON response. */ }
  if (response.status === 401) return '学校账号或密码未通过验证，请检查后重新输入。';
  if (response.status === 429) return '登录尝试过于频繁，请稍后再试。';
  return '暂时无法完成学校身份验证，请稍后再试。';
}

async function restoreSession(): Promise<void> {
  try {
    const response = await fetch('/api/auth/me', {
      credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000),
    });
    if (response.status === 401) {
      showUser(null);
    } else if (response.ok) {
      showUser(readIdentity(await response.json()));
    } else {
      authStatus.textContent = '暂时无法检查登录状态，可尝试重新登录。';
    }
  } catch {
    authStatus.textContent = '暂时无法检查登录状态，请确认网络后再登录。';
  } finally {
    setPending(false);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (authPending) return;
  clearError();
  if (!username.value.trim()) {
    showError('请输入学校账号。');
    username.focus();
    return;
  }
  setPending(true);
  login.textContent = '正在验证…';
  authStatus.textContent = '正在向学校验证身份，请勿重复提交。';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value }),
      signal: AbortSignal.timeout(45000),
    });
    password.value = '';
    if (!response.ok) {
      showError(await responseMessage(response));
      return;
    }
    showUser(readIdentity(await response.json()));
    username.value = '';
    authStatus.focus();
  } catch {
    showError('未能确认登录结果，请检查网络或稍后重试。密码已清空，请重新输入。');
  } finally {
    password.value = '';
    login.textContent = '验证并登录';
    setPending(false);
  }
});

logout.addEventListener('click', async () => {
  if (authPending) return;
  clearError();
  setPending(true);
  logout.textContent = '正在退出…';
  try {
    const response = await fetch('/api/auth/logout', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error('Logout failed');
    showUser(null);
    password.value = '';
    authStatus.textContent = '已退出柜锁项目。';
    setPending(false);
    username.focus();
  } catch {
    showError('暂时无法确认退出，请检查网络后再次退出。');
  } finally {
    logout.textContent = '退出柜锁项目';
    setPending(false);
  }
});

async function checkConnection(): Promise<void> {
  if (healthPending) return;
  healthPending = true;
  retry.disabled = true;
  connection.textContent = '检查中…';
  authentication.textContent = '检查中…';
  message.textContent = '正在检查后台服务。';
  try {
    const [healthResponse, statusResponse] = await Promise.all([
      fetch('/api/health', { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000) }),
      fetch('/api/status', { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(8000) }),
    ]);
    if (!healthResponse.ok || !statusResponse.ok) throw new Error('Service check failed');
    const health: unknown = await healthResponse.json();
    const status: unknown = await statusResponse.json();
    if (!isRecord(health) || health.status !== 'ok' || health.service !== 'tongtongsuo' ||
        !isRecord(status)) throw new Error('Unexpected service response');
    connection.textContent = '已连接';
    authentication.textContent = status.authentication === 'school_account' ? '可提交验证' : '暂不可用';
    message.textContent = '后台服务可用；开锁功能尚未开放。';
  } catch {
    connection.textContent = '暂时无法连接';
    authentication.textContent = '暂无法确认';
    message.textContent = '暂时无法连接后台，请稍后重试。';
  } finally {
    healthPending = false;
    retry.disabled = false;
  }
}

window.addEventListener('pagehide', () => { password.value = ''; });
retry.addEventListener('click', () => { void checkConnection(); });
void restoreSession();
void checkConnection();
