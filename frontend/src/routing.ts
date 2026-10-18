export function shouldOpenAdmin(role: string | undefined, path: string, freshLogin: boolean): boolean {
  return role === 'admin' && (path === '/admin' || freshLogin);
}
