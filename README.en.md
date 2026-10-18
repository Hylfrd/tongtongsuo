<p align="center">
  <img src="docs/assets/readme-toolbox-logo.png" alt="Positive Education Hall · Shared Toolbox" width="800">
</p>

<p align="center">
  <a href="https://github.com/Hylfrd/tongtongsuo"><img src="https://img.shields.io/badge/GitHub-repo-007ec6?logo=github" alt="GitHub repository"></a>
</p>

<p align="center"><a href="README.md">中文</a> · English</p>

A shared toolbox borrowing, return and management system for Positive Education Hall at BNBU.

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
