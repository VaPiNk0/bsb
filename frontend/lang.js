// Jazyk rozhrania. Načítava sa v <head> bez defer, aby ho ostatné skripty mali hneď.
// Slovenský text je priamo v HTML; anglický je vedľa neho v atribúte data-en (text prvku)
// alebo data-en-<atribút>. V skriptoch sa píše t("po slovensky", "in English").
// Prepnutie jazyka stránku načíta znova, takže texty stačí nastaviť raz.
(function () {
  const KEY = "lang";
  const ATTRIBUTES = ["aria-label", "placeholder", "data-placeholder", "data-label"];

  function stored() {
    try {
      const value = localStorage.getItem(KEY);
      return value === "sk" || value === "en" ? value : null;
    } catch {
      return null;   // súkromný režim alebo zakázané úložisko
    }
  }

  // Východiskový jazyk je slovenčina bez ohľadu na prehliadač: veľa Slovákov má prehliadač po anglicky.
  const lang = stored() || "sk";
  window.LANG = lang;
  window.LOCALE = lang === "en" ? "en-GB" : "sk-SK";
  window.t = (sk, en) => (lang === "en" ? en : sk);
  document.documentElement.lang = lang;

  document.addEventListener("DOMContentLoaded", () => {
    if (lang === "en") {
      document.querySelectorAll("[data-en]").forEach((el) => { el.textContent = el.dataset.en; });
      for (const name of ATTRIBUTES) {
        document.querySelectorAll(`[data-en-${name}]`).forEach((el) => {
          el.setAttribute(name, el.getAttribute(`data-en-${name}`));
        });
      }
    }
    const button = document.getElementById("langToggle");
    if (!button) return;
    const next = lang === "en" ? "sk" : "en";
    button.textContent = next.toUpperCase();
    button.title = next === "en" ? "Switch to English" : "Prepnúť do slovenčiny";
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", () => {
      try { localStorage.setItem(KEY, next); } catch { return; }   // bez úložiska by sa voľba po načítaní stratila
      location.reload();
    });
  });
})();
