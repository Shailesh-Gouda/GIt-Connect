import os
import secrets
import sqlite3
import base64
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse

from flask import Flask, flash, redirect, render_template, request, session, url_for
import requests
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-env")

# 🔐 GitHub OAuth (set via env vars in production; do not hardcode secrets)
CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.environ.get("GITHUB_REDIRECT_URI")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL")
# NOTE: GitHub may require `repo` scope to create repositories (even public) on some accounts.
# You can override via env var `GITHUB_OAUTH_SCOPE`.
GITHUB_OAUTH_SCOPE = os.environ.get("GITHUB_OAUTH_SCOPE", "read:user user:email repo")
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

# Trust Render/Reverse-proxy headers so external URLs and scheme are correct.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
if PUBLIC_BASE_URL and PUBLIC_BASE_URL.strip().lower().startswith("https://"):
    app.config.setdefault("SESSION_COOKIE_SECURE", True)

# 📁 Upload config
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
RESUME_UPLOAD_FOLDER = os.path.join(UPLOAD_FOLDER, "resumes")
os.makedirs(RESUME_UPLOAD_FOLDER, exist_ok=True)

DB_PATH = os.path.join(os.path.dirname(__file__), "portpolio.db")


def _oauth_configured() -> bool:
    if not CLIENT_ID or not CLIENT_SECRET:
        return False
    if CLIENT_ID.startswith("YOUR_") or CLIENT_SECRET.startswith("YOUR_"):
        return False
    if CLIENT_ID == CLIENT_SECRET:
        return False
    return True


def _callback_url_for_oauth() -> str:
    if GITHUB_REDIRECT_URI:
        return GITHUB_REDIRECT_URI
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/") + url_for("callback")
    return url_for("callback", _external=True)


@app.before_request
def _enforce_public_base_url():
    if not PUBLIC_BASE_URL:
        return None

    try:
        target = urlparse(PUBLIC_BASE_URL)
    except Exception:
        return None

    if not target.scheme or not target.netloc:
        return None

    current_scheme = request.scheme
    current_host = request.host

    if current_scheme == target.scheme and current_host == target.netloc:
        return None

    # Preserve path/query; avoid trailing '?' Flask can append in full_path.
    path = request.path or "/"
    qs = request.query_string.decode("utf-8", "ignore")
    new_url = f"{target.scheme}://{target.netloc}{path}"
    if qs:
        new_url = f"{new_url}?{qs}"
    return redirect(new_url, code=302)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolios (
              github_login TEXT PRIMARY KEY,
              name TEXT,
              email TEXT,
              phone TEXT,
              location TEXT,
              website TEXT,
              github_url TEXT,
              linkedin TEXT,
              instagram TEXT,
              twitter TEXT,
              bio TEXT,
              skills TEXT,
              college TEXT,
              cgpa TEXT,
              objective TEXT,
              tagline TEXT,
              languages TEXT,
              hobbies TEXT,
              why_me TEXT,
              degree TEXT,
              branch TEXT,
              graduation_year TEXT,
              leetcode_url TEXT,
              hackerrank_url TEXT,
              achievements TEXT,
              certificates TEXT,
              resume_url TEXT,
              profile_pic TEXT,
              project_name TEXT,
              project_desc TEXT,
              repo_url TEXT,
              notes TEXT,
              updated_at TEXT
            )
            """
        )

        _ensure_columns(
            conn,
            "portfolios",
            {
                "phone": "TEXT",
                "location": "TEXT",
                "website": "TEXT",
                "github_url": "TEXT",
                "linkedin": "TEXT",
                "instagram": "TEXT",
                "twitter": "TEXT",
                "cgpa": "TEXT",
                "objective": "TEXT",
                "tagline": "TEXT",
                "languages": "TEXT",
                "hobbies": "TEXT",
                "why_me": "TEXT",
                "degree": "TEXT",
                "branch": "TEXT",
                "graduation_year": "TEXT",
                "leetcode_url": "TEXT",
                "hackerrank_url": "TEXT",
                "achievements": "TEXT",
                "certificates": "TEXT",
                "resume_url": "TEXT",
            },
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              github_login TEXT NOT NULL,
              name TEXT NOT NULL,
              description TEXT,
              repo_url TEXT,
              category TEXT,
              visibility TEXT,
              code_repo_url TEXT,
              code_path TEXT,
              image_path TEXT,
              pages_url TEXT,
              created_at TEXT
            )
            """
        )
        _ensure_columns(
            conn,
            "projects",
            {
                "category": "TEXT",
                "visibility": "TEXT",
                "code_repo_url": "TEXT",
                "code_path": "TEXT",
                "image_path": "TEXT",
                "pages_url": "TEXT",
            },
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              github_login TEXT NOT NULL,
              content TEXT NOT NULL,
              repo_name TEXT NOT NULL,
              file_path TEXT NOT NULL,
              commit_url TEXT,
              created_at TEXT
            )
            """
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, col_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def _load_portfolio(github_login: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM portfolios WHERE github_login = ?",
            (github_login,),
        ).fetchone()
    return dict(row) if row else None


def _list_projects(github_login: str, category: str | None = None) -> list[dict]:
    with _db() as conn:
        if category is None:
            rows = conn.execute(
                "SELECT id, name, description, repo_url, category, visibility, code_repo_url, code_path, image_path, pages_url, created_at FROM projects WHERE github_login = ? ORDER BY id DESC",
                (github_login,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, description, repo_url, category, visibility, code_repo_url, code_path, image_path, pages_url, created_at FROM projects WHERE github_login = ? AND category = ? ORDER BY id DESC",
                (github_login, category),
            ).fetchall()
    return [dict(r) for r in rows]


def _add_project(
    github_login: str,
    name: str,
    description: str | None,
    repo_url: str | None,
    category: str | None = None,
    visibility: str | None = None,
    code_repo_url: str | None = None,
    code_path: str | None = None,
    image_path: str | None = None,
    pages_url: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO projects (github_login, name, description, repo_url, category, visibility, code_repo_url, code_path, image_path, pages_url, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                github_login,
                name,
                description,
                repo_url,
                category or "",
                (visibility or "public").strip().lower(),
                code_repo_url or "",
                code_path or "",
                image_path or "",
                pages_url or "",
                now,
            ),
        )


def _list_notes(github_login: str) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, content, repo_name, file_path, commit_url, created_at FROM notes WHERE github_login = ? ORDER BY id DESC",
            (github_login,),
        ).fetchall()
    return [dict(r) for r in rows]


def _add_note(
    github_login: str,
    content: str,
    repo_name: str,
    file_path: str,
    commit_url: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO notes (github_login, content, repo_name, file_path, commit_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (github_login, content, repo_name, file_path, commit_url, now),
        )


def _save_portfolio(github_login: str, data: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO portfolios (
              github_login, name, email, phone, location, website, github_url, linkedin, instagram, twitter,
              bio, skills, college, cgpa, objective, tagline, languages, hobbies, why_me, degree, branch, graduation_year, leetcode_url, hackerrank_url,
              achievements, certificates, resume_url, profile_pic,
              project_name, project_desc, repo_url, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(github_login) DO UPDATE SET
              name=excluded.name,
              email=excluded.email,
              phone=excluded.phone,
              location=excluded.location,
              website=excluded.website,
              github_url=excluded.github_url,
              linkedin=excluded.linkedin,
              instagram=excluded.instagram,
              twitter=excluded.twitter,
              bio=excluded.bio,
              skills=excluded.skills,
              college=excluded.college,
              cgpa=excluded.cgpa,
              objective=excluded.objective,
              tagline=excluded.tagline,
              languages=excluded.languages,
              hobbies=excluded.hobbies,
              why_me=excluded.why_me,
              degree=excluded.degree,
              branch=excluded.branch,
              graduation_year=excluded.graduation_year,
              leetcode_url=excluded.leetcode_url,
              hackerrank_url=excluded.hackerrank_url,
              achievements=excluded.achievements,
              certificates=excluded.certificates,
              resume_url=excluded.resume_url,
              profile_pic=excluded.profile_pic,
              project_name=excluded.project_name,
              project_desc=excluded.project_desc,
              repo_url=excluded.repo_url,
              notes=excluded.notes,
              updated_at=excluded.updated_at
            """,
            (
                github_login,
                data.get("name"),
                data.get("email"),
                data.get("phone"),
                data.get("location"),
                data.get("website"),
                data.get("github_url"),
                data.get("linkedin"),
                data.get("instagram"),
                data.get("twitter"),
                data.get("bio"),
                data.get("skills"),
                data.get("college"),
                data.get("cgpa"),
                data.get("objective"),
                data.get("tagline"),
                data.get("languages"),
                data.get("hobbies"),
                data.get("why_me"),
                data.get("degree"),
                data.get("branch"),
                data.get("graduation_year"),
                data.get("leetcode_url"),
                data.get("hackerrank_url"),
                data.get("achievements"),
                data.get("certificates"),
                data.get("resume_url"),
                data.get("profile_pic"),
                data.get("project_name"),
                data.get("project_desc"),
                data.get("repo_url"),
                data.get("notes"),
                now,
            ),
        )


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "portpolio-app",
    }


def _gh_headers_optional(token: str | None) -> dict:
    if token:
        return _gh_headers(token)
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "portpolio-app",
    }


def _gh_repo_exists(token: str, owner: str, repo_name: str) -> bool:
    res = requests.get(
        f"https://api.github.com/repos/{owner}/{repo_name}",
        headers=_gh_headers(token),
        timeout=15,
    )
    if res.status_code == 404:
        return False
    return res.ok


