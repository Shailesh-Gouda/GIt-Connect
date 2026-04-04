(() => {
  const btn = document.querySelector("[data-code-fullscreen]");
  const main = document.querySelector("[data-code-main]");
  if (!btn || !main) return;
  const splitBtn = document.querySelector("[data-code-split]");
  const hasSplitPreview = !!document.querySelector(".code-split-preview");

  const setState = (on) => {
    document.body.classList.toggle("code-fullscreen", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "Exit full screen" : "Full screen";
    btn.textContent = on ? "⤡" : "⤢";
    if (!on) {
      document.body.classList.remove("code-split");
      if (splitBtn) {
        splitBtn.setAttribute("aria-pressed", "false");
        splitBtn.title = "Split preview";
      }
    } else if (splitBtn && hasSplitPreview) {
      document.body.classList.add("code-split");
      splitBtn.setAttribute("aria-pressed", "true");
      splitBtn.title = "Hide split preview";
    }
  };

  const isOn = () => document.body.classList.contains("code-fullscreen");

  btn.addEventListener("click", () => setState(!isOn()));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOn()) setState(false);
    if ((e.ctrlKey || e.metaKey) && (e.key || "").toLowerCase() === "f") {
      if (document.body.classList.contains("page-code")) {
        e.preventDefault();
        setState(!isOn());
      }
    }
  });
})();
