import os

import app as app_module


app_module._init_db()

app = app_module.app

# Optional: keep parity with Render's PORT when using `python wsgi.py` locally.
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

