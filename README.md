# PortPolio (Flask)

## Run locally

```powershell
py -m pip install -r requirements.txt
py app.py
```

Open `http://127.0.0.1:5000`.

## Deploy on Render

This repo includes `render.yaml`, so you can deploy as a Render Blueprint.

### Start command

Render runs:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

### Required environment variables

- `FLASK_SECRET_KEY` (Render generates this if you use the Blueprint)
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GITHUB_REDIRECT_URI` must be your Render URL + `/callback` (example: `https://YOUR-SERVICE.onrender.com/callback`)

### Note about SQLite

This app uses `portpolio.db` (SQLite). On Render, the filesystem is ephemeral unless you attach a persistent disk.