def _gh_create_repo(token: str, name: str, description: str | None = None) -> tuple[bool, str | None, str | None]:
    res = requests.post(
        "https://api.github.com/user/repos",
        json={"name": name, "description": description or "", "private": False},
        headers=_gh_headers(token),
        timeout=15,
    )
    if not res.ok:
        status = res.status_code
        try:
            message = (res.json() or {}).get("message") or "GitHub repo create failed."
        except Exception:
            message = "GitHub repo create failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        if message.strip().lower() == "not found" or status in (401, 403, 404):
            message = (
                "GitHub API returned 'Not Found' / no permission to create repos. "
                "Logout, then login again with scope including `repo` (required for creating repos on some accounts). "
                f"Requested scope: `{GITHUB_OAUTH_SCOPE}`. Granted: `{granted_scopes or 'unknown'}`. (HTTP {status})"
            )
        return False, None, message

    repo_json = res.json() or {}
    return True, repo_json.get("html_url"), None


def _gh_ensure_repo(token: str, owner: str, name: str, description: str | None = None) -> tuple[bool, str | None, str | None]:
    if _gh_repo_exists(token, owner, name):
        return True, f"https://github.com/{owner}/{name}", None
    return _gh_create_repo(token, name, description=description)


def _gh_create_repo_with_visibility(
    token: str,
    name: str,
    description: str | None = None,
    private: bool = False,
) -> tuple[bool, str | None, str | None]:
    res = requests.post(
        "https://api.github.com/user/repos",
        json={"name": name, "description": description or "", "private": bool(private)},
        headers=_gh_headers(token),
        timeout=15,
    )
    if not res.ok:
        status = res.status_code
        try:
            message = (res.json() or {}).get("message") or "GitHub repo create failed."
        except Exception:
            message = "GitHub repo create failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        if message.strip().lower() == "not found" or status in (401, 403, 404):
            message = (
                "GitHub API returned 'Not Found' / no permission to create repos. "
                "Logout, then login again with scope including `repo` (required for creating repos on some accounts). "
                f"Requested scope: `{GITHUB_OAUTH_SCOPE}`. Granted: `{granted_scopes or 'unknown'}`. (HTTP {status})"
            )
        return False, None, message

    repo_json = res.json() or {}
    return True, repo_json.get("html_url"), None


def _gh_ensure_repo_with_visibility(
    token: str,
    owner: str,
    name: str,
    description: str | None = None,
    private: bool = False,
) -> tuple[bool, str | None, str | None]:
    if _gh_repo_exists(token, owner, name):
        return True, f"https://github.com/{owner}/{name}", None
    return _gh_create_repo_with_visibility(token, name, description=description, private=private)


def _gh_create_note_file(token: str, owner: str, repo_name: str, content: str) -> tuple[bool, str | None, str | None, str | None]:
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    file_path = f"notes/{created_at}.md"
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"

    payload = {
        "message": f"Add note {created_at}",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }

    res = requests.put(api_url, json=payload, headers=_gh_headers(token), timeout=15)
    if not res.ok:
        status = res.status_code
        try:
            message = (res.json() or {}).get("message") or "GitHub note commit failed."
        except Exception:
            message = "GitHub note commit failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        if message.strip().lower() == "not found" or status in (401, 403, 404):
            message = (
                "GitHub API returned 'Not Found' / no permission to commit. "
                "Logout, then login again with scope including `public_repo` (or `repo`). "
                f"Requested scope: `{GITHUB_OAUTH_SCOPE}`. Granted: `{granted_scopes or 'unknown'}`. (HTTP {status})"
            )
        return False, None, None, message

    data = res.json() or {}
    commit_url = None
    if isinstance(data.get("commit"), dict):
        commit_url = data["commit"].get("html_url") or data["commit"].get("url")
    return True, file_path, commit_url, None


def _gh_upsert_file(
    token: str,
    owner: str,
    repo_name: str,
    file_path: str,
    content: str,
    message: str,
) -> tuple[bool, str | None, str | None]:
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"

    sha = None
    existing = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if existing.status_code == 200:
        data = existing.json() or {}
        sha = data.get("sha")
    elif existing.status_code != 404 and not existing.ok:
        try:
            message2 = (existing.json() or {}).get("message") or "GitHub file read failed."
        except Exception:
            message2 = "GitHub file read failed."
        return False, None, message2

    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    res = requests.put(api_url, json=payload, headers=_gh_headers(token), timeout=15)
    if not res.ok:
        status = res.status_code
        try:
            message3 = (res.json() or {}).get("message") or "GitHub file commit failed."
        except Exception:
            message3 = "GitHub file commit failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        if message3.strip().lower() == "not found" or status in (401, 403, 404):
            message3 = (
                "GitHub API returned 'Not Found' / no permission to commit. "
                "Logout, then login again with scope including `public_repo` (or `repo`). "
                f"Requested scope: `{GITHUB_OAUTH_SCOPE}`. Granted: `{granted_scopes or 'unknown'}`. (HTTP {status})"
            )
        return False, None, message3

    data = res.json() or {}
    commit_url = None
    if isinstance(data.get("commit"), dict):
        commit_url = data["commit"].get("html_url") or data["commit"].get("url")
    return True, commit_url, None


def _gh_upsert_bytes(
    token: str,
    owner: str,
    repo_name: str,
    file_path: str,
    content_bytes: bytes,
    message: str,
) -> tuple[bool, str | None, str | None]:
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"

    sha = None
    existing = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if existing.status_code == 200:
        data = existing.json() or {}
        sha = data.get("sha")
    elif existing.status_code != 404 and not existing.ok:
        try:
            message2 = (existing.json() or {}).get("message") or "GitHub file read failed."
        except Exception:
            message2 = "GitHub file read failed."
        return False, None, message2

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
    }
    if sha:
        payload["sha"] = sha

    res = requests.put(api_url, json=payload, headers=_gh_headers(token), timeout=15)
    if not res.ok:
        status = res.status_code
        try:
            message3 = (res.json() or {}).get("message") or "GitHub file commit failed."
        except Exception:
            message3 = "GitHub file commit failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        if message3.strip().lower() == "not found" or status in (401, 403, 404):
            message3 = (
                "GitHub API returned 'Not Found' / no permission to commit. "
                "Logout, then login again with scope including `public_repo` (or `repo`). "
                f"Requested scope: `{GITHUB_OAUTH_SCOPE}`. Granted: `{granted_scopes or 'unknown'}`. (HTTP {status})"
            )
        return False, None, message3

    data = res.json() or {}
    commit_url = None
    if isinstance(data.get("commit"), dict):
        commit_url = data["commit"].get("html_url") or data["commit"].get("url")
    return True, commit_url, None


def _gh_delete_file(token: str, owner: str, repo_name: str, file_path: str, sha: str, message: str) -> tuple[bool, str | None]:
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"
    payload = {"message": message, "sha": sha}
    res = requests.delete(api_url, json=payload, headers=_gh_headers(token), timeout=15)
    if res.status_code in (200, 204):
        return True, None
    try:
        msg = (res.json() or {}).get("message") or "GitHub file delete failed."
    except Exception:
        msg = "GitHub file delete failed."
    return False, msg


def _gh_delete_path_recursive(token: str, owner: str, repo_name: str, path: str) -> tuple[bool, str | None]:
    path = (path or "").strip().strip("/")
    if not path:
        return False, "Invalid path."

    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{path}"
    res = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if res.status_code == 404:
        return True, None
    if not res.ok:
        try:
            msg = (res.json() or {}).get("message") or "GitHub path read failed."
        except Exception:
            msg = "GitHub path read failed."
        return False, msg

    try:
        data = res.json()
    except Exception:
        return False, "GitHub returned an invalid response while reading path contents."
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            item_path = item.get("path")
            if not item_path:
                continue
            if item_type == "dir":
                ok, err = _gh_delete_path_recursive(token, owner, repo_name, item_path)
                if not ok:
                    return False, err
            elif item_type == "file":
                sha = item.get("sha")
                if not sha:
                    continue
                ok, err = _gh_delete_file(token, owner, repo_name, item_path, sha, f"Delete {item_path}")
                if not ok:
                    return False, err
        return True, None

    if isinstance(data, dict) and data.get("type") == "file":
        sha = data.get("sha")
        if not sha:
            return False, "Missing file SHA."
        return _gh_delete_file(token, owner, repo_name, path, sha, f"Delete {path}")

    return False, "Unsupported GitHub content type."


def _gh_list_files_in_prefix(
    token: str | None,
    owner: str,
    repo_name: str,
    prefix: str,
) -> tuple[bool, list[str] | None, str | None]:
    prefix = (prefix or "").strip().strip("/")

    repo_res = requests.get(
        f"https://api.github.com/repos/{owner}/{repo_name}",
        headers=_gh_headers_optional(token),
        timeout=15,
    )
    if not repo_res.ok:
        try:
            msg = (repo_res.json() or {}).get("message") or "Could not read repo info."
        except Exception:
            msg = "Could not read repo info."
        return False, None, msg

    default_branch = (repo_res.json() or {}).get("default_branch") or "main"

    tree_res = requests.get(
        f"https://api.github.com/repos/{owner}/{repo_name}/git/trees/{default_branch}",
        headers=_gh_headers_optional(token),
        params={"recursive": "1"},
        timeout=15,
    )
    if not tree_res.ok:
        try:
            msg = (tree_res.json() or {}).get("message") or "Could not read repo tree."
        except Exception:
            msg = "Could not read repo tree."
        return False, None, msg

    tree = (tree_res.json() or {}).get("tree") or []
    files: list[str] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "blob":
            continue
        path = (item.get("path") or "").strip()
        if not path:
            continue
        if prefix:
            if not (path == prefix or path.startswith(prefix + "/")):
                continue
        files.append(path)

    files.sort()
    return True, files, None


