import './license.css';
import englishLicense from '../../LICENSE?raw';
import { icon } from './icons';
import { safeReturnPath } from './license-navigation';
import { rescanButton } from './site-footer';

export function mountLicense(root: HTMLElement): void {
  const from = safeReturnPath(new URLSearchParams(location.search).get('from'));
  document.title = '开源许可 · 正雅苑共享工具箱';
  document.documentElement.lang = 'zh-CN';
  root.className = 'license-app';
  root.innerHTML = `
    <main class="license-page">
      <header class="license-header">
        <div class="brand-with-scan"><p class="license-brand">正雅苑 <span>· 共享工具箱</span></p>${rescanButton()}</div>
        <div class="license-header-actions"><a class="license-return" data-license-return>${icon('back')}<span>返回</span></a></div>
      </header>
      <article class="license-paper" aria-labelledby="license-title">
        <div class="license-heading"><h1 id="license-title">开源许可</h1><p>AGPL-3.0 · GNU Affero 通用公共许可证</p></div>
        <iframe class="license-translation" src="https://www.chinasona.org/gnu/agpl-3.0-cn.html" title="AGPL-3.0 简体中文译文" referrerpolicy="no-referrer" sandbox="allow-popups allow-popups-to-escape-sandbox"></iframe>
        <p class="license-source"><a href="https://www.chinasona.org/gnu/agpl-3.0-cn.html" target="_blank" rel="noopener noreferrer">打开中文原文 ${icon('arrow')}</a></p>
        <details class="license-original"><summary><span>完整英文原文</span><span aria-hidden="true">＋</span></summary><pre lang="en" data-license-text tabindex="0" role="region" aria-label="AGPL-3.0 English license"></pre></details>
        <footer class="license-end"><a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener noreferrer">GNU 官方原文 ${icon('arrow')}</a><a class="license-return" data-license-return>${icon('back')}<span>返回</span></a></footer>
      </article>
    </main>`;
  root.querySelectorAll<HTMLAnchorElement>('[data-license-return]').forEach(link => { link.href = from; });
  root.querySelector<HTMLElement>('[data-license-text]')!.textContent = englishLicense;
}
