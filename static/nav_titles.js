(() => {
  const uniq = (arr) => Array.from(new Set(arr));
  const normalize = (v) => (v || "").trim();

  const loadCustom = (storageKey) => {
    try {
      const raw = localStorage.getItem(storageKey);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((x) => typeof x === "string") : [];
    } catch {
      return [];
    }
  };

  const saveCustom = (storageKey, titles) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(titles));
    } catch {
      // ignore
    }
  };

  const render = (root) => {
    const itemsEl = root.querySelector("[data-nav-titles-items], [data-project-titles-items]");
    const addBtn = root.querySelector("[data-nav-titles-add], [data-project-titles-add]");
    if (!itemsEl || !addBtn) return;

    const storageKey =
      root.getAttribute("data-storage-key") ||
      (root.hasAttribute("data-notes-titles") ? "gitconnect_note_titles" : "gitconnect_project_titles");
    const baseUrl = root.getAttribute("data-base-url") || root.getAttribute("data-projects-url") || "#";
    const linkTemplate = root.getAttribute("data-link-template") || "";
    const defaults = (root.getAttribute("data-default-titles") || "")
      .split(",")
      .map(normalize)
      .filter(Boolean);
    const custom = loadCustom(storageKey).map(normalize).filter(Boolean);
    const titles = uniq([...defaults, ...custom]).slice(0, 30);

    itemsEl.innerHTML = "";
    for (const title of titles) {
      const a = document.createElement("a");
      a.className = "topbar-dd-item";
      a.setAttribute("role", "menuitem");
      if (linkTemplate && linkTemplate.includes("__TITLE__")) {
        a.href = linkTemplate.replace("__TITLE__", encodeURIComponent(title));
      } else {
        a.href = `${baseUrl}?q=${encodeURIComponent(title)}`;
      }
      a.textContent = title;
      itemsEl.appendChild(a);
    }

    addBtn.onclick = () => {
      const next = normalize(window.prompt("Add a title (e.g., React, Java, Django):", ""));
      if (!next) return;
      const safe = next.slice(0, 28);
      const updated = uniq([...custom, safe]).slice(0, 30);
      saveCustom(storageKey, updated);
      render(root);
    };
  };

  const roots = document.querySelectorAll("[data-nav-titles], [data-project-titles], [data-notes-titles]");
  for (const root of roots) render(root);
})();
