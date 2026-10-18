<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="正雅苑 · 共享工具箱" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/tongtongsuo"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center">中文 · <a href="README.en.md">English</a></p>

面向 BNBU 正雅苑的共享工具箱借还与管理系统。

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
