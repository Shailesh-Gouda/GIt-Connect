(() => {
  const enc = encodeURIComponent;

  const buildLinks = ({ url, title }) => {
    const text = (title ? `${title} — ` : "") + url;
    return {
      whatsapp: `https://wa.me/?text=${enc(text)}`,
      telegram: `https://t.me/share/url?url=${enc(url)}&text=${enc(title || "")}`,
      twitter: `https://twitter.com/intent/tweet?url=${enc(url)}&text=${enc(title || "")}`,
      linkedin: `https://www.linkedin.com/sharing/share-offsite/?url=${enc(url)}`,
      email: `mailto:?subject=${enc(title || "Project")}&body=${enc(text)}`,
    };
  };

  const closeAll = (except) => {
    document.querySelectorAll("details.share-actions[open]").forEach((d) => {
      if (except && d === except) return;
      d.removeAttribute("open");
    });
  };

  document.addEventListener("click", (e) => {
    const details = e.target instanceof Element ? e.target.closest("details.share-actions") : null;
    if (!details) closeAll();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAll();
  });

  for (const details of document.querySelectorAll("details.share-actions[data-share]")) {
    const url = (details.getAttribute("data-share-url") || "").trim();
    const title = (details.getAttribute("data-share-title") || "").trim();
    if (!url) continue;

    details.addEventListener("toggle", () => {
      if (details.open) closeAll(details);
    });

    const links = buildLinks({ url, title });
    for (const a of details.querySelectorAll("[data-share-kind]")) {
      const kind = (a.getAttribute("data-share-kind") || "").trim();
      const href = links[kind];
      if (href) a.setAttribute("href", href);
    }

    const nativeBtn = details.querySelector("[data-native-share]");
    if (nativeBtn) {
      if (!navigator.share) {
        nativeBtn.style.display = "none";
      } else {
        nativeBtn.addEventListener("click", async () => {
          try {
            await navigator.share({ title: title || "Project", text: title || "", url });
            details.removeAttribute("open");
          } catch {
            // user canceled; ignore
          }
        });
      }
    }
  }
})();

