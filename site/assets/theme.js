// Light and dark theme toggle, like the one on runtimeconditions.io.
// The page follows the system until the reader picks a theme; the choice is remembered.
(() => {
  const KEY = "rc-theme";
  const root = document.documentElement;
  const read = () => {
    try {
      const value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  };
  const write = (value) => {
    try {
      localStorage.setItem(KEY, value);
    } catch {
      // The choice only lasts for this page when storage is blocked.
    }
  };
  const system = window.matchMedia("(prefers-color-scheme: light)");
  const current = () => root.dataset.theme || (system.matches ? "light" : "dark");

  // The slider sets how dark the dark theme is and how soft the light theme is.
  const LEVEL_KEY = { dark: "rc-dark-level", light: "rc-light-level" };
  const LEVEL_DEFAULT = 40;
  const readLevel = (theme) => {
    try {
      const raw = localStorage.getItem(LEVEL_KEY[theme]);
      const number = Number(raw);
      return raw !== null && number >= 0 && number <= 100 ? number : null;
    } catch {
      return null;
    }
  };
  for (const theme of ["dark", "light"]) {
    const level = readLevel(theme);
    if (level !== null) root.style.setProperty(`--${theme}-level`, String(level));
  }

  // Set before the page paints, so a reader who chose a theme never sees the other one flash.
  const saved = read();
  if (saved) root.dataset.theme = saved;

  document.addEventListener("DOMContentLoaded", () => {
    const nav = document.querySelector(".icon-nav");
    if (!nav) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-toggle";
    button.innerHTML =
      '<svg class="icon-moon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>' +
      '<svg class="icon-sun" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>' +
      '<span class="theme-toggle-label"></span>';
    const label = button.querySelector(".theme-toggle-label");
    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "100";
    slider.className = "theme-level";
    const syncSlider = () => {
      const theme = current();
      slider.value = String(readLevel(theme) ?? LEVEL_DEFAULT);
      const text = theme === "dark" ? "Dark theme brightness" : "Light theme softness";
      slider.title = text;
      slider.setAttribute("aria-label", text);
    };
    slider.addEventListener("input", () => {
      const theme = current();
      root.style.setProperty(`--${theme}-level`, slider.value);
      try {
        localStorage.setItem(LEVEL_KEY[theme], slider.value);
      } catch {
        // The setting only lasts for this page when storage is blocked.
      }
    });
    const update = () => {
      const dark = current() === "dark";
      const text = dark ? "Switch to the light theme" : "Switch to the dark theme";
      button.setAttribute("aria-label", text);
      button.title = text;
      label.textContent = dark ? "Light" : "Dark";
      syncSlider();
    };
    button.addEventListener("click", () => {
      const next = current() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      write(next);
      update();
    });
    system.addEventListener("change", update);
    nav.prepend(slider, button);
    update();
  });
})();
