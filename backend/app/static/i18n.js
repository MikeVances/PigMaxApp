// i18n.js — Translation engine for MINMAXapp
// Language data is loaded from i18n/<lang>.js files BEFORE this script.
// Usage in HTML:  <tag data-i18n="key">fallback</tag>
// Usage in JS:    t('key') or t('key', { var: value })

(function () {

  // Languages loaded by <script> tags; each sets window.LOCALES.<lang>
  window.LOCALES = window.LOCALES || {};

  // Restore last choice, default to EN
  let currentLang = localStorage.getItem('lang') || 'en';
  if (!window.LOCALES[currentLang]) currentLang = 'en';

  // ── t(): synchronous lookup with EN fallback ──────────────────────────────
  window.t = function (key, vars) {
    const locale   = window.LOCALES[currentLang] || {};
    const fallback = window.LOCALES['en']         || {};
    let str = locale[key] !== undefined ? locale[key] : fallback[key];
    if (str === undefined) str = key; // last resort: echo the key
    if (vars && typeof str === 'string') {
      str = str.replace(/\{(\w+)\}/g, (_, k) => vars[k] !== undefined ? vars[k] : '{' + k + '}');
    }
    return str;
  };

  // ── applyDOM(): translate all data-i18n* attributes in the document ───────
  function applyDOM() {
    const locale   = window.LOCALES[currentLang] || {};
    const fallback = window.LOCALES['en']         || {};

    function val(key) {
      return locale[key] !== undefined ? locale[key] : fallback[key];
    }

    document.querySelectorAll('[data-i18n]').forEach(el => {
      const v = val(el.dataset.i18n);
      if (v !== undefined) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-html]').forEach(el => {
      const v = val(el.dataset.i18nHtml);
      if (v !== undefined) el.innerHTML = v;
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const v = val(el.dataset.i18nPlaceholder);
      if (v !== undefined) el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      const v = val(el.dataset.i18nTitle);
      if (v !== undefined) el.title = v;
    });

    document.documentElement.lang = currentLang;

    const tk = document.body && document.body.dataset.pageTitle;
    if (tk) { const v = val(tk); if (v) document.title = v; }
  }

  // ── setLang(): switch language in-place, no page reload ───────────────────
  function setLang(lang) {
    if (!window.LOCALES[lang]) return; // file not loaded
    currentLang = lang;
    localStorage.setItem('lang', lang);
    applyDOM();
    updateSwitcherUI();
  }

  // ── Language switcher UI ──────────────────────────────────────────────────
  const LANG_META = [
    { code: 'en', flag: '🇬🇧', label: 'EN' },
    { code: 'ru', flag: '🇷🇺', label: 'RU' },
    { code: 'pl', flag: '🇵🇱', label: 'PL' },
    { code: 'de', flag: '🇩🇪', label: 'DE' },
    { code: 'fr', flag: '🇫🇷', label: 'FR' },
  ];

  let switcherBar = null;

  function updateSwitcherUI() {
    if (!switcherBar) return;
    LANG_META.forEach(({ code }) => {
      const btn = switcherBar.querySelector('[data-lang="' + code + '"]');
      if (!btn) return;
      const active = code === currentLang;
      btn.style.opacity      = active ? '1'   : '0.55';
      btn.style.borderColor  = active ? 'var(--accent)' : 'var(--border)';
      btn.style.fontWeight   = active ? '700' : '400';
    });
  }

  function injectLangSwitcher() {
    switcherBar = document.createElement('div');
    switcherBar.style.cssText =
      'position:fixed;top:8px;right:8px;z-index:9999;display:flex;gap:3px;';

    LANG_META.forEach(({ code, flag, label }) => {
      const btn = document.createElement('button');
      btn.className       = 'btn btn-secondary';
      btn.dataset.lang    = code;
      btn.style.cssText   = 'font-size:11px;padding:3px 7px;transition:opacity .15s,border-color .15s;';
      btn.textContent     = flag + ' ' + label;

      const hasData = !!(window.LOCALES[code] && Object.keys(window.LOCALES[code]).length > 0);
      if (!hasData) {
        // Stub file loaded but empty — dim and disable
        btn.disabled          = true;
        btn.style.opacity     = '0.3';
        btn.title             = 'Translation in progress';
      } else {
        btn.onclick = () => setLang(code);
      }

      switcherBar.appendChild(btn);
    });

    document.body.appendChild(switcherBar);
    updateSwitcherUI();
  }

  // ── Public API ────────────────────────────────────────────────────────────
  window.I18N = { get lang() { return currentLang; }, t: window.t, setLang, applyDOM };

  document.addEventListener('DOMContentLoaded', () => {
    applyDOM();
    injectLangSwitcher();
  });

})();
