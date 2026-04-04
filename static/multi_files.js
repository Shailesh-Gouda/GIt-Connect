(() => {
  const makeInput = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.name = "project_files";
    input.multiple = true;
    input.className = "file-input";
    return input;
  };

  const roots = document.querySelectorAll("[data-file-inputs]");
  for (const root of roots) {
    const btn = root.querySelector("[data-add-file-input]");
    if (!btn) continue;

    btn.addEventListener("click", () => {
      root.insertBefore(makeInput(), btn);
    });
  }
})();