def _gh_get_text_file(
    token: str | None,
    owner: str,
    repo_name: str,
    file_path: str,
) -> tuple[bool, str | None, str | None]:
    file_path = (file_path or "").strip().strip("/")
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"
    res = requests.get(api_url, headers=_gh_headers_optional(token), timeout=15)
    if not res.ok:
        if token is None:
            for branch in ("main", "master"):
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}/{file_path}"
                raw_res = requests.get(raw_url, timeout=15)
                if not raw_res.ok:
                    continue
                raw = raw_res.content or b""
                if len(raw) > 250_000:
                    return False, None, "File too large to preview here."
                try:
                    return True, raw.decode("utf-8"), None
                except Exception:
                    return False, None, "File is not UTF-8 text."
        try:
            msg = (res.json() or {}).get("message") or "Could not read file."
        except Exception:
            msg = "Could not read file."
        return False, None, msg

    try:
        data = res.json() or {}
    except Exception:
        return False, None, "GitHub returned an invalid response while reading the file."
    content_b64 = data.get("content") or ""
    encoding = data.get("encoding") or ""
    if encoding != "base64" or not content_b64:
        return False, None, "Unsupported file content."

    try:
        raw = base64.b64decode(content_b64.encode("utf-8"), validate=False)
        if len(raw) > 250_000:
            return False, None, "File too large to preview here."
        text = raw.decode("utf-8")
    except Exception:
        return False, None, "File is not UTF-8 text."

    return True, text, None


def _gh_get_file_bytes(
    token: str | None,
    owner: str,
    repo_name: str,
    file_path: str,
) -> tuple[bool, bytes | None, str | None]:
    file_path = (file_path or "").strip().strip("/")
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"
    res = requests.get(api_url, headers=_gh_headers_optional(token), timeout=15)
    if not res.ok:
        if token is None:
            for branch in ("main", "master"):
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}/{file_path}"
                raw_res = requests.get(raw_url, timeout=15)
                if raw_res.ok:
                    return True, raw_res.content or b"", None
        try:
            msg = (res.json() or {}).get("message") or "Could not read file."
        except Exception:
            msg = "Could not read file."
        return False, None, msg

    try:
        data = res.json() or {}
    except Exception:
        return False, None, "GitHub returned an invalid response while reading the file."
    content_b64 = data.get("content") or ""
    encoding = data.get("encoding") or ""
    if encoding != "base64" or not content_b64:
        return False, None, "Unsupported file content."

    try:
        raw = base64.b64decode(content_b64.encode("utf-8"), validate=False)
    except Exception:
        return False, None, "Decode failed."

    return True, raw, None


def _gh_copy_prefix_between_repos(
    token: str,
    owner: str,
    src_repo: str,
    src_prefix: str,
    dst_repo: str,
    dst_prefix: str,
    message: str,
) -> tuple[bool, str | None]:
    src_prefix = (src_prefix or "").strip().strip("/")
    dst_prefix = (dst_prefix or "").strip().strip("/")
    if not src_prefix or not dst_prefix:
        return False, "Invalid project path."

    ok, files, err = _gh_list_files_in_prefix(token, owner, src_repo, src_prefix)
    if not ok or files is None:
        return False, err or "Could not list source files."

    for path in files:
        p = (path or "").strip().strip("/")
        if not p or not _is_safe_project_path(src_prefix, p):
            continue
        rel = p[len(src_prefix) :].lstrip("/")
        dst_path = dst_prefix if not rel else f"{dst_prefix}/{rel}"
        okb, raw, errb = _gh_get_file_bytes(token, owner, src_repo, p)
        if not okb or raw is None:
            return False, errb or f"Could not read {p}"
        oku, _, erru = _gh_upsert_bytes(token, owner, dst_repo, dst_path, raw, message)
        if not oku:
            return False, erru or f"Could not write {dst_path}"

    return True, None


def _gh_get_content_meta(
    token: str,
    owner: str,
    repo_name: str,
    file_path: str,
) -> tuple[bool, dict | None, str | None]:
    file_path = (file_path or "").strip().strip("/")
    api_url = f"https://api.github.com/repos/{owner}/{repo_name}/contents/{file_path}"
    res = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if not res.ok:
        try:
            msg = (res.json() or {}).get("message") or "Could not read file."
        except Exception:
            msg = "Could not read file."
        return False, None, msg

    try:
        data = res.json() or {}
    except Exception:
        return False, None, "GitHub returned an invalid response while reading the file."

    if not isinstance(data, dict):
        return False, None, "Unsupported GitHub content response."
    return True, data, None


def _normalize_rel_path(value: str) -> str:
    value = (value or "").strip().replace("\\", "/").strip("/")
    value = re.sub(r"/{2,}", "/", value)
    return value


def _gh_enable_pages(token: str, owner: str, repo: str, path: str = "/docs") -> tuple[bool, str | None, str | None]:
    api_url = f"https://api.github.com/repos/{owner}/{repo}/pages"

    # Use the repo's default branch (not always "main").
    repo_res = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers=_gh_headers(token),
        timeout=15,
    )
    default_branch = "main"
    if repo_res.ok:
        try:
            default_branch = (repo_res.json() or {}).get("default_branch") or "main"
        except Exception:
            default_branch = "main"

    source = {"branch": default_branch, "path": path}

    get_res = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if get_res.status_code == 404:
        res = requests.post(api_url, json={"source": source}, headers=_gh_headers(token), timeout=15)
    else:
        res = requests.put(api_url, json={"source": source}, headers=_gh_headers(token), timeout=15)

    if not res.ok:
        status = res.status_code
        try:
            msg = (res.json() or {}).get("message") or "GitHub Pages setup failed."
        except Exception:
            msg = "GitHub Pages setup failed."

        granted_scopes = (res.headers.get("X-OAuth-Scopes") or "").strip()
        msg = f"{msg} (HTTP {status}). Granted scopes: `{granted_scopes or 'unknown'}`."
        return False, None, msg

    pages_json = None
    try:
        pages_json = res.json() or {}
    except Exception:
        pages_json = None

    if isinstance(pages_json, dict):
        html_url = pages_json.get("html_url") or pages_json.get("url")
        return True, html_url, None

    # Some successful responses are 204 No Content; fetch details after enabling.
    details = requests.get(api_url, headers=_gh_headers(token), timeout=15)
    if details.ok:
        try:
            pages = details.json() or {}
            html_url = pages.get("html_url") or pages.get("url")
            return True, html_url, None
        except Exception:
            return True, None, None

    return True, None, None


def _repo_slug(name: str) -> str:
    value = (name or "").strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9._-]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:80]


def _parse_github_repo_name(repo_url: str, expected_owner: str) -> str | None:
    url = (repo_url or "").strip()
    if not url.startswith("https://github.com/"):
        return None
    parts = url.replace("https://github.com/", "").strip("/").split("/")
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if owner != expected_owner:
        return None
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return repo or None


_DEFAULT_BRANCH_CACHE: dict[tuple[str, str], tuple[str, float]] = {}


def _default_branch(owner: str, repo: str, token: str | None = None) -> str:
    key = (owner, repo)
    cached = _DEFAULT_BRANCH_CACHE.get(key)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers.update(_gh_headers(token))

    try:
        res = requests.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=10)
        if res.ok:
            branch = (res.json() or {}).get("default_branch") or "main"
        else:
            branch = "main"
    except Exception:
        branch = "main"

    # Cache for 1 hour to avoid rate limits.
    _DEFAULT_BRANCH_CACHE[key] = (branch, now + 3600)
    return branch


def _raw_github_url(owner: str, repo: str, path: str, branch: str = "main") -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path.lstrip('/')}"


def _blob_github_url(owner: str, repo: str, path: str, branch: str = "main") -> str:
    return f"https://github.com/{owner}/{repo}/blob/{branch}/{path.lstrip('/')}"


def _is_safe_project_path(code_prefix: str, requested_path: str) -> bool:
    prefix = (code_prefix or "").strip().replace("\\", "/").strip("/")
    path = (requested_path or "").strip().replace("\\", "/").strip("/")
    if not prefix or not path:
        return False

    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False

    return path == prefix or path.startswith(prefix + "/")


