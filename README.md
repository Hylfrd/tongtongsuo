# tongtongsuo

校园柜锁 Web 项目。计划流程为扫码进入网页、学校身份认证、后端核验权限、记录操作并通过通通锁 G2 网关开锁。

## 当前状态

- 用户已购买隐形抽屉 / 柜门电子锁，以及 G2 网关。
- 用户已在通通锁 App 中完成设备联网和关联，并确认 App 可以操作。
- 已提供 Node.js 24 + Vite + TypeScript 前端骨架和 Python + FastAPI 后端骨架。
- 前端只有扫码入口与后台连接检查；学校身份认证、学生信息存储、授权规则和通通锁真实调用均未实现。
- 生产域名为 `https://bnbu.hmcl-helper.cn`。部署使用系统 Python 3.12；本地也可使用 Python 3.13。
- App 操作成功不代表本项目后端已连通，也不单独证明操作经过 G2 网关；后续需分别验证云端设备读取和网关控制链路。

## 设备资料

用户提供了淘宝商品页截图 PDF，商品标题为“免打孔抽屉锁隐形柜门智能电子感应锁衣柜鞋柜锁nfc涂鸦蓝牙远程”。截图包含多个商品规格，不能仅凭标题认定已购版本支持所有标注功能。实际接入方向依据用户已使用通通锁 App 和 G2 网关的确认；具体锁型号、固件及可用接口仍待核验。

原始商品截图保留在用户本地，不随初始化提交上传。

## 官方开发文档

