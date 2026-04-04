(() => {
  const input = document.querySelector("[data-topbar-search]");
  if (!input) return;

  const normalize = (value) => (value || "").toLowerCase().trim();

  const getItems = () => Array.from(document.querySelectorAll(".card .item"));

  const updateEmptyState = (container, visibleCount, query) => {
    if (!container) return;
    const existing = container.querySelector("[data-search-empty]");
    if (!query) {
      if (existing) existing.remove();
      return;
    }
    if (visibleCount > 0) {
      if (existing) existing.remove();
      return;
    }
    if (!existing) {
      const p = document.createElement("p");
      p.className = "muted";
      p.setAttribute("data-search-empty", "1");
      p.textContent = "No results found.";
      container.appendChild(p);
    }
  };

  const filterItems = (query) => {
    const items = getItems();
    if (items.length === 0) return false;

    const q = normalize(query);
    let visibleCount = 0;
    for (const item of items) {
      const hay = normalize(item.textContent);
      const match = !q || hay.includes(q);
      item.style.display = match ? "" : "none";
      if (match) visibleCount += 1;
    }

    const container = document.querySelector(".card");
    updateEmptyState(container, visibleCount, q);
    return true;
  };

  const findAndScroll = (query) => {
    const q = normalize(query);
    if (!q) return;

    const candidates = Array.from(
      document.querySelectorAll("main .hero-inner, main .hero-inner *")
    );
    const match = candidates.find((el) => {
      if (!(el instanceof HTMLElement)) return false;
      if (el.closest("header")) return false;
      if (el.children.length > 0) return false;
      const text = normalize(el.textContent);
      return text && text.includes(q);
    });

    if (match) {
      match.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  input.addEventListener("input", (e) => {
    const value = e.target.value;
    const handled = filterItems(value);
    if (!handled) findAndScroll(value);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      input.value = "";
      filterItems("");
      input.blur();
      return;
    }
    if (e.key === "Enter") {
      const handled = filterItems(input.value);
      if (!handled) findAndScroll(input.value);
    }
  });

  document.addEventListener("keydown", (e) => {
    const key = (e.key || "").toLowerCase();
    if ((e.ctrlKey || e.metaKey) && key === "k") {
      e.preventDefault();
      input.focus();
      input.select();
    }
    if (key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const active = document.activeElement;
      const typing =
        active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
      if (!typing) {
        e.preventDefault();
        input.focus();
        input.select();
      }
    }
  });

  const params = new URLSearchParams(window.location.search);
  const initial = params.get("q");
  if (initial) {
    input.value = initial;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
})();
