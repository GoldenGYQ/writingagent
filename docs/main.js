(() => {
  const root = document.documentElement;
  const toggle = document.querySelector("[data-theme-toggle]");
  const savedTheme = window.localStorage.getItem("gyq-agent-theme");

  if (savedTheme === "light" || savedTheme === "dark") {
    root.dataset.theme = savedTheme;
  }

  const syncToggle = () => {
    const isLight = root.dataset.theme === "light";
    toggle?.setAttribute("aria-pressed", String(isLight));
    if (toggle) toggle.querySelector(".theme-label").textContent = isLight ? "Light" : "Dark";
  };

  toggle?.addEventListener("click", () => {
    const nextTheme = root.dataset.theme === "light" ? "dark" : "light";
    root.dataset.theme = nextTheme;
    window.localStorage.setItem("gyq-agent-theme", nextTheme);
    syncToggle();
  });

  syncToggle();
})();
