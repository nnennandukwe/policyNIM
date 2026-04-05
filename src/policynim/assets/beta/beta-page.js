(() => {
  const root = document.documentElement;
  const body = document.body;
  if (!root || !body) {
    return;
  }
  const toggleButton = document.querySelector("[data-theme-toggle]");
  const toggleLabel = toggleButton?.querySelector("[data-theme-label]");
  const applyTheme = (theme) => {
    root.dataset.theme = theme;
    if (toggleButton) {
      toggleButton.setAttribute("aria-pressed", String(theme === "dark"));
      toggleButton.setAttribute(
        "title",
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
      );
    }
    if (toggleLabel) {
      toggleLabel.textContent = theme === "dark" ? "Theme: Dark" : "Theme: Light";
    }
  };
  applyTheme(root.dataset.theme === "dark" ? "dark" : "light");
  toggleButton?.addEventListener("click", () => {
    const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    try {
      window.localStorage.setItem("policynim-beta-theme", nextTheme);
    } catch (error) {
      // Ignore storage failures and keep the in-memory theme state.
    }
  });
  body.dataset.js = "ready";
  for (const button of document.querySelectorAll("[data-copy]")) {
    let resetLabelTimer = 0;
    button.addEventListener("click", async () => {
      const originalLabel = button.textContent || "Copy command";
      const text = button.getAttribute("data-copy") || "";
      window.clearTimeout(resetLabelTimer);
      try {
        if (!navigator.clipboard || !window.isSecureContext) {
          throw new Error("Clipboard access unavailable.");
        }
        await navigator.clipboard.writeText(text);
        button.dataset.copyState = "copied";
        button.textContent = "Copied";
      } catch (error) {
        delete button.dataset.copyState;
        window.prompt("Copy this command:", text);
        button.textContent = "Copy again";
      } finally {
        resetLabelTimer = window.setTimeout(() => {
          delete button.dataset.copyState;
          button.textContent = originalLabel;
        }, 1400);
      }
    });
  }
})();