def _join_project_path(base_dir: str, rel: str) -> str | None:
    base_dir = (base_dir or "").replace("\\", "/").strip("/")
    rel = (rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith(("/", "\\")):
        return None
    if rel.startswith(("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:", "#")):
        return None

    base_parts = [p for p in base_dir.split("/") if p]
    rel_parts = [p for p in rel.split("/") if p]

    out: list[str] = []
    out.extend(base_parts)
    for p in rel_parts:
        if p in ("", "."):
            continue
        if p == "..":
            if out:
                out.pop()
            else:
                return None
        else:
            out.append(p)

    if any(p in ("", ".", "..") for p in out):
        return None
    return "/".join(out)


def _rewrite_preview_html(html: str, asset_base_url: str, html_file_path: str, code_prefix: str) -> str:
    html_file_path = (html_file_path or "").replace("\\", "/").strip("/")
    code_prefix = (code_prefix or "").replace("\\", "/").strip("/")

    if "/" in html_file_path:
        base_dir = "/".join(html_file_path.split("/")[:-1])
    else:
        base_dir = ""

    def repl(match: re.Match) -> str:
        attr = match.group(1)
        url = match.group(2) or ""
        url = url.strip()
        if not url:
            return match.group(0)
        if url.startswith(("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:", "#")):
            return match.group(0)
        if url.startswith("/"):
            return match.group(0)

        joined = _join_project_path(base_dir, url)
        if not joined or not _is_safe_project_path(code_prefix, joined):
            return match.group(0)

        return f'{attr}="{asset_base_url}{urlencode({"path": joined})}"'

    pattern = re.compile(r'\b(src|href)\s*=\s*"([^"]+)"', re.IGNORECASE)
    html2 = pattern.sub(repl, html)
    pattern2 = re.compile(r"\b(src|href)\s*=\s*'([^']+)'", re.IGNORECASE)
    html2 = pattern2.sub(repl, html2)
    return html2


def _require_token() -> str | None:
    return session.get("token")


def _require_login() -> tuple[str | None, str | None]:
    token = _require_token()
    github_login = session.get("github_login")
    if not token or not github_login:
        return None, None
    return token, github_login


# 🏠 HOME PAGE
@app.route("/")
def home():
    return render_template("1.html")


# 👨‍💻 Developer
@app.route("/developer")
def developer():
    if session.get("token"):
        return redirect(url_for("dashboard"))
    return render_template("2.html", oauth_configured=_oauth_configured())


# 👀 User (placeholder)
@app.route("/user")
def user():
    return render_template("user.html")


# 🔐 GitHub Login
@app.route("/login")
def login():
    if not _oauth_configured():
        flash("GitHub login is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.", "error")
        return redirect(url_for("developer"))

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state

    redirect_uri = _callback_url_for_oauth()
    params = {
        "client_id": CLIENT_ID,
        "scope": GITHUB_OAUTH_SCOPE,
        "state": state,
        "redirect_uri": redirect_uri,
    }
    params["prompt"] = "consent"
    return redirect(f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}")


# 🔁 GitHub Callback
@app.route("/callback")
def callback():
    if request.args.get("error"):
        flash(request.args.get("error_description") or request.args.get("error"), "error")
        return redirect(url_for("developer"))

    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)

    if not code:
        flash("GitHub callback missing ?code= parameter.", "error")
        return redirect(url_for("developer"))

    if not expected_state or not state or state != expected_state:
        app.logger.warning(
            "OAuth state mismatch. host_url=%s state=%s expected=%s redirect_uri=%s",
            request.host_url,
            state,
            expected_state,
            _callback_url_for_oauth(),
        )
        flash(
            "Login failed (invalid OAuth state). Make sure you start login and complete the callback on the SAME domain (onrender vs custom domain, http vs https, www vs non-www).",
            "error",
        )
        return redirect(url_for("developer"))

    redirect_uri = _callback_url_for_oauth()
    token_res = requests.post(
        GITHUB_TOKEN_URL,
        headers={"Accept": "application/json"},
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code
            ,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )

    if not token_res.ok:
        app.logger.warning("GitHub token exchange failed. status=%s body=%s", token_res.status_code, token_res.text[:500])
        flash("GitHub token exchange failed. Please try again.", "error")
        return redirect(url_for("developer"))

    access_token = (token_res.json() or {}).get("access_token")
    if not access_token:
        app.logger.warning("GitHub token missing in response. body=%s", token_res.text[:500])
        flash("GitHub token was not returned. Check OAuth app settings.", "error")
        return redirect(url_for("developer"))

    session["token"] = access_token

    return redirect("/dashboard")


# 📄 FORM PAGE
@app.route("/dashboard")
def dashboard():
    token = session.get("token")

    if not token:
        flash("Please login with GitHub first.", "info")
        return redirect(url_for("developer"))

    user_res = requests.get(GITHUB_USER_URL, headers=_gh_headers(token), timeout=15)

    if not user_res.ok:
        session.pop("token", None)
        flash("GitHub session expired or invalid. Please login again.", "error")
        return redirect(url_for("developer"))

    user = user_res.json() or {}
    session["oauth_scopes"] = (user_res.headers.get("X-OAuth-Scopes") or "").strip()

    github_login = user.get("login")
    if github_login:
        session["github_login"] = github_login
        existing = _load_portfolio(github_login)
        if existing and not request.args.get("edit"):
            return redirect(url_for("portfolio", github_login=github_login))

    existing = _load_portfolio(github_login) if github_login else None
    edit_mode = bool(existing) and bool(request.args.get("edit"))
    return render_template("3.html", user=user, existing=existing, edit_mode=edit_mode)


# 💾 SAVE + CREATE REPO + SHOW PORTFOLIO
@app.route("/save", methods=["POST"])
def save():
    token = session.get("token")
    github_login = session.get("github_login") or request.form.get("github")

    if not token or not github_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    name = request.form.get("name")
    bio = request.form.get("bio")
    skills = request.form.get("skills")
    project_name = request.form.get("project_name") or request.form.get("project_title")
    project_desc = request.form.get("project_desc")
    notes_text = request.form.get("notes")
    email = request.form.get("email")
    college = request.form.get("college")
    phone = request.form.get("phone")
    location = request.form.get("location")
    website = request.form.get("website")
    github_url = request.form.get("github_url")
    linkedin = request.form.get("linkedin")
    instagram = request.form.get("instagram")
    twitter = request.form.get("twitter")
    cgpa = request.form.get("cgpa")
    objective = request.form.get("objective")
    tagline = request.form.get("tagline")
    languages = request.form.get("languages")
    hobbies = request.form.get("hobbies")
    why_me = request.form.get("why_me")
    degree = request.form.get("degree")
    branch = request.form.get("branch")
    graduation_year = request.form.get("graduation_year")
    leetcode_url = request.form.get("leetcode_url")
    hackerrank_url = request.form.get("hackerrank_url")
    achievements = request.form.get("achievements")
    certificates = request.form.get("certificates")
    resume_url = ""

    # 📷 IMAGE: file upload (preferred)
    profile_pic_url = (request.form.get("profile_pic") or "").strip()
    file = request.files.get("profile_pic_file")
    filename = None

    if file and file.filename != "":
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))

    existing = _load_portfolio(github_login)
    is_edit_mode = request.form.get("edit_mode") == "1" and bool(existing)

    if filename:
        profile_pic = f"/static/uploads/{filename}"
    elif is_edit_mode:
        profile_pic = (existing.get("profile_pic") or "") if existing else ""
    else:
        profile_pic = profile_pic_url

    if is_edit_mode:
        resume_url = (existing.get("resume_url") or "") if existing else ""

    resume_file = request.files.get("resume_file")
    resume_filename = None
    if resume_file and resume_file.filename:
        resume_filename = secure_filename(resume_file.filename)
        if resume_filename:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            root, ext = os.path.splitext(resume_filename)
            resume_filename = f"{root[:40]}_{stamp}{ext}".replace(" ", "_")
            resume_file.save(os.path.join(RESUME_UPLOAD_FOLDER, resume_filename))

    if resume_filename:
        resume_url = f"/static/uploads/resumes/{resume_filename}"

    # 🚀 CREATE GITHUB REPO (skip during edit mode to avoid duplicates)
    repo_url = existing.get("repo_url") if existing else ""

    if (not is_edit_mode) and token and project_name:
        repo_name = _repo_slug(project_name)
        if repo_name:
            ok, created_repo_url, err = _gh_create_repo(token, repo_name, description=project_desc)
            if ok and created_repo_url:
                repo_url = created_repo_url
                _add_project(github_login, project_name, project_desc, repo_url)
            else:
                flash(err or "Could not create project repo. You can try again from the portfolio page.", "error")
        else:
            flash("Project name has invalid characters. Repo not created.", "error")

    # 📦 DATA FOR PORTFOLIO
    projects = [
        {
            "name": project_name,
            "desc": project_desc,
            "image": None,
            "repo": repo_url
        }
    ]

    notes = [notes_text] if notes_text else []

    if notes_text and (not is_edit_mode):
        notes_repo = "notes"
        ok, _, err = _gh_ensure_repo(token, github_login, notes_repo, description="My notes")
        if ok:
            ok2, file_path, commit_url, err2 = _gh_create_note_file(token, github_login, notes_repo, notes_text)
            if ok2 and file_path:
                _add_note(github_login, notes_text, notes_repo, file_path, commit_url)
            else:
                flash(err2 or "Could not save note to GitHub. You can add it later from the portfolio page.", "error")
        else:
            flash(err or "Could not create/find notes repo.", "error")

    _save_portfolio(
        github_login,
        {
            "name": name,
            "email": email,
            "phone": phone,
            "location": location,
            "website": website,
            "github_url": github_url,
            "linkedin": linkedin,
            "instagram": instagram,
            "twitter": twitter,
            "bio": bio,
            "skills": skills,
            "college": college,
            "cgpa": cgpa,
            "objective": objective,
            "tagline": tagline,
            "languages": languages,
            "hobbies": hobbies,
            "why_me": why_me,
            "degree": degree,
            "branch": branch,
            "graduation_year": graduation_year,
            "leetcode_url": leetcode_url,
            "hackerrank_url": hackerrank_url,
            "achievements": achievements,
            "certificates": certificates,
            "resume_url": resume_url,
            "profile_pic": profile_pic,
            "project_name": project_name,
            "project_desc": project_desc,
            "repo_url": repo_url,
            "notes": notes_text,
        },
    )

    return redirect(url_for("portfolio", github_login=github_login))


