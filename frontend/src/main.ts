import './style.css';

const entry = document.querySelector<HTMLParagraphElement>('#entry')!;
const connection = document.querySelector<HTMLElement>('#connection')!;
const message = document.querySelector<HTMLParagraphElement>('#status-message')!;
const retry = document.querySelector<HTMLButtonElement>('#retry')!;
const match = window.location.pathname.match(/^\/scan\/([^/]+)\/?$/);

if (match) {
  try {
    entry.textContent = `柜锁编号：${decodeURIComponent(match[1])}`;
  } catch {
    entry.textContent = '二维码地址格式不正确，请重新扫码。';
  }
}

async function checkConnection(): Promise<void> {
  retry.disabled = true;
  connection.textContent = '检查中…';
  message.textContent = '正在检查后台服务。';
  try {
    const response = await fetch('/api/health', {
      cache: 'no-store',
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error('Health check failed');
    const health: unknown = await response.json();
    if (
      typeof health !== 'object' || health === null ||
      !('status' in health) || health.status !== 'ok' ||
      !('service' in health) || health.service !== 'tongtongsuo'
    ) throw new Error('Unexpected service response');
    connection.textContent = '已连接';
    message.textContent = '后台服务可用；身份认证与开锁功能尚未开放。';
  } catch {
    connection.textContent = '暂时无法连接';
    message.textContent = '暂时无法连接后台，请稍后重试。';
  } finally {
    retry.disabled = false;
  }
}

retry.addEventListener('click', () => { void checkConnection(); });
void checkConnection();
