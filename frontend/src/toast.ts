import './toast.css';

type Kind = 'error' | 'success' | 'info';
const active: { message: string; close: () => void; element: HTMLElement }[] = [];

/** User-facing messages are always text nodes, including server-provided errors. */
export function showToast(message: string, kind: Kind = 'error'): void {
  active.filter(item => !item.element.isConnected).forEach(item => item.close());
  if (!message.trim() || active.some(item => item.message === message)) return;
  // A popover outside a modal dialog is still inert. Keep notifications inside
  // the active modal's subtree so their close button and live region work.
  const host = document.querySelector<HTMLDialogElement>('dialog[open]') ?? document.body;
  let stack = document.querySelector<HTMLDivElement>('#tts-toasts');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'tts-toasts';
    stack.className = 'toast-stack';
    stack.setAttribute('aria-label', '通知');
    if ('showPopover' in stack) stack.setAttribute('popover', 'manual');
    host.append(stack);
  } else if (stack.parentElement !== host) {
    if (stack.hasAttribute('popover') && stack.matches(':popover-open')) stack.hidePopover();
    host.append(stack);
  }
  if (active.length >= 3) active[0].close();
  const previousFocus = document.activeElement;
  const toast = document.createElement('div');
  toast.className = `toast toast-${kind}`;
  toast.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  const symbol = document.createElement('span');
  symbol.className = 'toast-symbol';
  symbol.setAttribute('aria-hidden', 'true');
  symbol.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>${kind === 'success' ? '<path d="m8 12 3 3 5-6"/>' : '<path d="M12 7v6m0 4v.01"/>'}</svg>`;
  const text = document.createElement('p');
  text.textContent = message;
  const dismiss = document.createElement('button');
  dismiss.type = 'button';
  dismiss.className = 'toast-dismiss';
  dismiss.setAttribute('aria-label', '关闭通知');
  dismiss.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="m7 7 10 10M17 7 7 17"/></svg>';
  toast.append(symbol, text, dismiss);
  stack.append(toast);
  if (stack.hasAttribute('popover') && !stack.matches(':popover-open')) stack.showPopover();

  let remaining = kind === 'error' ? 9000 : 5000;
  let started = performance.now();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closing = false;
  const close = (): void => {
    if (closing) return;
    closing = true;
    clearTimeout(timer);
    const index = active.findIndex(item => item.close === close);
    if (index >= 0) active.splice(index, 1);
    const hadFocus = toast.contains(document.activeElement);
    toast.classList.add('is-leaving');
    window.setTimeout(() => {
      toast.remove();
      if (hadFocus && previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
      if (!stack.childElementCount) {
        if (stack.hasAttribute('popover') && stack.matches(':popover-open')) stack.hidePopover();
        stack.remove();
      }
    }, matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180);
  };
  const pause = (): void => {
    if (!timer) return;
    clearTimeout(timer);
    timer = undefined;
    remaining = Math.max(0, remaining - (performance.now() - started));
  };
  const resume = (): void => {
    if (closing || timer || toast.matches(':hover') || toast.contains(document.activeElement)) return;
    started = performance.now();
    timer = setTimeout(close, remaining);
  };
  dismiss.addEventListener('click', close);
  toast.addEventListener('pointerenter', pause);
  toast.addEventListener('pointerleave', resume);
  toast.addEventListener('focusin', pause);
  toast.addEventListener('focusout', () => queueMicrotask(resume));
  active.push({ message, close, element: toast });
  resume();
}
