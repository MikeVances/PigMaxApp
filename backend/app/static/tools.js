// tools.js — страница инструментов: ограждение, герметичность, калибровка UA,
// панельное охлаждение, туннельные вентиляторы, эффективная температура.
// Каждый инструмент — маршрут интерфейса /calc/* (без API-ключа); запись в
// настройки корпуса — PUT /settings под токеном записи, как на главной.

(function () {
  const $ = id => document.getElementById(id);
  const num = id => parseFloat($(id).value);
  const opt = id => { const v = num(id); return Number.isNaN(v) ? null : v; };
  const tr = (key, fallback) => (window.t && window.t(key) !== key) ? window.t(key) : fallback;
  const fmt = (v, d) => (v === null || v === undefined) ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d ?? 1 });

  // ── Вкладки ────────────────────────────────────────────────────────────────
  document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => {
      b.classList.toggle('active', b === btn);
      $('tool-' + b.dataset.tab).style.display = b === btn ? '' : 'none';
    });
  }));

  // ── Точки «x y» построчно; десятичная запятая допустима (как в редакторе профилей)
  function parsePoints(text) {
    return text.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
      const parts = l.split(/[\s;]+/).map(x => parseFloat(x.replace(',', '.')));
      if (parts.length < 2 || parts.some(Number.isNaN)) throw new Error(l);
      return [parts[0], parts[1]];
    });
  }

  // ── Вывод: строки «подпись — значение», предупреждения и допущения кодами ──
  const last = {};   // последний ответ инструмента — перерисовка при смене языка
  function rows(prefix, list) {
    const tbody = $(prefix + 'Rows');
    tbody.innerHTML = '';
    list.forEach(([key, fallback, value]) => {
      const tr_ = document.createElement('tr');
      const a = document.createElement('td'); a.className = 'muted'; a.textContent = tr(key, fallback);
      const b = document.createElement('td'); b.className = 'num';
      const strong = document.createElement('strong'); strong.textContent = value;
      b.appendChild(strong); tr_.append(a, b); tbody.appendChild(tr_);
    });
  }
  function notes(ul, list, prefix) {
    ul.innerHTML = '';
    (list || []).forEach(n => {
      const code = typeof n === 'string' ? n : n.code;
      const li = document.createElement('li');
      li.textContent = tr(prefix + code, code);
      ul.appendChild(li);
    });
  }
  function show(prefix, j, render) {
    last[prefix] = { j, render };
    render(j);
    $(prefix + 'Result').style.display = '';
  }
  function error(prefix, j) {
    const d = j && j.detail;
    let text;
    if (Array.isArray(d)) text = tr('err_validation_failed', 'Check the fields') + ': ' + d.map(e => (e.loc || []).slice(1).join('.')).join(', ');
    else if (d && d.code) text = tr('err_' + d.code, d.code + ' ' + JSON.stringify(d.params || {}));
    else text = String(d || 'error');
    $(prefix + 'Error').textContent = text;
    $(prefix + 'Error').style.display = '';
  }
  async function post(prefix, url, body) {
    $(prefix + 'Error').style.display = 'none';
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { error(prefix, j); return null; }
    return j;
  }
  function badLine(prefix, e) {
    $(prefix + 'Error').textContent = tr('p_bad_line', 'Cannot read the line') + ': ' + e.message;
    $(prefix + 'Error').style.display = '';
  }

  // ── Запись в настройки корпуса (токен — тот же ключ, что на главной) ──────────
  let tokenFallback = '';
  function loadToken() { try { return localStorage.getItem('pigmax_write_token') || tokenFallback; } catch (_) { return tokenFallback; } }
  function saveToken(t) { try { localStorage.setItem('pigmax_write_token', t); } catch (_) {} tokenFallback = t; }
  async function putSettings(payload) {
    const send = () => {
      const headers = { 'Content-Type': 'application/json' };
      const token = loadToken();
      if (token) headers['X-Write-Token'] = token;
      return fetch('./settings', { method: 'PUT', headers, body: JSON.stringify(payload) });
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
  function done(id, text) {
    $(id).textContent = text;
    $(id).style.display = '';
    setTimeout(() => { $(id).style.display = 'none'; }, 3000);
  }

  // ── Ограждение ─────────────────────────────────────────────────────────────
  let envelopeUa = null;
  $('eRun').addEventListener('click', async () => {
    const body = { length_m: num('eLength'), width_m: num('eWidth'), wall_height_m: num('eWall'),
                   ridge_height_m: opt('eRidge'), walls: { u_w_m2k: num('eUWalls') }, roof: { u_w_m2k: num('eURoof') } };
    if (opt('eUEnd') !== null) body.end_walls = { u_w_m2k: opt('eUEnd') };
    const j = await post('e', './calc/envelope', body);
    if (!j) return;
    envelopeUa = j.ua_w_per_k;
    show('e', j, j => rows('e', [
      ['tl_ua', 'UA, W/K', fmt(j.ua_w_per_k)],
      ['tl_mean_u', 'Mean U', fmt(j.mean_u_w_m2k, 3)],
      ['tl_u_side', 'Side walls', fmt(j.u_w_m2k.side_walls, 3) + ' / ' + fmt(j.areas_m2.side_walls) + ' m²'],
      ['tl_u_end_res', 'End walls', fmt(j.u_w_m2k.end_walls, 3) + ' / ' + fmt(j.areas_m2.end_walls) + ' m²'],
      ['tl_u_roof', 'Roof', fmt(j.u_w_m2k.roof, 3) + ' / ' + fmt(j.areas_m2.roof) + ' m²'],
    ]));
  });
  $('eSave').addEventListener('click', async () => {
    if (envelopeUa === null) return;
    const r = await putSettings({ ua_coefficient: envelopeUa });
    if (r.ok) done('eDone', tr('settings_saved', 'Saved') + ' UA ' + fmt(envelopeUa));
    else error('e', await r.json().catch(() => ({})));
  });

  // ── Герметичность ──────────────────────────────────────────────────────────
  $('lRun').addEventListener('click', async () => {
    let extra;
    try { extra = parsePoints($('lExtra').value); } catch (e) { return badLine('l', e); }
    const j = await post('l', './calc/leakage', { house_length_m: num('lLength'), house_width_m: num('lWidth'),
      fan_capacity_m3h: num('lFan'), measured_pressure_pa: num('lPressure'), extra_points: extra });
    if (!j) return;
    show('l', j, j => {
      const list = [
        ['tl_ela', 'ELA, m²', fmt(j.ela_m2, 2)],
        ['tl_relative', 'Relative leakage', fmt(j.relative_leakage, 2)],
        ['tl_class', 'Class', tr('tightness_' + j.tightness_class, j.tightness_class)],
      ];
      // Утечка при 4 Па и показатель — только с несколькими точками теста
      if (j.leak_at_4pa_m3h !== null) list.push(['tl_leak4', 'Leak at 4 Pa', fmt(j.leak_at_4pa_m3h, 0)],
        ['tl_exponent', 'n', fmt(j.flow_exponent_n, 2)], ['tl_infiltration', 'kW per 10 °C', fmt(j.infiltration_kw_per_10k, 1)]);
      rows('l', list);
      // n вне 0.5–1.0 — точки теста противоречат друг другу: утечка при 4 Па ненадёжна
      notes($('lWarnings'), j.exponent_out_of_range ? ['exponent_out_of_range'] : [], 'tl_');
    });
  });

  // ── Калибровка UA: секундомер с отметкой на каждом градусе падения ──────────
  let startedAt = null, clockTimer = null;
  function appendPoint(s, temp) {
    const text = $('cPoints').value.trim();
    $('cPoints').value = (text ? text + '\n' : '') + s + '\t' + temp;
  }
  $('cStart').addEventListener('click', () => {
    startedAt = Date.now();
    $('cPoints').value = '';
    appendPoint(0, num('cStartTemp'));
    $('cTap').disabled = false;
    clearInterval(clockTimer);
    clockTimer = setInterval(() => {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      $('cClock').textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    }, 500);
  });
  $('cTap').addEventListener('click', () => {
    const pts = parsePoints($('cPoints').value);
    const prev = pts.length ? pts[pts.length - 1][1] : num('cStartTemp');
    appendPoint(Math.round((Date.now() - startedAt) / 1000), +(prev - 1).toFixed(1));
  });
  $('cReset').addEventListener('click', () => {
    clearInterval(clockTimer); startedAt = null;
    $('cTap').disabled = true; $('cPoints').value = ''; $('cClock').textContent = '0:00';
  });
  function calibrationBody() {
    return { t_outside_c: num('cTOut'), points: parsePoints($('cPoints').value).map(([t_s, temp_c]) => ({ t_s, temp_c })) };
  }
  $('cRun').addEventListener('click', async () => {
    let body;
    try { body = calibrationBody(); } catch (e) { return badLine('c', e); }
    if (opt('cVolume') !== null) body.volume_m3 = opt('cVolume');
    const j = await post('c', './calc/calibrate-ua', body);
    if (!j) return;
    show('c', j, j => {
      rows('c', [
        ['tl_ua', 'UA, W/K', fmt(j.ua_coefficient)],
        ['tl_volume', 'Volume, m³', fmt(j.volume_m3)],
        ['tl_duration', 'Duration, min', fmt(j.duration_minutes)],
        ['tl_rms', 'RMS, °C', fmt(j.rms_c, 3)],
        ['tl_c_struct', 'C structures, kJ/K', j.c_struct_resolved ? fmt(j.c_struct_j_per_k / 1000, 0) : '—'],
      ]);
      notes($('cWarnings'), j.warnings, 'cal_warn_');
    });
  });
  $('cSave').addEventListener('click', async () => {
    let body;
    try { body = calibrationBody(); } catch (e) { return badLine('c', e); }
    // Сохранение пересчитывает UA по объёму СОХРАНЁННОГО корпуса — показываем то, что легло
    const r = await putSettings({ calibration: body });
    const j = await r.json().catch(() => ({}));
    if (r.ok) done('cDone', tr('settings_saved', 'Saved') + ' UA ' + fmt(j.ua_coefficient));
    else error('c', j);
  });

  // ── Панельное охлаждение ───────────────────────────────────────────────────
  $('vRun').addEventListener('click', async () => {
    const j = await post('v', './calc/evap-cooling', {
      house_length_m: num('vLength'), t_outdoor_db_c: num('vTOut'), rh_outdoor_pct: num('vRh'),
      total_fan_m3h: num('vFan'), pad_type: $('vType').value, pad_thickness_mm: parseInt($('vThick').value, 10),
      pad_length_m: num('vPadLen'), pad_height_m: num('vPadH'), t_inside_target_c: num('vTarget'),
      num_animals: parseInt($('vCount').value, 10) || 0, animal_weight_kg: opt('vWeight') || 0 });
    if (!j) return;
    show('v', j, j => rows('v', [
      ['tl_status', 'Status', tr('evap_status_' + j.status_code, j.status_code)],
      ['tl_t_after_pad', 'After pads, °C', fmt(j.t_pad_out_c)],
      ['tl_t_at_fans', 'At fans, °C', fmt(j.t_fan_end_c)],
      ['tl_pad_velocity', 'Pad speed, m/s', fmt(j.air_velocity_ms, 2)],
      ['tl_pad_dp', 'Pad Δp, Pa', fmt(j.pad_pressure_drop_pa)],
      ['tl_water', 'Water, l/h', fmt(j.water_per_hour_l, 0)],
      ['tl_pad_recommended', 'Recommended area, m²', fmt(j.recommended_pad_area_m2)],
    ]));
  });

  // ── Туннельные вентиляторы ─────────────────────────────────────────────────
  $('tRun').addEventListener('click', async () => {
    let curve;
    try { curve = parsePoints($('tCurve').value); } catch (e) { return badLine('t', e); }
    const body = { target_m3h: num('tTarget'), section_m2: num('tSection'), fan_curve: curve,
                   pad_type: $('tType').value || null, pad_thickness_mm: parseInt($('tThick').value, 10) };
    if (opt('tPadArea') !== null) body.pad_area_m2 = opt('tPadArea');
    const j = await post('t', './calc/tunnel-fans', body);
    if (!j) return;
    show('t', j, j => {
      rows('t', [
        ['tl_fans', 'Fans', j.fans],
        ['tl_flow_per_fan', 'Flow per fan', fmt(j.flow_per_fan_m3h, 0)],
        ['tl_op_pressure', 'Pressure, Pa', fmt(j.operating_pressure_pa)],
        ['tl_pad_area', 'Pad area, m²', fmt(j.pad_area_m2)],
      ]);
      notes($('tWarnings'), j.warnings, 'warn_');
    });
  });

  // ── Эффективная температура и тепловой стресс ──────────────────────────────
  $('fRun').addEventListener('click', async () => {
    const body = { t_air_c: num('fTAir'), air_speed_ms: opt('fSpeed') || 0, floor: $('fFloor').value,
                   setpoint_c: opt('fSetpoint'), rh_pct: opt('fRh'), weight_kg: opt('fWeight') };
    const j = await post('f', './calc/effective-temperature', body);
    if (!j) return;
    show('f', j, j => {
      const list = [
        ['tl_t_eff', 'Effective, °C', fmt(j.t_effective_c)],
        ['tl_wind_chill', 'Air speed cooling, °C', fmt(j.wind_chill_c)],
        ['tl_floor_corr', 'Floor, °C', fmt(j.floor_correction_c)],
      ];
      if (j.above_setpoint_c !== null) list.push(['tl_above_setpoint', 'Above setpoint', fmt(j.above_setpoint_c)],
        ['tl_speed_to_setpoint', 'Speed to setpoint', fmt(j.speed_to_setpoint_ms, 2)]);
      if (j.heat_stress) {
        const hs = j.heat_stress;
        list.push(['tl_stress_exceeded', 'Reactions', hs.exceeded.length ? hs.exceeded.map(x => tr('stress_' + x, x)).join(', ') : tr('tl_none', 'none')],
                  ['tl_feed_loss', 'Feed loss, g/day', fmt(hs.feed_loss_g_day, 0)]);
      }
      rows('f', list);
      notes($('fWarnings'), j.warnings, 'warn_');
      notes($('fNotes'), j.assumptions, 'note_');
    });
  });

  // Тексты, собранные скриптом, перерисовываются при смене языка
  document.addEventListener('langchange', () => Object.values(last).forEach(({ j, render }) => render(j)));
})();
