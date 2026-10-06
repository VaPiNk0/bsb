// Téma stránky. Načítava sa v <head> bez defer, aby sa nastavila ešte pred prvým vykreslením.
// Kým si používateľ nevyberie sám, platí nastavenie systému.
(function () {
  const KEY = "theme";
  const root = document.documentElement;
  const systemLight = window.matchMedia("(prefers-color-scheme: light)");
  const system = () => (systemLight.matches ? "light" : "dark");

  function stored() {
    try {
      const value = localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;   // súkromný režim alebo zakázané úložisko
    }
  }

  function apply() {
    const theme = stored() || system();
    if (root.dataset.theme === theme) return;
    root.dataset.theme = theme;
    document.dispatchEvent(new CustomEvent("themechange", { detail: theme }));
  }

  apply();
  systemLight.addEventListener("change", apply);

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("themeToggle");
    if (!button) return;
    const label = () => {
      const dark = root.dataset.theme === "dark";
      button.title = dark ? t("Prepnúť na svetlú tému", "Switch to light theme") : t("Prepnúť na tmavú tému", "Switch to dark theme");
      button.setAttribute("aria-label", button.title);
    };
    label();
    document.addEventListener("themechange", label);
    button.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      try {
        // voľba zhodná so systémom sa neukladá, takže stránka ďalej sleduje systém
        if (next === system()) localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, next);
      } catch { /* bez úložiska voľba platí len do obnovenia stránky */ }
      root.dataset.theme = next;
      document.dispatchEvent(new CustomEvent("themechange", { detail: next }));
    });
  });
})();
