<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="Positive Education Hall · Shared Toolbox" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/tongtongsuo"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center"><a href="README.md">中文</a> · English</p>

A shared toolbox borrowing, return and management system for Positive Education Hall at BNBU.

## Admin

Open `/admin` on a computer. Phones display a desktop access notice. Menu and button labels below follow the Chinese admin interface.

### TTLock and cabinets

Under **其他 → 通通锁**, enter the Client ID and Client Secret from the TTLock Open Platform, then connect the TTLock App account that owns the lock and G2 gateway. Application credentials save automatically when you leave the fields. The Secret is encrypted, shown as asterisks once configured, and replaced when you enter a new value.

Click **同步通通锁** under **柜子与设备** to import devices and gateway associations, then assign locks to cabinets. Each cabinet has its own QR entry and can contain multiple locks. Gateway names, IDs and associations reflect the last device sync; sync again after changing them in the App.

Status results include their query time. A successful status request, an expired auto-lock timer or a locked bolt does not confirm that the cabinet door is closed.

### Lock settings

Click **编辑** on a lock to expand its settings below the list:

- **Basic information**: internal code, local name, cabinet and notes. Use **保存基本信息** to save. The local name does not change the lock name in the App.
- **Device information**: read-only TTLock ID, battery level, model, firmware, hardware revision and associated gateways.
- **Device settings**: auto-lock delay, passage mode and schedules, sound, volume, opening direction, privacy lock, tamper alarm and reset button. A setting is editable only when the lock supports it and the platform returns a valid current value. Disabled fields do not indicate that the feature is switched off.

Auto-lock accepts 1–32767 seconds, or `-1` to disable it on supported locks; `0` is invalid. Passage mode supports up to five schedules, each with selected weekdays and start/end times or an all-day setting. Configure at least one schedule before enabling it. Automatic unlocking at the start of a schedule is available when supported. Door-not-closed alarm duration currently remains disabled here; configure it in the TTLock App.

**查询配置** reads the latest platform values, including changes made in the App. After editing, click **保存设备配置** to apply changes and read them back for confirmation. If a result is unknown, partially failed or unconfirmed, query and check the current values before submitting again.

While the device page is visible, devices sync approximately every 30 minutes, statuses refresh every 5 minutes, and an expanded lock's settings refresh every 3 minutes. Automatic parameter refresh skips unsaved changes; manual queries retain parameter drafts. These tasks pause when you leave the device page or put the browser in the background.

### Icon generation

Under **其他 → 图标生成**, configure the provider, API base URL and API Key. Use the API root, such as `https://api.example.com/v1`. Keys are encrypted and never returned to the browser. Asterisks indicate a saved key; leave the field empty to retain it or enter a new key to replace it. Changing the provider or base URL requires a new key. Valid changes save automatically and show a toast.

The pipeline is optional prompt rewriting → image generation → optional image review. The image model defaults to `gpt-image-2.5-sunburst`. Rewriting and review can independently use `gpt-5.6-luna`, `gpt-6-astra`, `gpt-6-luna` or `gpt-6.1-sol`; both default to `gpt-6.1-sol` with `xhigh` reasoning.

Reasoning options are `low`, `mid`, `high`, `xhigh`, `max` and `ultra`; `mid` is sent to the provider as `medium`. The provider must support the selected models, reasoning levels, image quality and background options. Output dimensions are not specified.

Advanced settings include a 30–900 second request timeout and a prompt template. Keep `{name}` in the template; `{description}` inserts the tool description. **生成测试图** consumes provider quota without assigning an image to a tool. In the tool editor, preview a generated icon, click **使用此图标**, then save the tool. Image uploads remain available.

### Members and accounts

The login mode in **成员管理** saves automatically. SSO validates school credentials and lists users who have signed in with a returned student ID. Roster login checks name, student ID and room number against imported members; all imported members are listed.

Download the template and complete the **成员** sheet with name, student ID and room number. Keep IDs and room numbers as text. International members should use their full name in CAPITAL LETTERS, including spaces. Imports accept ordinary `.xlsx` files up to 2 MiB and 5000 members, without formulas or duplicate student IDs. Matching IDs are updated, new IDs are added, and existing members absent from the file are retained.

Filtering the member source does not change the login mode. **查看借阅记录** opens records filtered by that member's student ID. Borrowing records support filtering, sorting and Excel export. The administrator password can be changed at the bottom of **其他**.

## Develop

Requires Node.js 24 and Python 3.12+.

Install and build the frontend, then create a Python virtual environment:

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python -m venv .venv
```

Install backend dependencies and start the development server. Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

macOS / Linux:

```sh
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

Start

```sh
npm --prefix frontend run dev
```

Open <http://127.0.0.1:5173>. The frontend forwards `/api` requests to the local backend on port `8000`.
