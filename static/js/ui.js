/* Shared interface behaviour (v7):
   mobile sidebar, profile menu, light/dark theme, confirm dialogs, bulk select, row menus,
   copy buttons, dismissible messages, number count-up, "/" to search. No libraries needed. */
(function () {
  'use strict';
  var doc = document, body = doc.body, root = doc.documentElement;
  function $(sel, el) { return (el || doc).querySelector(sel); }
  function $$(sel, el) { return Array.prototype.slice.call((el || doc).querySelectorAll(sel)); }

  /* ---------- sidebar + profile menu ---------- */
  var menuBtn = $('#menu-btn'), overlay = $('#sidebar-overlay');
  var profileBtn = $('#profile-btn'), profileMenu = $('#profile-menu');
  function setSidebar(open) {
    body.classList.toggle('sidebar-open', open);
    if (menuBtn) { menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false'); }
  }
  function closeProfile() {
    if (!profileMenu) { return; }
    profileMenu.classList.add('hidden');
    if (profileBtn) { profileBtn.setAttribute('aria-expanded', 'false'); }
  }
  if (menuBtn) { menuBtn.addEventListener('click', function () { setSidebar(!body.classList.contains('sidebar-open')); }); }
  if (overlay) { overlay.addEventListener('click', function () { setSidebar(false); }); }
  if (profileBtn && profileMenu) {
    profileBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var opening = profileMenu.classList.contains('hidden');
      closeRowMenus();
      profileMenu.classList.toggle('hidden', !opening);
      profileBtn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    doc.addEventListener('click', function (e) { if (!profileMenu.contains(e.target)) { closeProfile(); } });
  }

  /* ---------- theme (dark / light), remembered in this browser ---------- */
  $$('[data-theme-toggle]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('qa-theme', next); } catch (e) { /* private mode: just not remembered */ }
    });
  });

  /* ---------- row action menus (the "..." button) ---------- */
  function closeRowMenus() { $$('.row-menu.open').forEach(function (m) { m.classList.remove('open'); }); }
  function placeMenu(btn, list) {
    // fixed position, so the menu is never cut off by a scrolling table
    var br = btn.getBoundingClientRect();
    var w = list.offsetWidth, h = list.offsetHeight;
    var top = br.bottom + 6;
    if (top + h > window.innerHeight - 8) { top = Math.max(8, br.top - h - 6); }
    list.style.position = 'fixed';
    list.style.right = 'auto';
    list.style.bottom = 'auto';
    list.style.top = top + 'px';
    list.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, br.right - w)) + 'px';
  }
  doc.addEventListener('click', function (e) {
    var btn = e.target.closest ? e.target.closest('[data-menu]') : null;
    if (btn) {
      e.stopPropagation();
      var menu = btn.parentNode;
      var wasOpen = menu.classList.contains('open');
      closeRowMenus(); closeProfile();
      if (!wasOpen) {
        menu.classList.add('open');
        var list = $('.row-menu-list', menu);
        if (list) { placeMenu(btn, list); }
      }
    } else if (!(e.target.closest && e.target.closest('.row-menu'))) {
      closeRowMenus();
    }
  });
  window.addEventListener('scroll', closeRowMenus, true);
  window.addEventListener('resize', closeRowMenus);

  /* ---------- confirm dialog (forms with data-confirm="...") ---------- */
  var dialog = null, lastFocus = null;
  function closeDialog() {
    if (dialog) { dialog.remove(); dialog = null; }
    if (lastFocus && lastFocus.focus) { lastFocus.focus(); }
  }
  function askConfirm(opts, onYes) {
    closeDialog();
    lastFocus = doc.activeElement;
    dialog = doc.createElement('div');
    dialog.className = 'modal-backdrop';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    var box = doc.createElement('div');
    box.className = 'modal ' + (opts.danger ? 'bad' : 'warn');
    var h = doc.createElement('h2'); h.textContent = opts.title || 'Are you sure?';
    var p = doc.createElement('p'); p.className = 'muted'; p.textContent = opts.text || '';
    var row = doc.createElement('div'); row.className = 'modal-actions';
    var no = doc.createElement('button'); no.type = 'button'; no.className = 'btn btn-ghost'; no.textContent = 'Cancel';
    var yes = doc.createElement('button'); yes.type = 'button'; yes.className = 'btn' + (opts.danger ? ' btn-danger' : ''); yes.textContent = opts.yes || 'Confirm';
    row.appendChild(no); row.appendChild(yes);
    box.appendChild(h); box.appendChild(p); box.appendChild(row);
    dialog.appendChild(box); body.appendChild(dialog);
    no.addEventListener('click', closeDialog);
    yes.addEventListener('click', function () { closeDialog(); onYes(); });
    dialog.addEventListener('click', function (e) { if (e.target === dialog) { closeDialog(); } });
    no.focus();
  }
  doc.addEventListener('submit', function (e) {
    var form = e.target;
    var text = form.getAttribute && form.getAttribute('data-confirm');
    if (!text || form.__confirmed) { return; }
    e.preventDefault();
    askConfirm({
      title: form.getAttribute('data-confirm-title') || 'Please confirm',
      text: text,
      yes: form.getAttribute('data-confirm-yes') || 'Yes, continue',
      danger: form.hasAttribute('data-danger')
    }, function () { form.__confirmed = true; form.submit(); });
  });
  doc.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { setSidebar(false); closeProfile(); closeRowMenus(); closeDialog(); }
  });

  /* ---------- bulk select (tables with data-bulk) ---------- */
  $$('[data-bulk]').forEach(function (scope) {
    var all = $('.bulk-all', scope);
    var items = $$('.bulk-item', scope);
    var bar = $('.bulk-bar', scope);
    var count = $('.bulk-count', scope);
    var form = $('.bulk-form', scope);
    var picker = form ? $('select[name=action]', form) : null;
    function refresh() {
      var n = items.filter(function (i) { return i.checked; }).length;
      if (bar) { bar.classList.toggle('show', n > 0); }
      if (count) { count.textContent = n; }
      if (all) { all.checked = n > 0 && n === items.length; all.indeterminate = n > 0 && n < items.length; }
      items.forEach(function (i) { var tr = i.closest('tr'); if (tr) { tr.classList.toggle('picked', i.checked); } });
    }
    if (all) { all.addEventListener('change', function () { items.forEach(function (i) { i.checked = all.checked; }); refresh(); }); }
    items.forEach(function (i) { i.addEventListener('change', refresh); });
    var clear = $('.bulk-clear', scope);
    if (clear) { clear.addEventListener('click', function () { items.forEach(function (i) { i.checked = false; }); refresh(); }); }
    if (form && picker) {
      form.addEventListener('submit', function (e) {
        if (form.__confirmed) { return; }
        var opt = picker.options[picker.selectedIndex];
        if (!picker.value) { e.preventDefault(); e.stopPropagation(); alert('Choose an action first.'); return; }
        var n = items.filter(function (i) { return i.checked; }).length;
        var danger = opt.hasAttribute('data-danger');
        e.preventDefault(); e.stopPropagation();
        askConfirm({
          title: danger ? 'This cannot be undone' : 'Apply to ' + n + ' selected?',
          text: (opt.getAttribute('data-confirm') || ('Apply "' + opt.textContent.trim() + '" to the selected rows?')) + ' (' + n + ' selected)',
          yes: 'Apply to ' + n, danger: danger
        }, function () { form.__confirmed = true; form.submit(); });
      }, true);
    }
    refresh();
  });

  /* ---------- small helpers ---------- */
  $$('[data-autosubmit]').forEach(function (el) { el.addEventListener('change', function () { if (el.form) { el.form.submit(); } }); });

  $$('[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var text = btn.getAttribute('data-copy');
      var done = function () { var old = btn.getAttribute('data-label') || btn.textContent; btn.setAttribute('data-label', old); btn.textContent = 'Copied'; setTimeout(function () { btn.textContent = old; }, 1400); };
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done); }
      else { var t = doc.createElement('textarea'); t.value = text; body.appendChild(t); t.select(); try { doc.execCommand('copy'); done(); } catch (e) { /* ignore */ } t.remove(); }
    });
  });

  $$('.msg').forEach(function (m) {
    m.classList.add('dismissible');
    m.title = 'Click to dismiss';
    m.addEventListener('click', function () { m.remove(); });
  });

  $$('[data-count]').forEach(function (el) {
    var target = parseInt(el.getAttribute('data-count'), 10);
    if (isNaN(target) || target < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
    var start = null, dur = 650;
    function tick(ts) {
      if (start === null) { start = ts; }
      var p = Math.min(1, (ts - start) / dur);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) { requestAnimationFrame(tick); } else { el.textContent = target; }
    }
    requestAnimationFrame(tick);
  });

  doc.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) { return; }
    var t = e.target, tag = t && t.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable)) { return; }
    var s = $('[data-search]');
    if (s) { e.preventDefault(); s.focus(); s.select(); }
  });
})();