@app.route("/portfolio/<github_login>")
def portfolio(github_login: str):
    data = _load_portfolio(github_login)
    if not data:
        # Check if user is trying to view their own portfolio or guest viewing
        is_own_profile = session.get("github_login") == github_login
        if is_own_profile:
            flash("No saved portfolio found. Please fill the form first.", "info")
            return redirect(url_for("dashboard"))
        else:
            # Guest viewing a non-existent portfolio
            flash(f"Portfolio for '{github_login}' not found. It may not exist yet.", "warning")
            return render_template(
                "4.html",
                name=github_login,
                email="",
                phone="",
                location="",
                website="",
                github_url="",
                linkedin="",
                instagram="",
                twitter="",
                college="",
                cgpa="",
                objective="",
                tagline="",
                languages="",
                hobbies="",
                why_me="",
                degree="",
                branch="",
                graduation_year="",
                leetcode_url="",
                hackerrank_url="",
                achievements="",
                certificates="",
                resume_url="",
                bio="",
                skills="",
                profile_pic="",
                projects=[],
                notes=[],
                github_login=github_login,
                can_edit=False,
            )

    projects = _list_projects(github_login)
    if not projects and data.get("project_name"):
        projects = [
            {
                "name": data.get("project_name"),
                "description": data.get("project_desc"),
                "repo_url": data.get("repo_url"),
                "created_at": data.get("updated_at"),
            }
        ]

    notes = _list_notes(github_login)
    if not notes and data.get("notes"):
        notes = [
            {
                "content": data.get("notes"),
                "repo_name": "notes",
                "file_path": "",
                "commit_url": "",
                "created_at": data.get("updated_at"),
            }
        ]

    return render_template(
        "4.html",
        name=data.get("name") or github_login,
        email=data.get("email") or "",
        phone=data.get("phone") or "",
        location=data.get("location") or "",
        website=data.get("website") or "",
        github_url=data.get("github_url") or "",
        linkedin=data.get("linkedin") or "",
        instagram=data.get("instagram") or "",
        twitter=data.get("twitter") or "",
        college=data.get("college") or "",
        cgpa=data.get("cgpa") or "",
        objective=data.get("objective") or "",
        tagline=data.get("tagline") or "",
        languages=data.get("languages") or "",
        hobbies=data.get("hobbies") or "",
        why_me=data.get("why_me") or "",
        degree=data.get("degree") or "",
        branch=data.get("branch") or "",
        graduation_year=data.get("graduation_year") or "",
        leetcode_url=data.get("leetcode_url") or "",
        hackerrank_url=data.get("hackerrank_url") or "",
        achievements=data.get("achievements") or "",
        certificates=data.get("certificates") or "",
        resume_url=data.get("resume_url") or "",
        bio=data.get("bio") or "",
        skills=data.get("skills") or "",
        profile_pic=data.get("profile_pic") or "",
        projects=projects,
        notes=notes,
        github_login=github_login,
        can_edit=(session.get("github_login") == github_login and session.get("token") is not None),
    )


@app.route("/portfolio/<github_login>/projects")
def portfolio_projects(github_login: str):
    data = _load_portfolio(github_login)
    if not data:
        flash("No saved portfolio found. Please fill the form first.", "info")
        return redirect(url_for("dashboard"))

    projects = _list_projects(github_login)
    if not projects and data.get("project_name"):
        projects = [
            {
                "name": data.get("project_name"),
                "description": data.get("project_desc"),
                "repo_url": data.get("repo_url"),
                "created_at": data.get("updated_at"),
            }
        ]

    return render_template(
        "projects.html",
        name=data.get("name") or github_login,
        projects=projects,
        github_login=github_login,
    )


@app.get("/portfolio/<github_login>/projects/<category>")
def portfolio_projects_category(github_login: str, category: str):
    data = _load_portfolio(github_login)
    if not data:
        flash("No saved portfolio found. Please fill the form first.", "info")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    projects = _list_projects(github_login, category=category_name)

    token = session.get("token")
    can_edit = session.get("github_login") == github_login and token is not None

    repo_name = _repo_slug(category_name)
    repo_url = None
    if can_edit and repo_name:
        ok, created_url, err = _gh_ensure_repo(token, github_login, repo_name, description=f"{category_name} projects")
        if ok:
            repo_url = created_url or f"https://github.com/{github_login}/{repo_name}"
            public_projects = [
                p for p in projects if (p.get("visibility") or "public").strip().lower() != "private"
            ]
            readme = _render_category_readme(category_name, public_projects, owner=github_login, category_repo=repo_name)
            ok2, _, err2 = _gh_upsert_file(
                token,
                github_login,
                repo_name,
                "README.md",
                readme,
                f"Update {category_name} projects list",
            )
            if not ok2:
                flash(err2 or "Could not update README in the category repo.", "error")
        else:
            flash(err or "Could not create/find the category repo.", "error")
            repo_url = None

    for p in projects:
        visibility = (p.get("visibility") or "public").strip().lower()
        p["visibility"] = visibility
        code_repo_url = (p.get("code_repo_url") or "").strip()
        code_path = (p.get("code_path") or "").strip()
        image_path = (p.get("image_path") or "").strip()

        can_view_code = visibility != "private" or bool(can_edit)
        p["can_view_code"] = can_view_code

        browse_url = ""
        if can_view_code and code_repo_url:
            if code_path and code_repo_url.startswith("https://github.com/"):
                browse_url = f"{code_repo_url}/tree/main/{code_path.strip('/')}"
            else:
                browse_url = code_repo_url
        p["code_browse_url"] = browse_url

        p["pages_url"] = (p.get("pages_url") or "").strip()

        image_url = ""
        repo_name_for_asset = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
        if image_path and image_path.startswith(("http://", "https://")):
            # Allow storing a full image URL in the DB (useful if the repo default branch isn't "main").
            if visibility == "public" or can_edit:
                image_url = image_path
        elif repo_name_for_asset and image_path:
            branch = _default_branch(github_login, repo_name_for_asset, token if can_edit else None)
            if visibility == "public":
                image_url = _raw_github_url(github_login, repo_name_for_asset, image_path, branch=branch)
            elif can_edit:
                image_url = _blob_github_url(github_login, repo_name_for_asset, image_path, branch=branch)
        p["image_url"] = image_url

    return render_template(
        "category_projects.html",
        name=data.get("name") or github_login,
        github_login=github_login,
        category=category_name,
        projects=projects,
        repo_name=repo_name,
        repo_url=repo_url,
        can_edit=can_edit,
    )


