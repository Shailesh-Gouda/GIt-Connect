(() => {
  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.left = "-9999px";
        document.body.appendChild(area);
        area.focus();
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
        return true;
      } catch {
        return false;
      }
    }
  };

  const flashCopied = (btn) => {
    const old = btn.textContent;
    btn.textContent = "Copied";
    btn.setAttribute("data-copied", "1");
    setTimeout(() => {
      btn.textContent = old;
      btn.removeAttribute("data-copied");
    }, 1200);
  };

  const codeBtn = document.querySelector("[data-copy-code]");
  const pre = document.querySelector("[data-code-pre]");
  if (codeBtn && pre) {
    codeBtn.addEventListener("click", async () => {
      const text = pre.innerText || "";
      if (!text.trim()) return;
      const ok = await copyText(text);
      if (ok) flashCopied(codeBtn);
    });
  }

  for (const btn of document.querySelectorAll("[data-copy-text]")) {
    btn.addEventListener("click", async () => {
      const explicit = btn.getAttribute("data-copy-value");
      let text = (explicit || "").trim();
      if (!text) {
        const targetSel = btn.getAttribute("data-copy-target");
        const target = targetSel ? document.querySelector(targetSel) : null;
        text = (target?.textContent || "").trim();
      }
      if (!text) return;
      const ok = await copyText(text);
      if (ok) flashCopied(btn);
    });
  }
})();
