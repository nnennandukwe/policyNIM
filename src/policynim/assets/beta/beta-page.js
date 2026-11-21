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
  for (const button of document.querySelectorAll("[data-copy-target]")) {
    const source = document.getElementById(button.dataset.copyTarget);
    const status = button.parentElement.querySelector("[role=status]");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(source.textContent);
        status.textContent = "Copied.";
      } catch {
        status.textContent = "Copy failed. Select the text above and copy it manually.";
      }
    });
    button.hidden = false;
  }
  body.dataset.js = "ready";

  const wizard = document.querySelector(".wizard");
  if (!wizard) return;
  const account = document.getElementById("account-view");
  const key = document.getElementById("key");
  const connect = document.getElementById("connect-view");

  // Recheck the session when history restores a signed-in document.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) window.location.replace(wizard.dataset.portalPath);
  });

  if (key) {
    account.hidden = true;
    const saved = document.getElementById("key-saved");
    const next = document.getElementById("key-next");
    const syncNext = () => {
      next.disabled = !saved.checked;
    };
    saved.addEventListener("change", syncNext);
    syncNext();
    window.addEventListener("pagehide", () => {
      document.getElementById("key-export").textContent = "";
    });
    return;
  }

  const tabs = Array.from(connect.querySelectorAll("[role=tab]"));
  const showClient = (selected) => {
    for (const tab of tabs) {
      const active = tab === selected;
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      panel.hidden = !active;
    }
  };
  for (const [index, tab] of tabs.entries()) {
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.tabIndex = 0;
    panel.querySelector("[data-client-title]").hidden = true;
    tab.addEventListener("click", () => showClient(tab));
    tab.addEventListener("keydown", (event) => {
      const destinations = {
        ArrowLeft: (index - 1 + tabs.length) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      };
      const destination = destinations[event.key];
      if (typeof destination !== "number") return;
      event.preventDefault();
      const selected = tabs[destination];
      showClient(selected);
      selected.focus();
    });
  }
  showClient(tabs[0]);
  connect.querySelector("[role=tablist]").hidden = false;

  const keyStep = document.getElementById("key-step");
  const connectStep = document.getElementById("connect-step");
  const showView = (focusHeading) => {
    const showConnect = window.location.hash === "#connect";
    account.hidden = showConnect;
    connect.hidden = !showConnect;
    const current = showConnect ? connectStep : keyStep;
    const other = showConnect ? keyStep : connectStep;
    current.setAttribute("aria-current", "step");
    other.removeAttribute("aria-current");
    keyStep.classList.toggle("is-complete", showConnect && keyStep.hasAttribute("data-has-key"));
    wizard.querySelector('[data-rail-icon="key"]').hidden = showConnect;
    wizard.querySelector('[data-rail-icon="connect"]').hidden = !showConnect;
    document.getElementById("window-status").textContent = showConnect
      ? "Run the command to finish setup"
      : "GitHub signed in";
    if (focusHeading) (showConnect ? connect : account).querySelector("h1").focus();
  };
  showView(["#account", "#connect"].includes(window.location.hash));
  window.addEventListener("hashchange", () => {
    if (["", "#account", "#connect"].includes(window.location.hash)) showView(true);
  });
})();
