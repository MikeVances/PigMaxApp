// house.js — выбор корпуса фермы, общий для главной и страницы инструментов.
// Список корпусов хранит сервер (GET /settings → houses); браузер помнит только,
// какой корпус открыт, — удобство конкретного посетителя, а не данные фермы.
// Страница вешает панель в элемент #houseBar и слушает событие housechange.

(function () {
  const KEY = 'pigmax_house';
  const tr = (k, fb) => (window.t && window.t(k) !== k) ? window.t(k) : fb;
  let current = null;
  try { current = localStorage.getItem(KEY); } catch (_) {}

  // Токен записи — тот же ключ, что у остальных страниц
  let tokenFallback = '';
  function loadToken() { try { return localStorage.getItem('pigmax_write_token') || tokenFallback; } catch (_) { return tokenFallback; } }
  function saveToken(t) { try { localStorage.setItem('pigmax_write_token', t); } catch (_) {} tokenFallback = t; }
  async function write(method, url, body) {
    const send = () => {
      const headers = { 'Content-Type': 'application/json' };
      const token = loadToken();
      if (token) headers['X-Write-Token'] = token;
      return fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
    };
    let r = await send();
    if (r.status === 401) {
      const token = prompt('Write token');
      if (!token) return r;
      saveToken(token);
      r = await send();
    }
    return r;
  }

  function url(path) {
    return current ? path + (path.includes('?') ? '&' : '?') + 'house=' + encodeURIComponent(current) : path;
  }
  function select(id) {
    current = id;
    try { localStorage.setItem(KEY, id); } catch (_) {}
    // Список на странице — тоже: после «Новый» сервер уже знает корпус, а выбор
    // в списке оставался бы на прежнем, и страница показывала бы чужой корпус
    const sel = document.getElementById('houseSelect');
    if (sel) sel.value = id;
    document.dispatchEvent(new CustomEvent('housechange', { detail: id }));
  }

  function render(houses) {
    const bar = document.getElementById('houseBar');
    if (!bar) return;
    bar.innerHTML = '';
    const label = document.createElement('span');
    label.className = 'muted small'; label.textContent = tr('house_label', 'House');
    const sel = document.createElement('select');
    sel.className = 'field'; sel.id = 'houseSelect';
    houses.forEach(h => sel.add(new Option(h, h)));
    sel.value = current;
    sel.addEventListener('change', () => select(sel.value));
    const btn = (key, fb, fn) => {
      const b = document.createElement('button');
      b.className = 'btn btn-secondary'; b.textContent = tr(key, fb); b.addEventListener('click', fn);
      return b;
    };
    const err = document.createElement('span'); err.className = 'alert-danger'; err.id = 'houseError';
    const fail = async r => {
      const j = await r.json().catch(() => ({}));
      const code = j.detail && j.detail.code;
      err.textContent = code ? tr('err_' + code, code) : (Array.isArray(j.detail) ? tr('err_house_name', 'Bad name') : String(r.status));
    };
    bar.append(label, sel,
      btn('house_new', 'New', async () => {
        const id = prompt(tr('house_new_prompt', 'Name of the new house'));
        if (!id) return;
        // Новый корпус — копия размеров и отопителя открытого: корпуса фермы обычно типовые
        const r = await write('POST', './houses', { house_id: id.trim(), copy_from: current });
        if (!r.ok) return fail(r);
        await refresh(); select(id.trim());
      }),
      btn('house_rename', 'Rename', async () => {
        const id = prompt(tr('house_rename_prompt', 'New name'), current);
        if (!id || id.trim() === current) return;
        const r = await write('POST', './houses/' + encodeURIComponent(current) + '/rename', { new_house_id: id.trim() });
        if (!r.ok) return fail(r);
        current = id.trim(); await refresh(); select(current);
      }),
      btn('house_delete', 'Delete', async () => {
        if (!confirm(tr('house_delete_confirm', 'Delete house') + ' ' + current + '?')) return;
        const r = await write('DELETE', './houses/' + encodeURIComponent(current));
        if (!r.ok) return fail(r);
        const j = await r.json();
        await refresh(); select(j.houses[0]);
      }),
      err);
  }

  async function refresh() {
    const r = await fetch('./settings');
    if (!r.ok) return;
    const houses = (await r.json()).houses || [];
    // Запомненного корпуса больше нет (удалён с другого устройства) — первый
    if (!houses.includes(current)) {
      current = houses[0] || null;
      try { localStorage.setItem(KEY, current); } catch (_) {}
    }
    render(houses);
  }

  window.PigHouse = { url, get current() { return current; }, refresh };
  document.addEventListener('langchange', refresh);
  window.PigHouse.ready = refresh();
})();