@app.post("/portfolio/<github_login>/projects/<category>/add")
def add_project_to_category(github_login: str, category: str):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        flash("Category is required.", "error")
        return redirect(url_for("portfolio_projects", github_login=github_login))

    project_name = (request.form.get("project_name") or "").strip()
    project_desc = (request.form.get("project_desc") or "").strip()
    project_repo_url = (request.form.get("repo_url") or "").strip()
    visibility = (request.form.get("visibility") or "public").strip().lower()

    if not project_name:
        flash("Project name is required.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    if visibility not in ("public", "private"):
        visibility = "public"

    repo_name = _repo_slug(category_name)
    if not repo_name:
        flash("Category contains invalid characters.", "error")
        return redirect(url_for("portfolio_projects", github_login=github_login))

    project_slug = _repo_slug(project_name)
    if not project_slug:
        flash("Project name contains invalid characters.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    target_repo_name = repo_name
    target_repo_url = f"https://github.com/{github_login}/{repo_name}"
    target_private = False

    if visibility == "private":
        target_private = True
        target_repo_name = _repo_slug(f"{category_name}-{project_name}")
        if not target_repo_name:
            flash("Could not create a repo name for this private project.", "error")
            return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    ok, ensured_url, err = _gh_ensure_repo_with_visibility(
        token,
        github_login,
        target_repo_name,
        description=f"{category_name} projects" if not target_private else f"{category_name}: {project_name}",
        private=target_private,
    )
    if not ok:
        if target_private:
            flash(err or "Could not create/find the private project repo. You may need `repo` scope.", "error")
        else:
            flash(err or "Could not create/find the category repo.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    if ensured_url:
        target_repo_url = ensured_url

    base_path = f"projects/{project_slug}"
    code_path = base_path

    readme = _render_project_readme(category_name, project_name, project_desc)
    ok_r, _, err_r = _gh_upsert_file(
        token,
        github_login,
        target_repo_name,
        f"{base_path}/README.md",
        readme,
        f"Add {project_name}",
    )
    if not ok_r:
        flash(err_r or "Could not create project README in GitHub.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    uploaded_files = request.files.getlist("project_files")
    uploaded_count = 0
    skipped_count = 0

    image_file = request.files.get("project_image")
    saved_image_path = ""

    if image_file and getattr(image_file, "filename", ""):
        safe_image_name = secure_filename(image_file.filename)
        root, ext = os.path.splitext(safe_image_name)
        ext = (ext or ".png").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        img_bytes = image_file.read() or b""
        if 0 < len(img_bytes) <= 2_500_000:
            saved_image_path = f"{base_path}/cover{ext}"
            ok_img, _, err_img = _gh_upsert_bytes(
                token,
                github_login,
                target_repo_name,
                saved_image_path,
                img_bytes,
                f"Add cover image ({project_name})",
            )
            if not ok_img:
                saved_image_path = ""
                if err_img:
                    flash(err_img, "error")
        else:
            flash("Project image too large (max ~2.5MB).", "error")

    for f in uploaded_files:
        if not f or not getattr(f, "filename", ""):
            continue
        safe_name = secure_filename(f.filename)
        if not safe_name:
            skipped_count += 1
            continue

        blob = f.read()
        if blob is None:
            skipped_count += 1
            continue

        if len(blob) > 1_500_000:
            skipped_count += 1
            continue

        ok_f, _, err_f = _gh_upsert_bytes(
            token,
            github_login,
            target_repo_name,
            f"{base_path}/{safe_name}",
            blob,
            f"Add {safe_name} ({project_name})",
        )
        if ok_f:
            uploaded_count += 1
        else:
            skipped_count += 1
            if err_f:
                flash(err_f, "error")

    _add_project(
        github_login,
        project_name,
        project_desc or None,
        project_repo_url or None,
        category=category_name,
        visibility=visibility,
        code_repo_url=target_repo_url,
        code_path=code_path,
        image_path=saved_image_path or None,
    )

    projects = _list_projects(github_login, category=category_name)

    if visibility == "public":
        public_projects = [
            p for p in projects if (p.get("visibility") or "public").strip().lower() != "private"
        ]
        cat_readme = _render_category_readme(category_name, public_projects, owner=github_login, category_repo=repo_name)
        ok2, _, err2 = _gh_upsert_file(
            token,
            github_login,
            repo_name,
            "README.md",
            cat_readme,
            f"Add {project_name} to {category_name}",
        )
        if not ok2:
            flash(err2 or "Project saved, but category README update failed.", "error")

    if uploaded_count > 0:
        flash(f"Uploaded {uploaded_count} file(s) to GitHub.", "info")
    elif uploaded_files and uploaded_count == 0:
        flash("No files uploaded (invalid names or too large).", "info")

    if visibility == "private":
        flash("Project code is private (only you can see the repo).", "info")
    else:
        flash(f"Project added under {category_name}.", "info")

    return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/upload")
def upload_project_assets(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, github_login, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    visibility = (project.get("visibility") or "public").strip().lower()
    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip()
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None

    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    uploaded_files = request.files.getlist("project_files")
    uploaded_count = 0
    skipped_count = 0

    for f in uploaded_files:
        if not f or not getattr(f, "filename", ""):
            continue
        safe_name = secure_filename(f.filename)
        if not safe_name:
            skipped_count += 1
            continue
        blob = f.read()
        if not blob or len(blob) > 1_500_000:
            skipped_count += 1
            continue
        ok_f, _, err_f = _gh_upsert_bytes(
            token,
            github_login,
            repo_name,
            f"{code_path.strip('/')}/{safe_name}",
            blob,
            f"Add {safe_name}",
        )
        if ok_f:
            uploaded_count += 1
        else:
            skipped_count += 1
            if err_f:
                flash(err_f, "error")

    image_file = request.files.get("project_image")
    saved_image_path = None
    if image_file and getattr(image_file, "filename", ""):
        safe_image_name = secure_filename(image_file.filename)
        root, ext = os.path.splitext(safe_image_name)
        ext = (ext or ".png").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            ext = ".png"
        img_bytes = image_file.read() or b""
        if 0 < len(img_bytes) <= 2_500_000:
            saved_image_path = f"{code_path.strip('/')}/cover{ext}"
            ok_img, _, err_img = _gh_upsert_bytes(
                token,
                github_login,
                repo_name,
                saved_image_path,
                img_bytes,
                "Update cover image",
            )
            if not ok_img:
                saved_image_path = None
                if err_img:
                    flash(err_img, "error")
        else:
            flash("Project image too large (max ~2.5MB).", "error")

    if saved_image_path is not None:
        with _db() as conn:
            conn.execute(
                "UPDATE projects SET image_path = ? WHERE id = ? AND github_login = ?",
                (saved_image_path, project_id, github_login),
            )

    if uploaded_count > 0:
        flash(f"Uploaded {uploaded_count} file(s).", "info")
    elif uploaded_files and uploaded_count == 0:
        flash("No files uploaded (invalid names or too large).", "info")

    if visibility == "private":
        flash("Private project updated (only you can see the repo).", "info")

    return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))


@app.get("/portfolio/<github_login>/projects/<category>/<int:project_id>/code")
def view_project_code(github_login: str, category: str, project_id: int):
    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    data = _load_portfolio(github_login)
    if not data:
        flash("No saved portfolio found. Please fill the form first.", "info")
        return redirect(url_for("dashboard"))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path, image_path, pages_url FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    token = session.get("token")
    can_edit = session.get("github_login") == github_login and token is not None
    visibility = (project.get("visibility") or "public").strip().lower()

    if visibility == "private" and not can_edit:
        flash("Code is private.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    selected = (request.args.get("file") or "").strip().strip("/")
    if selected and not (selected == code_path or selected.startswith(code_path + "/")):
        selected = ""

    ok, files, err = _gh_list_files_in_prefix(token if can_edit else None, github_login, repo_name, code_path)
    if not ok or files is None:
        # For public viewers, degrade gracefully: still allow viewing a direct file URL.
        if can_edit:
            flash(err or "Could not list project files.", "error")
            return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))
        files = [selected] if selected else []
    else:
        if not selected and files:
            selected = files[0]

    html_files = [f for f in files if isinstance(f, str) and f.lower().endswith(".html")]
    preview_file = None
    if html_files:
        idx = f"{code_path}/index.html"
        preview_file = idx if idx in html_files else html_files[0]

    preview_target = None
    if preview_file:
        if selected and selected.lower().endswith(".html"):
            preview_target = selected
        else:
            preview_target = preview_file

    content = None
    if selected:
        ok2, text, err2 = _gh_get_text_file(token if can_edit else None, github_login, repo_name, selected)
        if ok2:
            content = text
        else:
            flash(err2 or "Could not load that file.", "error")
            content = None

    is_web_file = bool(selected) and selected.lower().endswith((".html", ".css", ".js"))
    pages_url = (project.get("pages_url") or "").strip() or None

    return render_template(
        "code_viewer.html",
        name=data.get("name") or github_login,
        github_login=github_login,
        category=category_name,
        project_name=project.get("name") or "",
        project_id=project_id,
        repo_url=code_repo_url,
        repo_name=repo_name,
        code_path=code_path,
        files=files,
        selected_file=selected,
        content=content,
        can_edit=can_edit,
        is_web_file=is_web_file,
        has_preview=bool(preview_file),
        preview_file=preview_file,
        preview_target=preview_target,
        visibility=visibility,
        pages_url=pages_url,
    )


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/code/commit")
def commit_project_code(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    visibility = (project.get("visibility") or "public").strip().lower()
    if visibility == "private":
        # still allowed (you are owner), but keep explicit
        pass

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    file_path = (request.form.get("file_path") or "").strip().replace("\\", "/").strip("/")
    new_content = request.form.get("content") or ""
    commit_message = (request.form.get("message") or "").strip()

    if not file_path or not _is_safe_project_path(code_path, file_path):
        flash("Invalid file path.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    if len(new_content.encode("utf-8")) > 800_000:
        flash("Content too large to commit via this UI.", "error")
        return redirect(
            url_for(
                "view_project_code",
                github_login=github_login,
                category=category_name,
                project_id=project_id,
                file=file_path,
            )
        )

    if not commit_message:
        short = file_path.split("/")[-1]
        commit_message = f"Update {short}"

    ok, _, err = _gh_upsert_file(token, github_login, repo_name, file_path, new_content, commit_message)
    if not ok:
        flash(err or "Commit failed.", "error")
    else:
        flash("Committed to GitHub.", "info")

    return redirect(
        url_for(
            "view_project_code",
            github_login=github_login,
            category=category_name,
            project_id=project_id,
            file=file_path,
        )
    )


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/code/add-file")
def add_project_file(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, category, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    rel = _normalize_rel_path(request.form.get("new_file") or "")
    if not rel:
        flash("File name is required.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    full_path = _join_project_path(code_path, rel)
    if not full_path or not _is_safe_project_path(code_path, full_path):
        flash("Invalid file path.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    content = request.form.get("new_content") or ""
    message = (request.form.get("message") or "").strip() or f"Add {rel.split('/')[-1]}"

    ok, _, err = _gh_upsert_file(token, github_login, repo_name, full_path, content, message)
    if not ok:
        flash(err or "Could not create file.", "error")
    else:
        flash("File created.", "info")

    return redirect(
        url_for(
            "view_project_code",
            github_login=github_login,
            category=category_name,
            project_id=project_id,
            file=full_path,
        )
    )


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/code/delete-file")
def delete_project_file(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, category, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    file_path = _normalize_rel_path(request.form.get("file_path") or "")
    if not file_path or not _is_safe_project_path(code_path, file_path):
        flash("Invalid file path.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    message = (request.form.get("message") or "").strip() or f"Delete {file_path.split('/')[-1]}"

    okm, meta, errm = _gh_get_content_meta(token, github_login, repo_name, file_path)
    if not okm or not meta:
        flash(errm or "Could not load file metadata.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    sha = meta.get("sha")
    if not sha:
        flash("Could not delete file (missing sha).", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    okd, errd = _gh_delete_file(token, github_login, repo_name, file_path, sha, message)
    if not okd:
        flash(errd or "Delete failed.", "error")
    else:
        flash("File deleted.", "info")

    return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/code/rename-file")
def rename_project_file(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, category, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    old_path = _normalize_rel_path(request.form.get("old_path") or "")
    new_value = _normalize_rel_path(request.form.get("new_path") or "")
    if not old_path or not _is_safe_project_path(code_path, old_path):
        flash("Invalid old file path.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    if not new_value:
        flash("New file name is required.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    if "/" in new_value:
        new_path = _join_project_path(code_path, new_value)
    else:
        old_dir = "/".join(old_path.split("/")[:-1])
        base_dir = old_dir if old_dir else code_path
        new_path = _join_project_path(base_dir, new_value)

    if not new_path or not _is_safe_project_path(code_path, new_path):
        flash("Invalid new file path.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    if new_path == old_path:
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    message = (request.form.get("message") or "").strip() or f"Rename {old_path.split('/')[-1]} to {new_path.split('/')[-1]}"

    okm, meta, errm = _gh_get_content_meta(token, github_login, repo_name, old_path)
    if not okm or not meta:
        flash(errm or "Could not load file metadata.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    sha = meta.get("sha")
    content_b64 = meta.get("content") or ""
    encoding = meta.get("encoding") or ""
    if not sha or encoding != "base64" or not content_b64:
        flash("Could not rename this file.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    try:
        raw = base64.b64decode(content_b64.encode("utf-8"), validate=False)
    except Exception:
        flash("Could not read file content for rename.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    if len(raw) > 1_500_000:
        flash("File too large to rename via this UI.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    okc, _, errc = _gh_upsert_bytes(token, github_login, repo_name, new_path, raw, message)
    if not okc:
        flash(errc or "Could not create new file while renaming.", "error")
        return redirect(
            url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=old_path)
        )

    okd, errd = _gh_delete_file(token, github_login, repo_name, old_path, sha, message)
    if not okd:
        flash(errd or "Renamed, but could not delete old file.", "error")
    else:
        flash("File renamed.", "info")

    return redirect(
        url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id, file=new_path)
    )


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/code/visibility")
def update_project_visibility(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    desired = (request.form.get("visibility") or "").strip().lower()
    if desired not in ("public", "private"):
        flash("Invalid visibility.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    move_repo = (request.form.get("move_repo") or "").strip() == "1"
    delete_source = (request.form.get("delete_source") or "").strip() == "1"

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path, pages_url FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    current = (project.get("visibility") or "public").strip().lower()
    project_name = (project.get("name") or "").strip() or "Project"
    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    src_repo = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None

    if desired == current:
        flash("Visibility already set.", "info")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    new_repo_url = code_repo_url
    new_pages_url = (project.get("pages_url") or "").strip()

    if desired == "private":
        new_pages_url = ""
        if move_repo and current == "public":
            if not src_repo or not code_path:
                flash("This project has no code repo configured.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            dst_repo = _repo_slug(f"{category_name}-{project_name}")
            if not dst_repo:
                flash("Could not create a repo name for this private project.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            ok, ensured_url, err = _gh_ensure_repo_with_visibility(
                token,
                github_login,
                dst_repo,
                description=f"{category_name}: {project_name}",
                private=True,
            )
            if not ok:
                flash(err or "Could not create/find the private repo. You may need `repo` scope.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            okc, errc = _gh_copy_prefix_between_repos(
                token,
                github_login,
                src_repo,
                code_path,
                dst_repo,
                code_path,
                f"Make {project_name} private",
            )
            if not okc:
                flash(errc or "Could not move code to the private repo.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            if delete_source:
                okd, errd = _gh_delete_path_recursive(token, github_login, src_repo, code_path)
                if not okd:
                    flash(errd or "Moved to private repo, but could not delete the old public copy.", "error")

            new_repo_url = ensured_url or f"https://github.com/{github_login}/{dst_repo}"
            flash("Moved code to a private repo.", "info")
        elif not move_repo and current == "public":
            flash("Set to private in this app (note: GitHub repo may still be public).", "info")

    if desired == "public":
        if move_repo and current == "private":
            if not src_repo or not code_path:
                flash("This project has no code repo configured.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            dst_repo = _repo_slug(category_name)
            if not dst_repo:
                flash("Category contains invalid characters.", "error")
                return redirect(url_for("portfolio_projects", github_login=github_login))

            ok, ensured_url, err = _gh_ensure_repo_with_visibility(
                token,
                github_login,
                dst_repo,
                description=f"{category_name} projects",
                private=False,
            )
            if not ok:
                flash(err or "Could not create/find the category repo.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            okc, errc = _gh_copy_prefix_between_repos(
                token,
                github_login,
                src_repo,
                code_path,
                dst_repo,
                code_path,
                f"Make {project_name} public",
            )
            if not okc:
                flash(errc or "Could not move code to the public repo.", "error")
                return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

            if delete_source:
                okd, errd = _gh_delete_path_recursive(token, github_login, src_repo, code_path)
                if not okd:
                    flash(errd or "Moved to public repo, but could not delete the old private copy.", "error")

            new_repo_url = ensured_url or f"https://github.com/{github_login}/{dst_repo}"
            new_pages_url = ""
            flash("Moved code to the category repo.", "info")

    with _db() as conn:
        conn.execute(
            "UPDATE projects SET visibility = ?, code_repo_url = ?, pages_url = ? WHERE id = ? AND github_login = ?",
            (desired, new_repo_url, new_pages_url, project_id, github_login),
        )
        conn.commit()

    flash("Visibility updated.", "info")
    return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))


@app.get("/portfolio/<github_login>/projects/<category>/<int:project_id>/preview")
def preview_project(github_login: str, category: str, project_id: int):
    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    data = _load_portfolio(github_login)
    if not data:
        flash("No saved portfolio found. Please fill the form first.", "info")
        return redirect(url_for("dashboard"))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path, pages_url FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    token = session.get("token")
    can_edit = session.get("github_login") == github_login and token is not None
    visibility = (project.get("visibility") or "public").strip().lower()
    if visibility == "private" and not can_edit:
        flash("Preview is private.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    selected = (request.args.get("file") or "").strip().replace("\\", "/").strip("/")
    if selected and not _is_safe_project_path(code_path, selected):
        selected = ""

    ok, files, err = _gh_list_files_in_prefix(token if can_edit else None, github_login, repo_name, code_path)
    if not ok or files is None:
        if can_edit:
            flash(err or "Could not list project files.", "error")
            return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))
        files = [selected] if selected else []

    html_files = [f for f in files if isinstance(f, str) and f.lower().endswith(".html")]
    if selected and selected not in html_files:
        if selected.lower().endswith(".html"):
            html_files = [selected]
        else:
            selected = ""

    if not selected:
        idx = f"{code_path}/index.html"
        if idx in html_files:
            selected = idx
        elif html_files:
            selected = html_files[0]

    pages_url = (project.get("pages_url") or "").strip() or None

    return render_template(
        "preview.html",
        name=data.get("name") or github_login,
        github_login=github_login,
        category=category_name,
        project_name=project.get("name") or "",
        project_id=project_id,
        repo_url=code_repo_url,
        repo_name=repo_name,
        code_path=code_path,
        html_files=html_files,
        selected_file=selected,
        can_edit=can_edit,
        pages_url=pages_url,
    )


@app.get("/portfolio/<github_login>/projects/<category>/<int:project_id>/preview/render")
def preview_project_render(github_login: str, category: str, project_id: int):
    category_name = (category or "").strip()
    if not category_name:
        return "Bad request", 400

    with _db() as conn:
        row = conn.execute(
            "SELECT id, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        return "Not found", 404

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        return "Not found", 404

    token = session.get("token")
    can_edit = session.get("github_login") == github_login and token is not None
    visibility = (project.get("visibility") or "public").strip().lower()
    if visibility == "private" and not can_edit:
        return "Forbidden", 403

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        return "Not found", 404

    selected = (request.args.get("file") or "").strip().replace("\\", "/").strip("/")
    if not selected or not _is_safe_project_path(code_path, selected) or not selected.lower().endswith(".html"):
        selected = f"{code_path}/index.html"

    ok, html, err = _gh_get_text_file(token if can_edit else None, github_login, repo_name, selected)
    if not ok or html is None:
        return err or "Could not load HTML", 400

    asset_base = url_for(
        "preview_project_asset",
        github_login=github_login,
        category=category_name,
        project_id=project_id,
    )
    if not asset_base.endswith("?"):
        asset_base = asset_base + "?"

    rewritten = _rewrite_preview_html(html, asset_base, selected, code_path)

    from flask import Response

    resp = Response(rewritten, mimetype="text/html; charset=utf-8")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'self' https: data:; style-src 'self' https: 'unsafe-inline'; script-src 'self' https: 'unsafe-inline' 'unsafe-eval'; img-src 'self' https: data:; font-src 'self' https: data:; connect-src 'self' https:; frame-ancestors 'self';"
    return resp


@app.get("/portfolio/<github_login>/projects/<category>/<int:project_id>/preview/asset")
def preview_project_asset(github_login: str, category: str, project_id: int):
    category_name = (category or "").strip()
    if not category_name:
        return "Bad request", 400

    with _db() as conn:
        row = conn.execute(
            "SELECT id, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        return "Not found", 404

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        return "Not found", 404

    token = session.get("token")
    can_edit = session.get("github_login") == github_login and token is not None
    visibility = (project.get("visibility") or "public").strip().lower()
    if visibility == "private" and not can_edit:
        return "Forbidden", 403

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        return "Not found", 404

    requested = (request.args.get("path") or "").strip().replace("\\", "/").strip("/")
    if not requested or not _is_safe_project_path(code_path, requested):
        return "Bad request", 400

    api_url = f"https://api.github.com/repos/{github_login}/{repo_name}/contents/{requested}"
    res = requests.get(api_url, headers=_gh_headers_optional(token if can_edit else None), timeout=15)
    if not res.ok:
        return "Not found", 404

    try:
        data = res.json() or {}
    except Exception:
        return "Invalid response", 502
    content_b64 = data.get("content") or ""
    if data.get("encoding") != "base64" or not content_b64:
        return "Unsupported", 415

    try:
        raw = base64.b64decode(content_b64.encode("utf-8"), validate=False)
    except Exception:
        return "Decode failed", 400

    from flask import Response

    lower = requested.lower()
    mimetype = "application/octet-stream"
    if lower.endswith(".css"):
        mimetype = "text/css; charset=utf-8"
    elif lower.endswith(".js"):
        mimetype = "text/javascript; charset=utf-8"
    elif lower.endswith(".html"):
        mimetype = "text/html; charset=utf-8"
    elif lower.endswith(".png"):
        mimetype = "image/png"
    elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
        mimetype = "image/jpeg"
    elif lower.endswith(".webp"):
        mimetype = "image/webp"
    elif lower.endswith(".gif"):
        mimetype = "image/gif"
    elif lower.endswith(".svg"):
        mimetype = "image/svg+xml"

    resp = Response(raw, mimetype=mimetype)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/deploy")
def deploy_project(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only deploy your own projects.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip().strip("/")
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None
    if not repo_name or not code_path:
        flash("This project has no code repo configured.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    ok, files, err = _gh_list_files_in_prefix(token, github_login, repo_name, code_path)
    if not ok or files is None:
        flash(err or "Could not list project files for deploy.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    if len(files) > 120:
        flash("Too many files to deploy from this UI (max 120).", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    # Reset docs/ so the deployed site matches this project.
    _gh_delete_path_recursive(token, github_login, repo_name, "docs")
    _gh_upsert_file(token, github_login, repo_name, "docs/.nojekyll", "", "Add .nojekyll")

    total_bytes = 0
    copied = 0
    for src in files:
        if not _is_safe_project_path(code_path, src):
            continue
        rel = src[len(code_path) + 1 :] if src.startswith(code_path + "/") else ""
        if not rel:
            continue
        dest = f"docs/{rel}"

        okb, blob, errb = _gh_get_file_bytes(token, github_login, repo_name, src)
        if not okb or blob is None:
            flash(errb or f"Could not read {src}.", "error")
            continue

        if len(blob) > 1_500_000:
            flash(f"Skipped large file: {rel} (>1.5MB).", "info")
            continue

        total_bytes += len(blob)
        if total_bytes > 8_000_000:
            flash("Deploy size limit reached (8MB).", "error")
            break

        okc, _, errc = _gh_upsert_bytes(token, github_login, repo_name, dest, blob, f"Deploy {rel}")
        if okc:
            copied += 1
        else:
            flash(errc or f"Could not write {dest}.", "error")

    okp, html_url, errp = _gh_enable_pages(token, github_login, repo_name, path="/docs")
    if not okp:
        flash(errp or "Deploy failed.", "error")
        return redirect(url_for("view_project_code", github_login=github_login, category=category_name, project_id=project_id))

    if html_url:
        with _db() as conn:
            conn.execute(
                "UPDATE projects SET pages_url = ? WHERE id = ? AND github_login = ?",
                (html_url, project_id, github_login),
            )

    flash(f"Deployed {copied} file(s).", "info")
    if html_url:
        flash(f"Live URL: {html_url}", "info")

    return redirect(url_for("preview_project", github_login=github_login, category=category_name, project_id=project_id))


@app.post("/portfolio/<github_login>/projects/<category>/<int:project_id>/delete")
def delete_project(github_login: str, category: str, project_id: int):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    category_name = (category or "").strip()
    if not category_name:
        return redirect(url_for("portfolio_projects", github_login=github_login))

    with _db() as conn:
        row = conn.execute(
            "SELECT id, name, category, visibility, code_repo_url, code_path FROM projects WHERE id = ? AND github_login = ?",
            (project_id, github_login),
        ).fetchone()

    if not row:
        flash("Project not found.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    project = dict(row)
    if (project.get("category") or "").strip() != category_name:
        flash("Project category mismatch.", "error")
        return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))

    delete_github = (request.form.get("delete_github") or "") == "1"
    code_repo_url = (project.get("code_repo_url") or "").strip()
    code_path = (project.get("code_path") or "").strip()
    repo_name = _parse_github_repo_name(code_repo_url, github_login) if code_repo_url else None

    if delete_github and repo_name and code_path:
        ok, err = _gh_delete_path_recursive(token, github_login, repo_name, code_path)
        if not ok:
            flash(err or "Could not delete project files from GitHub.", "error")
        else:
            flash("Deleted project files from GitHub.", "info")

    with _db() as conn:
        conn.execute("DELETE FROM projects WHERE id = ? AND github_login = ?", (project_id, github_login))

    # Keep category README in sync (public-only list).
    repo_name_cat = _repo_slug(category_name)
    if repo_name_cat:
        projects = _list_projects(github_login, category=category_name)
        public_projects = [p for p in projects if (p.get("visibility") or "public").strip().lower() != "private"]
        cat_readme = _render_category_readme(category_name, public_projects, owner=github_login, category_repo=repo_name_cat)
        _gh_upsert_file(token, github_login, repo_name_cat, "README.md", cat_readme, f"Remove project from {category_name}")

    flash("Project deleted.", "info")
    return redirect(url_for("portfolio_projects_category", github_login=github_login, category=category_name))


def _render_project_readme(category: str, name: str, description: str) -> str:
    cat = (category or "Projects").strip()
    title = (name or "Project").strip()
    desc = (description or "").strip()

    lines = [
        f"# {title}",
        "",
        f"Category: **{cat}**",
        "",
    ]
    if desc:
        lines += [desc, ""]
    lines += [
        "_This folder is auto-managed by the portfolio app._",
        "",
    ]
    return "\n".join(lines)


def _render_category_readme(category: str, projects: list[dict], owner: str, category_repo: str) -> str:
    title = (category or "Projects").strip()
    base_repo_url = f"https://github.com/{owner}/{category_repo}"
    lines = [
        f"# {title} Projects",
        "",
        "This repo is auto-managed by the portfolio app.",
        "",
        "## Projects",
        "",
    ]

    if not projects:
        lines.append("_No projects yet._")
        lines.append("")
        return "\n".join(lines)

    for p in projects:
        name = (p.get("name") or "").strip() or "Untitled"
        desc = (p.get("description") or "").strip()
        url = (p.get("repo_url") or "").strip()
        visibility = (p.get("visibility") or "public").strip().lower()

        code_path = (p.get("code_path") or "").strip()
        code_link = ""
        if visibility != "private" and code_path:
            code_link = f"{base_repo_url}/tree/main/{code_path.strip('/')}"

        if url:
            line = f"- [{name}]({url})"
        elif code_link:
            line = f"- [{name}]({code_link})"
        else:
            line = f"- {name}"
        if desc:
            line += f" — {desc}"
        if visibility == "private":
            line += " _(Private code)_"
        lines.append(line)

    lines.append("")
    return "\n".join(lines)


@app.route("/portfolio/<github_login>/notes")
def portfolio_notes(github_login: str):
    data = _load_portfolio(github_login)
    if not data:
        flash("No saved portfolio found. Please fill the form first.", "info")
        return redirect(url_for("dashboard"))

    notes = _list_notes(github_login)
    if not notes and data.get("notes"):
        notes = [
            {
                "content": data.get("notes"),
                "repo_name": "notes",
                "file_path": "",
                "commit_url": "",
                "created_at": data.get("updated_at"),
            }
        ]

    return render_template(
        "notes.html",
        name=data.get("name") or github_login,
        notes=notes,
        github_login=github_login,
    )


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))


@app.post("/portfolio/<github_login>/add-project")
def add_project(github_login: str):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    project_name = (request.form.get("project_name") or "").strip()
    project_desc = (request.form.get("project_desc") or "").strip()

    if not project_name:
        flash("Project name is required.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    repo_name = _repo_slug(project_name)
    if not repo_name:
        flash("Project name contains invalid characters.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    ok, repo_url, err = _gh_create_repo(token, repo_name, description=project_desc)
    if not ok:
        flash(err or "Could not create GitHub repo. Try a different project name.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    _add_project(github_login, project_name, project_desc, repo_url)
    flash(f"Project repo created: {repo_name}", "info")
    return redirect(url_for("portfolio", github_login=github_login))


@app.post("/portfolio/<github_login>/add-note")
def add_note(github_login: str):
    token, session_login = _require_login()
    if not token or not session_login:
        flash("Please login with GitHub first.", "error")
        return redirect(url_for("developer"))

    if github_login != session_login:
        flash("You can only edit your own portfolio.", "error")
        return redirect(url_for("dashboard"))

    content = (request.form.get("note") or "").strip()
    if not content:
        flash("Note is empty.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    repo_name = "notes"
    ok, _, err = _gh_ensure_repo(token, github_login, repo_name, description="My notes")
    if not ok:
        flash(err or "Could not create/find notes repo.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    ok, file_path, commit_url, err = _gh_create_note_file(token, github_login, repo_name, content)
    if not ok:
        flash(err or "Could not save note to GitHub.", "error")
        return redirect(url_for("portfolio", github_login=github_login))

    _add_note(github_login, content, repo_name, file_path or "", commit_url)
    flash("Note saved to GitHub.", "info")
    return redirect(url_for("portfolio", github_login=github_login))


# ▶️ RUN APP
if __name__ == "__main__":
    _init_db()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
