(() => {
  const root = document.documentElement;
  if (!root) {
    return;
  }
  let theme = "";
  try {
    theme = window.localStorage.getItem("policynim-beta-theme") || "";
  } catch (error) {
    theme = "";
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  root.dataset.theme = theme;
})();
