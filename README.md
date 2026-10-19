<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="正雅苑 · 共享工具箱" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/zhengya-sharedtoolbox"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center">中文 · <a href="README.en.md">English</a></p>

## 简介

正雅苑共享工具箱是 BNBU 正雅苑的工具借还系统。学生扫描柜子上的二维码即可开门借还，管理员通过 Web 后台管理柜子、智能锁、成员与借还记录。

系统已接入通通锁开放平台，开锁控制真实的智能锁。

线上地址：<https://bnbu.hmcl-helper.cn>

## 后台管理

管理后台为桌面端设计，请用电脑访问 `/admin`。

### 智能锁与网关

在“其他 → 通通锁”中填写开放平台的 Client ID 与 Client Secret，并绑定通通锁 App 账号。配置完成后，可在“柜子与设备”中点击“同步通通锁”，把锁分配到各个柜子；每个柜子有自己的二维码入口，可关联多把锁。

首次添加锁或为 G2 网关配网，需在设备旁通过通通锁 App 完成，之后在后台同步即可。后台不直接操作蓝牙。

锁的“编辑”中可以调整内部编号、自动闭锁、常开时段、提示音等参数：

- 后台里的锁名称仅用于本系统，不会改到通通锁 App。
- 保存参数后系统会下发并核对；状态未知时先点“查询配置”看最新值，再决定是否重试。
- 显示“成功”仅代表指令已下发。柜门是否关好，以现场实际状态为准。

网关的“编辑”中可以修改本地名称与备注，查询网关类型、MAC、网络、在线状态及关联锁的信号强度。网关与锁的关联由通通锁发现，查询时同步到本系统。

### AI 图标生成

在“其他 → 图标生成”中配置 AI 提供商的 API 与 Key，即可用提示词为工具生成图标。流程支持提示词重写、生图与图片审查，可调整模型、思考强度、图片质量等参数；生成满意后可应用到工具，也可直接上传图片。

Key 加密存储。更换提供商或地址时需重新填写。

### 成员与登录方式

两种登录方式，切换后即时生效：

- **SSO**：对接学校统一身份认证，仅记录实际登录并返回学号的成员。
- **名单**：通过 Excel 模板批量导入姓名、学号、宿舍号，仅名单内的成员可用。

借阅记录可在后台筛选并导出为 Excel。管理员密码在“其他”页面底部修改。

## 开发

需要 Node.js 24 和 Python 3.12+。

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python -m venv .venv
```

安装后端依赖并启动服务。Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

macOS / Linux：

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```sh
npm --prefix frontend run dev
```

访问 <http://127.0.0.1:5173>，前端将 `/api` 转发到本机 `8000`。

测试：

```sh
npm --prefix frontend test
cd backend && python -m pytest -q
```

## 开源协议

AGPL-3.0，详见 [LICENSE](LICENSE)。
