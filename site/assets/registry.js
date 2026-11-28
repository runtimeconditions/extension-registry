// Search, package tabs, copy buttons and the version chooser.
// Every page works without this script; it only adds these conveniences.
(() => {
  const LANGUAGE_KEY = "registry-language";

  const escapeHtml = (value) =>
    value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const storage = {
    get(key) {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, value);
      } catch {
        // Storage can be blocked; the choice just is not remembered.
      }
    },
  };

  // Package tabs: one language at a time, remembered across pages.
  const selectTab = (container, tab, remember) => {
    for (const other of container.querySelectorAll('[role="tab"]')) {
      const selected = other === tab;
      other.setAttribute("aria-selected", String(selected));
      other.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(other.getAttribute("aria-controls"));
      if (panel) panel.hidden = !selected;
    }
    if (remember) storage.set(LANGUAGE_KEY, tab.id.replace(/^tab-/, ""));
  };

  const setUpTabs = (root) => {
    for (const container of root.querySelectorAll("[data-tabs]")) {
      const tabs = [...container.querySelectorAll('[role="tab"]')];
      if (!tabs.length) continue;
      container.classList.add("tabs-ready");
      const saved = tabs.find((tab) => tab.id === `tab-${storage.get(LANGUAGE_KEY)}`);
      selectTab(container, saved || tabs[0], false);
      tabs.forEach((tab, index) => {
        tab.addEventListener("click", () => selectTab(container, tab, true));
        tab.addEventListener("keydown", (event) => {
          const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
          if (!step) return;
          event.preventDefault();
          const next = tabs[(index + step + tabs.length) % tabs.length];
          selectTab(container, next, true);
          next.focus();
        });
      });
    }
  };

  // Copy buttons on code blocks.
  const copyText = async (text, button) => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(button.parentElement.querySelector("code") || button.parentElement);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Press Ctrl+C";
    }
    setTimeout(() => {
      button.textContent = "Copy";
    }, 1600);
  };

  const setUpCopyButtons = (root) => {
    for (const pre of root.querySelectorAll(".md pre")) {
      if (pre.querySelector(".copy")) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "copy";
      button.textContent = "Copy";
      button.addEventListener("click", () => copyText((pre.querySelector("code") || pre).innerText, button));
      pre.append(button);
    }
  };

  const setUpVersionSelect = (root) => {
    for (const select of root.querySelectorAll("[data-version-select]")) {
      select.addEventListener("change", () => {
        window.location.href = select.value;
      });
    }
  };

  const setUpContent = (root) => {
    setUpTabs(root);
    setUpCopyButtons(root);
    setUpVersionSelect(root);
  };

  // Search: more than one match shows cards, exactly one shows its details.
  const setUpSearch = () => {
    const input = document.getElementById("search");
    const main = document.getElementById("main");
    if (!input || !main) return;
    const toc = document.querySelector(".toc");
    const original = { html: main.innerHTML, className: main.className };
    const details = new Map();
    let index = null;
    let loading = null;
    let latest = 0;
    let results = [];
    let timer = 0;

    const loadIndex = () => {
      loading ||= fetch("/search-index.json")
        .then((response) => {
          if (!response.ok) throw new Error(`search index: HTTP ${response.status}`);
          return response.json();
        })
        .then((data) => {
          index = data;
        });
      return loading;
    };

    const rank = (entry, query) => {
      if (entry.slug === query || entry.name === query) return 0;
      if (entry.slug.startsWith(query) || entry.name.startsWith(query)) return 1;
      return 2;
    };

    const find = (query) => {
      const words = query.toLowerCase().split(/\s+/).filter(Boolean);
      const lower = query.toLowerCase();
      return index
        .filter((entry) => words.every((word) => entry.terms.includes(word)))
        .sort((a, b) => rank(a, lower) - rank(b, lower) || a.slug.localeCompare(b.slug));
    };

    const loadDetail = (url) => {
      if (!details.has(url)) {
        details.set(
          url,
          fetch(url)
            .then((response) => {
              if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
              return response.text();
            })
            .then((text) => new DOMParser().parseFromString(text, "text/html").getElementById("main").innerHTML),
        );
      }
      return details.get(url);
    };

    const show = (html, className) => {
      main.innerHTML = html;
      main.className = `content ${className}`;
      if (toc) toc.hidden = true;
      setUpContent(main);
    };

    const restore = () => {
      results = [];
      main.innerHTML = original.html;
      main.className = original.className;
      if (toc) toc.hidden = false;
      setUpContent(main);
    };

    const run = async () => {
      const query = input.value.trim();
      const request = ++latest;
      if (!query) {
        restore();
        return;
      }
      try {
        await loadIndex();
      } catch {
        show('<h1>Search is not available</h1><p class="lead">The search index could not be loaded. Open <a href="/extensions/">Available extensions</a> instead.</p>', "list");
        return;
      }
      if (request !== latest) return;
      results = find(query);
      const quoted = `“${escapeHtml(query)}”`;
      if (!results.length) {
        show(
          `<h1>No results</h1><p class="lead">No extension matches ${quoted}. Try a name, a kind such as <code>aws.sqs</code>, or a field, or open <a href="/extensions/">Available extensions</a>.</p>`,
          "list",
        );
      } else if (results.length === 1) {
        const [only] = results;
        let html;
        try {
          html = await loadDetail(only.url);
        } catch {
          html = `<div class="cards">${only.card}</div>`;
        }
        if (request !== latest) return;
        show(
          `<p class="search-note">The only match for ${quoted}. <a href="${only.url}">Open its page</a></p>${html}`,
          "detail",
        );
      } else {
        show(
          `<h1>${results.length} results for ${quoted}</h1><div class="cards">${results.map((entry) => entry.card).join("")}</div>`,
          "list",
        );
      }
    };

    input.addEventListener("focus", () => loadIndex().catch(() => {}), { once: true });
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 120);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && results.length) {
        event.preventDefault();
        window.location.href = results[0].url;
      } else if (event.key === "Escape") {
        input.value = "";
        restore();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      if (target.closest && target.closest("input, textarea, select, [contenteditable]")) return;
      event.preventDefault();
      input.focus();
    });

    const query = new URLSearchParams(window.location.search).get("q");
    if (query) {
      input.value = query;
      run();
    }
  };

  setUpContent(document);
  setUpSearch();
})();
