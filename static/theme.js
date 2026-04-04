(() => {
  const STORAGE_KEY = "pp_theme";
  const root = document.documentElement;

  function getSavedTheme() {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : null;
  }

  function applyTheme(theme) {
    root.dataset.theme = theme;
    const toggle = document.querySelector("[data-theme-toggle]");
    if (toggle) toggle.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
  }

  function toggleTheme() {
    const current = root.dataset.theme === "light" ? "light" : "dark";
    const next = current === "dark" ? "light" : "dark";
    window.localStorage.setItem(STORAGE_KEY, next);
    applyTheme(next);
  }

  const initial = getSavedTheme() || "dark";
  applyTheme(initial);

  function closeAllMenus() {
    document.querySelectorAll("[data-menu][data-open='true']").forEach((menu) => {
      menu.dataset.open = "false";
      const toggle = menu.querySelector("[data-menu-toggle]");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    });
  }

  function toggleMenu(menu) {
    const isOpen = menu.dataset.open === "true";
    closeAllMenus();
    if (isOpen) return;
    menu.dataset.open = "true";
    const toggle = menu.querySelector("[data-menu-toggle]");
    if (toggle) toggle.setAttribute("aria-expanded", "true");
  }

  window.addEventListener("click", (e) => {
    const btn = e.target instanceof Element ? e.target.closest("[data-theme-toggle]") : null;
    if (btn) {
      e.preventDefault();
      toggleTheme();
      return;
    }

    const menuToggle = e.target instanceof Element ? e.target.closest("[data-menu-toggle]") : null;
    if (menuToggle) {
      e.preventDefault();
      const menu = menuToggle.closest("[data-menu]");
      if (menu) toggleMenu(menu);
      return;
    }

    const clickedInsideMenu = e.target instanceof Element ? e.target.closest("[data-menu]") : null;
    if (!clickedInsideMenu) closeAllMenus();
  });

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllMenus();
  });
})();
