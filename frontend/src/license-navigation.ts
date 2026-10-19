/** Preserve local entry context without accepting an external return destination. */
export function safeReturnPath(value: string | null): string {
  if (!value || value.length > 2048 || !value.startsWith('/') || value.startsWith('//') || /[\\\p{Cc}\p{Cf}\p{Cs}]/u.test(value)) return '/';
  try {
    const url = new URL(value, 'https://toolbox.invalid');
    if (url.origin !== 'https://toolbox.invalid') return '/';
    const rawPath = value.split(/[?#]/, 1)[0];
    const decoded = decodeURIComponent(rawPath);
    // Reject URL normalization and encoded delimiters before matching application routes.
    if (url.pathname !== rawPath || /[\\\p{Cc}\p{Cf}\p{Cs}]/u.test(decoded)) return '/';
    // Controls remain unsafe even when encoded in query or fragment values.
    if (/[\\\p{Cc}\p{Cf}\p{Cs}]/u.test(decodeURIComponent(url.search + url.hash))) return '/';
    if (['/', '/m', '/m/', '/admin', '/contact'].includes(decoded)) return rawPath + url.search + url.hash;
    const match = decoded.match(/^\/(?:m\/)?scan\/([^/]+)\/?$/u);
    if (!match || match[1].length > 64 || ['.', '..'].includes(match[1]) || /[%?#]/.test(match[1])) return '/';
    return rawPath + url.search + url.hash;
  } catch {
    return '/';
  }
}

export function licenseHref(from: string): string {
  return `/license?${new URLSearchParams({ from: safeReturnPath(from) })}`;
}

export function contactHref(from: string, language: 'zh' | 'en' = 'zh'): string {
  let path = safeReturnPath(from);
  const url = new URL(path, 'https://toolbox.invalid');
  if (url.pathname === '/contact') path = safeReturnPath(url.searchParams.get('from'));
  return `/contact?${new URLSearchParams({ from: path, lang: language })}`;
}
