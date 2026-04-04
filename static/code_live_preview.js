(() => {
  const body = document.body;
  if (!body || !body.classList.contains("page-code")) return;

  const textarea = document.querySelector(".code-edit textarea[name='content']");
  const frame = document.querySelector("[data-preview-frame]");
  if (!textarea || !frame) return;

  const codePath = (body.dataset.codePath || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  const selectedFile = (body.dataset.selectedFile || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
  const assetBase = (body.dataset.previewAsset || "").trim();

  const isHtml = (p) => (p || "").toLowerCase().endsWith(".html");
  if (!isHtml(selectedFile) || !codePath) return;

  const norm = (p) =>
    (p || "")
      .replace(/\\/g, "/")
      .trim()
      .replace(/^\/+|\/+$/g, "")
      .replace(/\/{2,}/g, "/");

  const isSafe = (p) => {
    const path = norm(p);
    if (!path) return false;
    const parts = path.split("/");
    if (parts.some((x) => x === "" || x === "." || x === "..")) return false;
    return path === codePath || path.startsWith(codePath + "/");
  };

  const join = (baseDir, rel) => {
    const base = norm(baseDir);
    const r = (rel || "").replace(/\\/g, "/").trim();
    if (!r) return "";
    if (r.startsWith("/") || r.startsWith("\\")) return "";
    if (/^(https?:)?\/\//i.test(r)) return "";
    if (/^(data:|mailto:|tel:|javascript:|#)/i.test(r)) return "";

    const out = base ? base.split("/").filter(Boolean) : [];
    for (const part of r.split("/")) {
      if (!part || part === ".") continue;
      if (part === "..") {
        if (out.length) out.pop();
        continue;
      }
      out.push(part);
    }
    return out.join("/");
  };

  const assetPrefix = (() => {
    if (!assetBase) return "";
    return assetBase.includes("?") ? assetBase + "&path=" : assetBase + "?path=";
  })();

  const rewrite = (html) => {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html || "", "text/html");
      const baseDir = selectedFile.split("/").slice(0, -1).join("/");

      const rewriteUrl = (url) => {
        const val = (url || "").trim();
        if (!val) return val;
        if (/^(https?:)?\/\//i.test(val)) return val;
        if (/^(data:|mailto:|tel:|javascript:|#)/i.test(val)) return val;
        if (val.startsWith("/") || val.startsWith("\\")) return val;

        const resolved = join(baseDir, val);
        if (!resolved || !assetPrefix || !isSafe(resolved)) return val;
        return assetPrefix + encodeURIComponent(resolved);
      };

      doc.querySelectorAll("[src]").forEach((el) => {
        const v = el.getAttribute("src");
        const next = rewriteUrl(v);
        if (next && next !== v) el.setAttribute("src", next);
      });

      doc.querySelectorAll("link[href]").forEach((el) => {
        const v = el.getAttribute("href");
        const next = rewriteUrl(v);
        if (next && next !== v) el.setAttribute("href", next);
      });

      return "<!doctype html>\n" + doc.documentElement.outerHTML;
    } catch (e) {
      return html || "";
    }
  };

  let timer = null;
  const update = () => {
    if (!body.classList.contains("code-fullscreen") || !body.classList.contains("code-split")) return;
    frame.srcdoc = rewrite(textarea.value);
  };

  textarea.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(update, 250);
  });
})();

