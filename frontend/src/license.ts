import './license.css';
import englishLicense from '../../LICENSE?raw';
import { icon, githubIcon } from './icons';
import { safeReturnPath } from './license-navigation';

const guide = [
  ['使用与修改', '你可以运行、研究和修改本项目。再分发时，需要保留版权、许可和免责说明，并按许可证要求提供相应源代码。修改后的作品应注明改动和日期。', '第 0—6 条'],
  ['网络服务', '如果修改本程序，并让用户通过网络与修改版交互，需要向这些用户显著提供免费获取相应源代码的机会。', '第 13 条'],
  ['继续保持开放', '再分发的受许可作品仍须遵守 AGPL。附加条款、专利许可和其他协议不能随意削减接收者在本许可证下的权利。', '第 7—12 条'],
  ['版本与保证', '版本选择须遵循程序的许可声明。在适用法律允许的范围内，软件按原样提供，不附带担保；责任限制以许可证原文为准。', '第 14—17 条'],
];

export function mountLicense(root: HTMLElement): void {
  const from = safeReturnPath(new URLSearchParams(location.search).get('from'));
  document.title = '开源许可 · 正雅苑共享工具箱';
  document.documentElement.lang = 'zh-CN';
  root.className = 'license-app';
  root.innerHTML = `
    <main class="license-page">
      <header class="license-header">
        <p class="license-brand">正雅苑 <span>· 共享工具箱</span></p>
        <a class="license-return" data-license-return>${icon('back')}<span>返回</span></a>
      </header>
      <article class="license-paper" aria-labelledby="license-title">
        <div class="license-heading"><p class="license-eyebrow">AGPL-3.0</p><h1 id="license-title">开源许可</h1><p>GNU Affero 通用公共许可证 · 第 3 版</p></div>
        <div class="license-intro"><p>以下为中文导读，帮助你了解本项目的使用与分享方式，不替代许可证。完整条款以本页收录的英文原文为准。</p><a href="https://github.com/Hylfrd/tongtongsuo" target="_blank" rel="noopener noreferrer">${githubIcon}<span>查看项目源码</span>${icon('arrow')}</a></div>
        <div class="license-guide">${guide.map(([title, description, sections]) => `<section><div class="license-guide-title"><h2>${title}</h2><span>${sections}</span></div><p>${description}</p></section>`).join('')}</div>
        <div class="license-sources"><p>完整中文译文可在 <a href="https://www.gnu.org/licenses/translations.html" target="_blank" rel="noopener noreferrer">GNU 翻译索引</a> 中查阅，或阅读其收录的 <a href="https://www.chinasona.org/gnu/agpl-3.0-cn.html" target="_blank" rel="noopener noreferrer">简体中文译文</a>。中文译文为非官方版本。</p></div>
        <details class="license-original"><summary><span>完整英文原文</span><span aria-hidden="true">＋</span></summary><pre lang="en" data-license-text></pre></details>
        <footer class="license-end"><a href="https://www.gnu.org/licenses/agpl-3.0.html" target="_blank" rel="noopener noreferrer">GNU 官方原文 ${icon('arrow')}</a><a class="license-return" data-license-return>${icon('back')}<span>返回</span></a></footer>
      </article>
    </main>`;
  root.querySelectorAll<HTMLAnchorElement>('[data-license-return]').forEach(link => { link.href = from; });
  root.querySelector<HTMLElement>('[data-license-text]')!.textContent = englishLicense;
}
