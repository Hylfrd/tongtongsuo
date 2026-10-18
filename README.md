<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="正雅苑 · 共享工具箱" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/tongtongsuo"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center">中文 · <a href="README.en.md">English</a></p>

面向 BNBU 正雅苑的共享工具箱借还与管理系统。

## Admin

使用电脑访问 `/admin`。手机端仅显示电脑访问提示。

### 通通锁与柜子

在“其他 → 通通锁”填写开放平台的 Client ID、Client Secret，并连接已添加锁和 G2 网关的通通锁 App 账号。应用配置离开输入框后自动保存；Secret 加密保存，以星号表示已配置，填写新值会替换旧值。

在“柜子与设备”点击“同步通通锁”导入设备及网关关联，再把锁分配到对应柜子。每个柜子有独立二维码入口，可以关联多把锁。网关名称、编号与锁的关联来自上次设备同步；手机中改过名称或绑定后，先重新同步。

“请求状态”的结果带查询时间，成功提示只表示请求完成。自动闭锁倒计时结束、锁舌已闭锁，都不能证明柜门已经关好。

### 锁配置

点击锁的“编辑”，在列表下方展开配置：

- **基本信息**：内部编号、锁名称、所在柜子和备注，点击“保存基本信息”。这里的名称用于本系统，不会修改 App 中的锁名。
- **设备信息**：通通锁 ID、电量、型号、固件、硬件版本及关联网关，均为只读。
- **设备参数**：自动闭锁秒数、常开模式及时间段、提示音、音量、开门方向、反锁、防撬报警和重置键。仅锁支持且平台返回有效当前值的选项可以修改；灰显不表示已关闭。

自动闭锁可填 1–32767 秒；支持关闭此功能的锁可填 `-1`，不能填 `0`。常开最多五个时段，每段选择星期及起止时间，或选择全天；启用前至少设置一段，支持时可设置时段开始自动开锁。门未关报警时间目前保留灰显，须在通通锁 App 中设置。

“查询配置”读取平台最新值，手机修改后可主动查询。修改参数后点击“保存设备配置”，系统发送变更并重新读取确认。若出现结果未知、部分失败或未确认提示，先查询核对，避免连续重复提交。

设备页在前台时，设备列表约每 30 分钟同步、状态约每 5 分钟查询，已展开的锁参数约每 3 分钟查询；有未保存修改时跳过参数自动查询。手动查询保留参数草稿。切换页面或退到后台会暂停这些任务。

### 图标生成

在“其他 → 图标生成”配置提供商、API 地址和 API Key。地址填写兼容 API 的根路径（如 `https://api.example.com/v1`）。Key 加密保存且不回显；已有值显示星号，留空保留，填写新值替换。更换提供商或地址时须重新填写 Key。有效修改会自动保存，以 toast 提示结果。

生成流程为可选提示词重写 → 生图 → 可选图片审查。生图模型默认 `gpt-image-2.5-sunburst`；重写与审查可分别选择 `gpt-5.6-luna`、`gpt-6-astra`、`gpt-6-luna`、`gpt-6.1-sol`，默认 `gpt-6.1-sol` / `xhigh`。

思考强度支持 `low`、`mid`、`high`、`xhigh`、`max`、`ultra`，其中 `mid` 请求时转换为 `medium`。模型、思考强度、图片质量与背景选项须由提供商实际支持；不指定输出尺寸。

高级设置中，请求超时为 30–900 秒；提示词必须保留 `{name}`，可用 `{description}` 插入工具描述。“生成测试图”会消耗额度，但不关联工具。工具编辑中的“生成 icon”须在预览后点击“使用此图标”，再保存工具；也可以上传图片。

### 成员与账号

“成员管理”中切换登录方式会自动保存：SSO 使用学校账号验证，仅记录实际登录且返回学号的成员；名单模式核对姓名、学号和宿舍号三项，导入的成员全部可查。

先下载模板，在“成员”表填写姓名、学号、宿舍号，学号和宿舍号保留文本格式。外籍成员使用完整大写姓名并保留空格。仅支持普通 `.xlsx`，每次最多 5000 人、2 MiB，不含公式或重复学号；按学号新增或更新，保留本次表格未包含的原成员。

名单来源筛选不改变登录方式；“查看借阅记录”按该成员学号进入记录页。借还记录可筛选、排序并导出 Excel。“其他”底部可修改管理员密码。

## Develop

需要 Node.js 24 和 Python 3.12+。

安装并构建前端，创建 Python 虚拟环境：

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python -m venv .venv
```

安装后端依赖并启动开发服务。Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

macOS / Linux：

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

启动

```sh
npm --prefix frontend run dev
```

访问 <http://127.0.0.1:5173>。前端会将 `/api` 请求转发到本机的 `8000` 端口。
