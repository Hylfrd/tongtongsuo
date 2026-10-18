export type Language = 'zh' | 'en';
export const messages = {
  zh: {
    loginHint: '使用 BNBU 账号登录', startScan: '扫描柜子二维码', hall: '正雅苑', title: '共享工具箱', login: '登录', username: '登录账号', password: '密码', accountHint: '输入账号', passwordHint: '请输入密码',
    checking: '正在验证…', loading: '正在加载…', logout: '退出登录', loggedOut: '已退出登录', admin: '管理入口', scan: '扫码选择柜子', scanDetail: '扫码后选择借用或归还工具。', invalidCabinet: '未找到该柜子，请重新扫码。',
    cabinet: '柜号', welcome: '欢迎，', borrow: '借用工具', return: '归还工具', choose: '选择工具', home: '返回首页', back: '返回', available: '剩余', emptyTools: '暂无工具', emptyLoans: '暂无借用记录', borrowedAt: '借用时间', selected: '已选', items: '件', quantity: '数量', decrease: '减少数量', increase: '增加数量',
    unlock: '开锁', opening: '正在开锁', unconfirmed: '未确认开锁', opened: '已开锁', within: '请在', seconds: '秒内开柜', autoLock: '关门自动落锁', finish: '完成', reopen: '重新打开柜门', retry: '重试',
    network: '网络连接失败，请稍后重试。', expired: '登录已过期，请重新登录。', authFailed: '账号或密码未通过验证，请重试。', cooldown: '稍后重试', second: '秒', failed: '请求失败，请重试。', hardware: '暂时无法连接柜锁，请稍后再试。', logoutFailed: '暂时无法确认退出，请重试。',
    preview: '界面预览 · 不操作真实柜锁', previewLogin: '预览借还流程', previewUser: '苑舍同学', demoDone: '演示完成', previewOnly: '仅更新本页演示记录', source: '项目源码', noSelection: '请先选择工具。',
  },
  en: {
    loginHint: 'Sign in with your BNBU account', startScan: 'Scan QR code', hall: 'Positive Education Hall', title: 'Shared Toolbox', login: 'Sign in', username: 'Account', password: 'Password', accountHint: 'Enter your account', passwordHint: 'Enter your password',
    checking: 'Signing in…', loading: 'Loading…', logout: 'Sign out', loggedOut: 'Signed out', admin: 'Admin', scan: 'Choose your cabinet', scanDetail: 'Scan the cabinet QR code to borrow or return tools.', invalidCabinet: 'Cabinet not found. Please scan again.',
    cabinet: 'Cabinet', welcome: 'Welcome, ', borrow: 'Borrow tools', return: 'Return tools', choose: 'Choose tools', home: 'Back to home', back: 'Back', available: 'Available', emptyTools: 'No tools', emptyLoans: 'No borrowed tools', borrowedAt: 'Borrowed', selected: 'Selected', items: 'items', quantity: 'Quantity', decrease: 'Decrease quantity', increase: 'Increase quantity',
    unlock: 'Unlock', opening: 'Unlocking', unconfirmed: 'Unlock not confirmed', opened: 'Unlocked', within: 'Open within', seconds: 'seconds', autoLock: 'Closes and locks automatically', finish: 'Done', reopen: 'Open cabinet again', retry: 'Retry',
    network: 'Connection failed. Please try again.', expired: 'Your session expired. Please sign in again.', authFailed: 'Account or password could not be verified. Please try again.', cooldown: 'Try again in', second: 's', failed: 'Request failed. Please try again.', hardware: 'Unable to connect to the lock. Please try again later.', logoutFailed: 'Unable to confirm sign-out. Please try again.',
    preview: 'UI preview · No real lock commands', previewLogin: 'Preview borrowing and returns', previewUser: 'Resident', demoDone: 'Preview complete', previewOnly: 'Only this preview has been updated', source: 'Source code', noSelection: 'Select a tool first.',
  },
} as const;
