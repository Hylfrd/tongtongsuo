<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="Positive Education Hall · Shared Toolbox" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/tongtongsuo"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center"><a href="README.md">中文</a> · English</p>

## Introduction

The shared toolbox borrowing system at Positive Education Hall (正雅苑), BNBU. Students scan the QR code on a cabinet to open it and borrow or return tools; administrators manage cabinets, smart locks, members and borrowing records through the web dashboard.

The system is connected to the TTLock Open Platform and controls real smart locks.

Live: <https://bnbu.hmcl-helper.cn>

## Admin Dashboard

The admin console is designed for desktop; open `/admin` on a computer.

### TTLock and gateways

Enter the Open Platform Client ID and Client Secret under **其他 → 通通锁** and link your TTLock App account. Once configured, click **同步通通锁** under **柜子与设备** to assign locks to cabinets. Each cabinet has its own QR entry and can hold several locks.

Adding a lock for the first time, or provisioning Wi-Fi on a G2 gateway, is done in the TTLock App next to the device; sync from the dashboard afterwards. The dashboard does not touch Bluetooth directly.

The lock **编辑** view adjusts the internal code, auto-lock, passage schedules, sound and other parameters:

- The name in the dashboard is local to this system and does not change the lock name in the TTLock App.
- Saving parameters pushes them to the lock and reads them back. If the result is unknown, click **查询配置** for the latest values before retrying.
- A success message only means the command was sent. Whether the cabinet door is actually closed must be confirmed on site.

The gateway **编辑** view renames the local entry and shows gateway type, MAC, network, online state and the signal strength of associated locks. Gateway–lock associations are discovered by TTLock and synced on query.

### AI icon generation

Configure the AI provider API and Key under **其他 → 图标生成** to generate tool icons from prompts. The pipeline supports prompt rewriting, image generation and review, with model, reasoning and image quality options; apply an icon once you are happy with it, or upload an image directly.

The Key is stored encrypted. Changing the provider or base URL requires re-entering it.

### Members and sign-in modes

Two sign-in modes, effective immediately when switched:

- **SSO**: uses the school's unified identity provider and records only members who signed in with a returned student ID.
- **Roster**: bulk-imports name, student ID and room number from an Excel template; only listed members can sign in.

Borrowing records can be filtered and exported to Excel in the dashboard. The admin password is changed at the bottom of **其他**.

## Development

Requires Node.js 24 and Python 3.12+.

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python -m venv .venv
```

Install backend dependencies and start the server. Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

macOS / Linux:

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```sh
npm --prefix frontend run dev
```

Open <http://127.0.0.1:5173>. The frontend forwards `/api` to port `8000` on the same machine.

Tests:

```sh
npm --prefix frontend test
cd backend && python -m pytest -q
```

## License

AGPL-3.0, see [LICENSE](LICENSE).