- 商家提供的入口：[APP 开锁（电子钥匙） / APP 集成开发指南](https://cnopen.ttlock.com/document/doc?urlName=userGuide%2Fekey.html)。
- [获取访问令牌](https://cnopen.ttlock.com/document/doc?urlName=cloud/oauth2/getAccessToken.html)。
- 同一文档站的“开发指南 → 远程开锁（网关）”与“Cloud API V3”是后续后端接入需要核对的章节。

已核对入口页面的开发步骤：注册开发者账号并创建应用，取得 `client_id` 和 `client_secret`，由后端获取访问令牌，然后携带令牌调用相关 Open API。入口同时介绍 APP SDK 的蓝牙流程；本项目的网关后端接入方案需按相应章节进一步确认。

## 本地运行

在仓库目录开启两个终端。后端（Windows PowerShell）：

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端（Node.js 24）：

```powershell
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`，开发服务器把 `/api` 请求转发到后端。扫码地址预留为 `/scan/<柜锁编号>`；该编号仅定位设备，不授予权限。没有学校密码输入框，也没有模拟登录或假开锁。

构建与检查：

```powershell
# 在 frontend 目录：类型检查并构建
npm run build

# 在 backend 目录：接口行为测试
.\.venv\Scripts\python.exe -m pytest -q
```

前端构建完成后，后端也会在 `http://127.0.0.1:8000/` 提供 `frontend/dist`，用于检查同源生产模式。生产服务器不运行 Vite 开发服务器或 Node 服务；Node 仅用于构建。

## 当前接口

| 接口 | 当前行为 |
| --- | --- |
| `GET /api/health` | 返回进程健康状态、服务名和当前提交版本；不代表学校登录或通通锁已连通 |
| `GET /api/status` | 返回身份认证与开锁均为 `not_configured` |
| `GET /api/auth/login` | 返回 HTTP 503、`AUTH_NOT_CONFIGURED` |
| `GET /api/auth/callback` | 返回 HTTP 503，不接受未经验证的票据 |
| `POST /api/locks/{lock_id}/unlock` | 返回 HTTP 503、`LOCK_CONTROL_NOT_CONFIGURED`，不操作硬件 |

`APP_VERSION` 为部署版本；`FRONTEND_DIST` 可指定前端构建目录，默认使用本仓库的 `frontend/dist`。这些是进程环境变量，当前代码不自动读取 `.env` 文件。

## GitHub Actions 与部署

参考 [hmcl-helper 的工作流](https://github.com/Hylfrd/hmcl-helper/blob/master/.github/workflows/ci.yml)，复用 SSH/SCP 和以下三个仓库 secrets：

- `SERVER_HOST`：服务器地址。
- `SERVER_USER`：具备本项目服务及 Caddy 配置管理权限的 SSH 用户；当前部署脚本要求 root。
- `SERVER_PASSWORD`：该用户的 SSH 密码。

`.github/workflows/ci.yml` 在 PR 时执行前端构建、后端测试和 ShellCheck；推送 `main` 时，通过检查后上传同一构建产物并部署。可手动运行；勾选 `inspect_only` 时只读取服务器状态，不部署。部署串行执行，不中断正在切换的发布。

文件传输使用 Runner 的 OpenSSH `scp` 与 `sshpass`（密码经环境变量传入），沿用上述 SSH 凭据；整个上传有 180 秒超时，避免旧 SCP action 的传输等待无法受 `command_timeout` 限制。首次连接记录服务器主机密钥，同次运行后续连接校验该密钥。

服务器布局：

```text
/var/www/tongtongsuo/
  incoming/<run>-<attempt>/       上传包
  releases/<sha>-<run>-<attempt>/  代码、构建结果、独立 Python venv
  current -> releases/...        当前运行版本
  backups/<run>-<attempt>/        发布前的本项目 unit 和 Caddy 配置
/etc/systemd/system/tongtongsuo.service
/etc/caddy/tongtongsuo.caddy
```

- 后端由 systemd 的独立动态非 root 用户运行，只监听 `127.0.0.1:18731`。
- CI 使用与服务器匹配的 Linux/Python 3.12 下载二进制依赖包并随发布上传。服务器用 `venv --without-pip` 创建隔离环境，再从包内引导 pip 并离线安装；不依赖系统 `ensurepip`，不修改系统 Python 包。
- 在既有 `/etc/caddy/Caddyfile` 末尾添加本项目配置的精确 `import`；独立配置仅声明 `bnbu.hmcl-helper.cn`。保留其他站点与服务，先备份并验证配置，再平滑 reload Caddy。
- Caddy 负责 HTTPS；域名 DNS 必须指向服务器，80/443 应保持可访问。
- 部署验证本机 API、前端页面和公网 HTTPS API 的提交版本；激活阶段失败时恢复此前应用版本、本项目 unit 和 Caddy 配置。
- 发布前的安装失败不会切换现有服务。备份、旧发布与上传包保留，当前没有自动删除策略。
- ShellCheck、DNS 或真实部署失败时 Actions 会失败，不把上传成功视为部署成功。可在修复后重新运行工作流。

服务器排查命令：`systemctl status tongtongsuo`、`journalctl -u tongtongsuo -n 50`、`curl http://127.0.0.1:18731/api/health`。生产 Uvicorn 关闭访问日志，避免后续认证回调的查询参数落入该日志。

## 下一阶段：真实身份认证与设备接入

学校任意登录页面不能自动把学生身份传给本后端。需确定学校支持的 SSO/CAS/OIDC 等接入方式、应用授权与回调地址，由后端验证票据后取得允许使用的身份字段。不能把浏览器传来的学号当成已认证身份，也不采集学校密码。

1. 确认开放平台开发者账号、应用和凭据是否已准备好，并核对现有 App 账号及门锁的授权接入方式。
2. 确定学校认证接入许可、返回字段及柜锁使用权限；再实现会话与操作记录。
3. 完成通通锁令牌获取和只读的门锁、网关查询，验证设备关联与在线状态，再安排实际开锁验证。

当前没有获取访问令牌、读取设备或控制门锁的成功记录。账号、密码、应用密钥、令牌和带敏感信息的接口响应保存在本地忽略路径中，不提交到 Git。

## 仓库

- 远程仓库：<https://github.com/Hylfrd/tongtongsuo>
- 初始分支：`main`
- 提交格式见 [AGENTS.md](AGENTS.md)。
