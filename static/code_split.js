(() => {
  const btn = document.querySelector("[data-code-split]");
  if (!btn) return;
  const hasSplitPreview = !!document.querySelector(".code-split-preview");
  if (!hasSplitPreview) return;

  const setState = (on) => {
    document.body.classList.toggle("code-split", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.title = on ? "Hide split preview" : "Split preview";
  };

  const isOn = () => document.body.classList.contains("code-split");

  setState(isOn());

  btn.addEventListener("click", () => setState(!isOn()));
})();
