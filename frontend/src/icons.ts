const paths = {
  scan: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M4 12h16"/><rect x="7" y="7" width="3" height="3"/><path d="M14 7h3v3m-10 4v3h3m4-3h3v3"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
  back: '<path d="m12 5-7 7 7 7M5 12h15"/>',
  user: '<circle cx="12" cy="8" r="3"/><path d="M5 21v-3a7 7 0 0 1 14 0v3"/>',
  key: '<circle cx="8" cy="9" r="4"/><path d="m11 12 8 8m-4-4 2-2m0 4 2-2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  open: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 7-2m-3 10v2"/>',
  refresh: '<path d="M19 9a8 8 0 1 0-1 9"/><path d="M19 3v6h-6"/>',
  toolbox: '<rect x="3" y="8" width="18" height="13" rx="3"/><path d="M8 8V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v3M3 13h18m-12-2v4m6-4v4"/>',
  return: '<path d="M5 7h10a5 5 0 0 1 0 10H9M5 7l4-4M5 7l4 4"/>',
  tape: '<path d="M4 16V9a6 6 0 0 1 12 0v7H4Zm12-5h5v5h-5m-3 0v3H3v-3"/><circle cx="10" cy="9" r="2"/>',
  hammer: '<path d="m10 9-6 11 3 1 7-11m-4-1L7 7l3-5 6 3 2 3 3 1-2 3-3-2-3 1-3-2Z"/>',
  pliers: '<path d="m8 3 2 6-6 12m12-18-2 6 6 12M10 9l4 3m0-3-4 3m-3 7 5-10 5 10"/><circle cx="12" cy="10" r="2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  logout: '<path d="M9 4H5v16h4m6-12 4 4-4 4m-6-4h10"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
} as const;

export type IconName = keyof typeof paths;
export function icon(name: IconName, className = ''): string {
  return `<svg class="icon ${className}" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
}

export const githubIcon = '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .8a11.2 11.2 0 0 0-3.54 21.83c.56.1.77-.24.77-.54v-2.1c-3.13.68-3.8-1.33-3.8-1.33-.51-1.3-1.25-1.65-1.25-1.65-1.02-.7.08-.69.08-.69 1.13.08 1.73 1.16 1.73 1.16 1 1.71 2.62 1.22 3.26.93.1-.72.39-1.22.71-1.5-2.5-.29-5.13-1.25-5.13-5.57 0-1.23.44-2.23 1.16-3.02-.12-.28-.5-1.43.11-2.98 0 0 .95-.3 3.08 1.15a10.7 10.7 0 0 1 5.61 0c2.14-1.45 3.08-1.15 3.08-1.15.62 1.55.23 2.7.12 2.98.72.79 1.15 1.79 1.15 3.02 0 4.33-2.63 5.28-5.14 5.56.4.35.76 1.03.76 2.08v3.11c0 .3.2.65.77.54A11.2 11.2 0 0 0 12 .8Z"/></svg>';
