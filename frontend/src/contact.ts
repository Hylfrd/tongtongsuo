import './contact.css';
import { installAmbient } from './ambient';
import { icon } from './icons';
import type { Language } from './i18n';
import { safeReturnPath } from './license-navigation';
import { issuesUrl, repositoryUrl } from './project';
import { rescanButton, siteFooter } from './site-footer';

export function mountContact(root: HTMLElement): void {
  const query = new URLSearchParams(location.search);
  const from = safeReturnPath(query.get('from'));
  let language: Language = query.get('lang') === 'en' ? 'en' : 'zh';
  let disposeBackground: (() => void) | undefined;
  const copy = {
    zh: { title:'联系我们', hall:'正雅苑', toolbox:'共享工具箱', back:'返回', developer:'开发者', wechat:'微信', phone:'电话', email:'邮箱', school:'学校', help:'如遇到使用问题，可直接联系开发者。', feedback:'问题反馈', contribute:'贡献', issues:'GitHub Issues', repository:'项目仓库' },
    en: { title:'Contact us', hall:'Positive Education Hall', toolbox:'Shared Toolbox', back:'Back', developer:'Developer', wechat:'WeChat', phone:'Phone', email:'Email', school:'School', help:'If you have trouble using the toolbox, contact the developer directly.', feedback:'Report an issue', contribute:'Contribute', issues:'GitHub Issues', repository:'Project repository' },
  };

  function render(): void {
    const text = copy[language];
    document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
    document.title = `${text.title} · ${text.hall}`;
    disposeBackground?.();
    root.className = 'student-app contact-app';
    root.innerHTML = `<main class="page-shell contact-shell">
      <header class="contact-header"><div class="brand-with-scan"><div class="product-context"><span class="product-hall">${text.hall}</span><span class="product-name">${text.toolbox}</span></div>${rescanButton(language)}</div><div class="contact-header-actions"><button class="text-button language-button" type="button" data-contact-language aria-label="${language === 'zh' ? 'Switch to English' : '切换为中文'}">${language === 'zh' ? 'EN' : '中文'}</button><a class="secondary-button contact-return" data-contact-return>${icon('back')}<span>${text.back}</span></a></div></header>
      <div class="contact-scroll"><article class="contact-paper" aria-labelledby="contact-title">
        <div class="contact-heading"><h1 id="contact-title">${text.title}</h1><p>${text.help}</p></div>
        <div class="contact-columns"><section aria-labelledby="developer-title"><h2 id="developer-title">${text.developer}</h2><dl class="contact-details">
          <div><dt>${text.wechat}</dt><dd>Hylfrd</dd></div>
          <div><dt>${text.phone}</dt><dd><a href="tel:+8618123705397">+86 18123705397</a></dd></div>
          <div><dt>${text.email}</dt><dd><a href="mailto:Hylfrd@outlook.com">Hylfrd@outlook.com</a></dd></div>
          <div><dt>${text.school}</dt><dd><a href="mailto:v530024107@outlook.com">v530024107@outlook.com</a></dd></div>
        </dl></section><nav class="contact-project" aria-label="${text.repository}">
          <a href="${issuesUrl}" target="_blank" rel="noopener noreferrer"><div><h2>${text.feedback}</h2><span>${text.issues}</span></div>${icon('arrow')}</a>
          <a href="${repositoryUrl}" target="_blank" rel="noopener noreferrer"><div><h2>${text.contribute}</h2><span>Hylfrd / zhengya-sharedtoolbox</span></div>${icon('arrow')}</a>
        </nav></div>
      </article></div>
    </main>${siteFooter(location.pathname + location.search, language)}`;
    root.querySelector<HTMLAnchorElement>('[data-contact-return]')!.href = from;
    disposeBackground = installAmbient(root);
  }

  root.addEventListener('click', event => {
    if (!(event.target instanceof Element) || !event.target.closest('[data-contact-language]')) return;
    language = language === 'zh' ? 'en' : 'zh';
    const url = new URL(location.href);
    url.searchParams.set('lang', language);
    history.replaceState(null, '', url);
    render();
    root.querySelector<HTMLButtonElement>('[data-contact-language]')?.focus({ preventScroll:true });
  });
  window.addEventListener('pagehide', () => disposeBackground?.(), { once:true });
  render();
}
