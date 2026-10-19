import './site-footer.css';
import { githubIcon, icon } from './icons';
import type { Language } from './i18n';
import { contactHref, licenseHref } from './license-navigation';
import { repositoryUrl } from './project';
import { showToast } from './toast';

export function rescanButton(language: Language = 'zh'): string {
  const label = language === 'en' ? 'Scan another cabinet' : '重新扫码';
  return `<button class="rescan-button" type="button" data-rescan aria-label="${label}" title="${label}">${icon('scan')}</button>`;
}

export function siteFooter(from: string, language: Language = 'zh', admin = false): string {
  return `<footer class="${admin ? 'adm-footer' : 'site-footer'}"><div class="footer-links">${rescanButton(language)}<a class="contact-link" href="${contactHref(from, language)}">${language === 'en' ? 'Contact us' : '联系我们'}</a><a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2025433394号-1</a><a class="license-link${admin ? ' adm-license' : ''}" href="${licenseHref(from)}">License</a><a class="github-link" href="${repositoryUrl}" target="_blank" rel="noopener noreferrer" aria-label="${language === 'en' ? 'Source code' : '项目源码'}">${githubIcon}</a></div></footer>`;
}

export function updateSiteFooter(root: Element, from: string, language: Language = 'zh'): void {
  const contact = root.querySelector<HTMLAnchorElement>('.contact-link');
  if (contact) {
    contact.href = contactHref(from, language);
    contact.textContent = language === 'en' ? 'Contact us' : '联系我们';
  }
  root.querySelector('.license-link')?.setAttribute('href', licenseHref(from));
  root.querySelector('.github-link')?.setAttribute('aria-label', language === 'en' ? 'Source code' : '项目源码');
  const label = language === 'en' ? 'Scan another cabinet' : '重新扫码';
  root.querySelector('[data-rescan]')?.setAttribute('aria-label', label);
  root.querySelector('[data-rescan]')?.setAttribute('title', label);
}

export function installRescan(root: Element, allowed: () => boolean): void {
  root.addEventListener('click', event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>('[data-rescan]');
    if (!button || button.disabled || !allowed()) return;
    button.disabled = true;
    const language = document.documentElement.lang.startsWith('en') ? 'en' : 'zh';
    void import('./scanner')
      .then(({ openCabinetScanner }) => openCabinetScanner(language, path => location.assign(path)))
      .catch(error => showToast(error instanceof Error ? error.message : language === 'en' ? 'Unable to open the camera.' : '无法打开摄像头。', 'error'))
      .finally(() => { button.disabled = false; });
  });
}
