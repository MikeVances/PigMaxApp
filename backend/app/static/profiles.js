// profiles.js — редактор профилей генетики (вкладка страницы проекта).
// Читает и пишет маршрутами интерфейса /genetics/{id}: формат тот же, что у
// GET /api/v1/genetics/{id}, поэтому загруженный профиль сохраняется копией как есть.

(function () {
  const $ = id => document.getElementById(id);
  const tr = (key, fallback) => (window.t && window.t(key) !== key) ? window.t(key) : fallback;
  const CURVES = { pNurTemp: ['nursery', 'temp_by_weight'], pNurVent: ['nursery', 'min_vent_by_weight'],
                   pNurRh: ['nursery', 'rh_by_weight'], pFinTemp: ['finisher', 'temp_by_weight'],
                   pFinVent: ['finisher', 'min_vent_by_weight'], pFinRh: ['finisher', 'rh_by_weight'] };
  let builtinIds = new Set();

  // ── Токен записи — тот же ключ localStorage, что у настроек главной ──────────
  function loadToken() { try { return localStorage.getItem('pigmax_write_token') || ''; } catch (_) { return tokenFallback; } }
  function saveToken(t) { try { localStorage.setItem('pigmax_write_token', t); } catch (_) {} tokenFallback = t; }
  let tokenFallback = '';

  async function write(method, id, body) {
    const send = () => {
      const headers = { 'Content-Type': 'application/json' };
      const token = loadToken() || tokenFallback;
      if (token) headers['X-Write-Token'] = token;
      return fetch('./genetics/' + encodeURIComponent(id), { method, headers, body: body ? JSON.stringify(body) : undefined });
    };
    let r = await send();
    // 401 — на площадке задан WRITE_TOKEN: спрашиваем один раз и повторяем
    if (r.status === 401) {
      const token = prompt('Write token');
      if (!token) return r;
      saveToken(token);
      r = await send();
    }
    return r;
  }

  // ── Точки: «x y» построчно. Десятичная запятая допустима (7,5 28) — поэтому
  // запятая не разделитель, а разделители — пробелы, табуляция и «;» ────────────
  function parsePoints(text) {
    return text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
      const parts = l.split(/[\s;]+/).map(x => parseFloat(x.replace(',', '.')));
      if (parts.length < 2 || parts.some(Number.isNaN)) throw new Error(l);
      return [parts[0], parts[1]];
    });
  }
  const showPoints = pts => (pts || []).map(p => p[0] + '\t' + p[1]).join('\n');

  function fill(p, copy) {
    $('pId').value = copy ? '' : p.id;
    $('pName').value = copy ? p.name + ' ' + tr('p_copy_suffix', '(copy)') : p.name;
    $('pMeaning').value = p.setpoint_meaning;
    $('pNurseryMax').value = p.nursery_max_kg;
    $('pRhMax').value = p.rh_max_pct ?? '';
    const src = (p.assumptions || []).find(a => a.code === 'user_profile');
    $('pSource').value = src ? src.params.source : (p.citation || '');
    Object.entries(CURVES).forEach(([el, [phase, curve]]) => { $(el).value = showPoints((p[phase] || {})[curve]); });
    // Одна кривая на обе фазы — откорм оставляем пустым, чтобы так и сохранилось
    if (JSON.stringify(p.nursery) === JSON.stringify(p.finisher)) ['pFinTemp', 'pFinVent', 'pFinRh'].forEach(el => { $(el).value = ''; });
    $('pGrowth').value = showPoints(p.growth_by_day);
  }

  function collect() {
    const body = { name: $('pName').value.trim(), setpoint_meaning: $('pMeaning').value,
                   nursery_max_kg: parseFloat($('pNurseryMax').value), source: $('pSource').value.trim(),
                   nursery: {} };
    const rh = parseFloat($('pRhMax').value);
    if (!Number.isNaN(rh)) body.rh_max_pct = rh;
    Object.entries(CURVES).forEach(([el, [phase, curve]]) => {
      const pts = parsePoints($(el).value);
      if (!pts.length) return;
      body[phase] = body[phase] || {};
      body[phase][curve] = pts;
    });
    const growth = parsePoints($('pGrowth').value);
    if (growth.length) body.growth_by_day = growth;
    return body;
  }

  function showError(text) { $('pError').textContent = text; $('pError').style.display = ''; }
  function done(text) {
    $('pDone').textContent = text;
    $('pDone').style.display = '';
    setTimeout(() => { $('pDone').style.display = 'none'; }, 2500);
  }
  function serverError(j) {
    const d = j && j.detail;
    if (Array.isArray(d)) return tr('err_validation_failed', 'Check the fields') + ': ' + d.map(e => (e.loc || []).slice(1).join('.')).join(', ');
    const code = d && d.code || d || 'error';
    return tr('err_' + code, JSON.stringify(d));
  }

  async function refreshList(selectId) {
    const list = await fetch('./genetics').then(r => r.json()).catch(() => []);
    builtinIds = new Set(list.filter(p => p.builtin).map(p => p.id));
    $('pCopyFrom').innerHTML = '';
    list.forEach(p => $('pCopyFrom').add(new Option((p.builtin ? '' : '★ ') + p.name, p.id)));
    if (selectId) $('pCopyFrom').value = selectId;
  }

  $('pLoad').addEventListener('click', async () => {
    $('pError').style.display = 'none';
    const id = $('pCopyFrom').value;
    const r = await fetch('./genetics/' + encodeURIComponent(id));
    const j = await r.json();
    if (!r.ok) return showError(serverError(j));
    // Встроенный — заготовка копии с пустым id; свой — правка на месте
    fill(j, builtinIds.has(id));
  });

  $('pSave').addEventListener('click', async () => {
    $('pError').style.display = 'none';
    let body;
    try { body = collect(); } catch (e) { return showError(tr('p_bad_line', 'Cannot read the line') + ': ' + e.message); }
    const id = $('pId').value.trim();
    const r = await write('PUT', id, body);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return showError(r.status === 404 || r.status === 405 ? tr('err_profile_id_invalid', 'Bad id') : serverError(j));
    done(tr('p_saved', 'Saved'));
    refreshList(id);
    // Вкладка проекта перечитает свой список: новый профиль с кривой роста сразу в расчёте
    document.dispatchEvent(new Event('geneticschange'));
  });

  $('pDelete').addEventListener('click', async () => {
    $('pError').style.display = 'none';
    const id = $('pId').value.trim();
    if (!id || !confirm(tr('p_confirm_delete', 'Delete profile') + ' ' + id + '?')) return;
    const r = await write('DELETE', id);
    if (!r.ok) return showError(serverError(await r.json().catch(() => ({}))));
    done(tr('p_deleted', 'Deleted'));
    refreshList();
    document.dispatchEvent(new Event('geneticschange'));
  });

  refreshList();
})();
