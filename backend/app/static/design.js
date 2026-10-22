// design.js — страница проекта откорма и помещения свиноматок.
// Ходит в маршруты интерфейса (/calc/design, /calc/sow-room), а не в /api/v1:
// там с заданными API_KEYS нужен ключ, которого у браузера посетителя нет.

(function () {
  const $ = id => document.getElementById(id);
  const num = id => parseFloat($(id).value);
  // Перевод с запасным текстом: нет ключа — показываем код как есть
  const tr = (key, fallback) => (window.t && window.t(key) !== key) ? window.t(key) : fallback;
  const fmt = v => (v === null || v === undefined) ? '—' : Number(v).toLocaleString();

  // ── Вкладки ────────────────────────────────────────────────────────────────
  document.querySelectorAll('.tab').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.tab').forEach(b => {
      $('tab-' + b.dataset.tab).style.display = b === btn ? '' : 'none';
    });
  }));

  // ── Справочники ────────────────────────────────────────────────────────────
  // Проект идёт по кривой роста — в списке только профили, у которых она есть
  // Список очищается перед заполнением: редактор профилей просит перечитать его
  // после записи (событие geneticschange), и без очистки профили задвоились бы
  // Список и настройки приходят в любом порядке: профиль хозяйства выбирают оба
  // обработчика, пока пользователь не выбрал сам
  let farm = null, userPicked = false;
  function applyFarmGenetics() {
    if (userPicked || !farm) return;
    if ([...$('dGenetics').options].some(o => o.value === farm.default_genetics)) $('dGenetics').value = farm.default_genetics;
  }
  $('dGenetics').addEventListener('change', () => { userPicked = true; });

  function loadGenetics() {
    fetch('./genetics').then(r => r.json()).then(list => {
      const dg = $('dGenetics'), keep = dg.value;
      dg.innerHTML = '';
      list.filter(p => p.has_growth_curve).forEach(p => dg.add(new Option(p.name, p.id)));
      if (keep && list.some(p => p.id === keep && p.has_growth_curve)) dg.value = keep;
      applyFarmGenetics();
    }).catch(() => {});
  }
  loadGenetics();
  document.addEventListener('geneticschange', loadGenetics);

  // ── Значения по умолчанию из настроек хозяйства (страница инструментов) ──────
  // Климат подставляется, только если он получен по координатам: иначе остаются
  // примерные значения формы, и это видно по пустому источнику климата
  fetch('./settings').then(r => r.json()).then(st => {
    farm = st.farm;
    if (!farm) return;
    $('dMortality').value = farm.mortality_pct;
    if (farm.winter_design_c !== null) {
      $('dWinter').value = farm.winter_design_c;
      $('dSummer').value = farm.summer_design_db_c;
      $('dSummerRh').value = farm.summer_design_rh_pct;
    }
    applyFarmGenetics();
  }).catch(() => {});
  fetch('./sow-profiles').then(r => r.json()).then(list => {
    list.forEach(p => $('sProfile').add(new Option(p.name, p.id)));
  }).catch(() => {});

  function showError(el, body) {
    const d = body && body.detail;
    const code = d && (d.code || d) || 'error';
    el.textContent = tr('err_' + code, typeof d === 'object' ? JSON.stringify(d) : String(code));
    el.style.display = '';
  }

  function fillList(ul, notes, prefix) {
    ul.innerHTML = '';
    (notes || []).forEach(n => {
      const li = document.createElement('li');
      li.textContent = tr(prefix + n.code, n.code);
      ul.appendChild(li);
    });
  }

  // ── Проект ─────────────────────────────────────────────────────────────────
  let lastDesign = null;

  function renderDesign(j) {
    const eq = j.equipment;
    $('eqMin').textContent = fmt(eq.min_ventilation_capacity_m3h);
    $('eqMax').textContent = fmt(eq.max_ventilation_capacity_m3h);
    $('eqHeatNeed').textContent = fmt(eq.heating_need_kw);
    $('eqHeat').textContent = fmt(eq.heating_capacity_kw);
    $('eqLeak').textContent = fmt(j.envelope.leak_4pa_m3h);
    $('eqSummerAir').textContent = fmt(eq.summer_air_c);
    $('eqSpeed').textContent = fmt(eq.speed_to_summer_target_ms);
    // Стресс в самый тяжёлый день, если нужная скорость не обеспечена (Huynh 2005)
    $('eqStress').textContent = fmt(eq.heat_stress.t_c) + ' / ' + fmt(eq.heat_stress.feed_loss_g_day);
    $('eqPadsRow').style.display = eq.pads ? '' : 'none';
    if (eq.pads) $('eqPads').textContent = fmt(eq.pads.area_m2) + ' / ' + fmt(eq.pads.water_l_h);

    const tbody = $('dDays');
    tbody.innerHTML = '';
    j.days.forEach(d => {
      const tr_ = document.createElement('tr');
      [d.day, d.weight_kg, d.setpoint_c, fmt(d.min_ventilation_m3h), tr('basis_' + d.min_basis, d.min_basis),
       d.heating_kw, fmt(d.summer_ventilation_m3h)].forEach(v => {
        const td = document.createElement('td');
        td.textContent = v;
        tr_.appendChild(td);
      });
      tbody.appendChild(tr_);
    });
    fillList($('dWarnings'), j.warnings, 'warn_');
    fillList($('dAssumptions'), j.assumptions, 'note_');
    $('dResult').style.display = '';
  }

  $('dRun').addEventListener('click', async () => {
    $('dError').style.display = 'none';
    const pads = $('dPads').value;
    const body = {
      house: {
        length_m: num('dLength'), width_m: num('dWidth'), wall_height_m: num('dWall'),
        ridge_height_m: num('dRidge') || null,
        envelope: { walls: { u_w_m2k: num('dUWalls') }, roof: { u_w_m2k: num('dURoof') } },
        airtightness: { class: $('dAirtight').value },
        altitude_m: farm ? farm.altitude_m : 0,
      },
      herd: {
        genetics: $('dGenetics').value, count: parseInt($('dCount').value, 10),
        start_day: num('dStart'), end_day: num('dEnd'),
        step_days: parseInt($('dStep').value, 10), mortality_pct: num('dMortality'),
      },
      heating_margin_pct: num('dMargin'),
      climate: { winter_design_c: num('dWinter'), summer_design_db_c: num('dSummer'),
                 summer_design_rh_pct: num('dSummerRh') },
    };
    if (pads) {
      const [type, mm] = pads.split(':');
      body.pads = { pad_type: type, thickness_mm: parseInt(mm, 10) };
    }
    const r = await fetch('./calc/design', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) { showError($('dError'), j); return; }
    lastDesign = j;
    renderDesign(j);
  });

  // ── Помещение свиноматок ───────────────────────────────────────────────────
  let lastSow = null;

  function renderSow(j) {
    $('sRoom').textContent = fmt(j.setpoints.design_room_temp_c);
    $('sNest').textContent = fmt(j.setpoints.nest_c);
    $('sMin').textContent = fmt(j.min_ventilation.total_m3h);
    $('sHeat').textContent = fmt(j.heating.heater_required_kw);
    $('sBasis').textContent = tr('basis_' + j.min_ventilation.basis, j.min_ventilation.basis);
    $('sSummer').textContent = fmt(j.summer_max_per_sow_m3h);
    fillList($('sWarnings'), j.warnings, 'warn_');
    fillList($('sAssumptions'), j.assumptions, 'note_');
    $('sResult').style.display = '';
  }

  $('sRun').addEventListener('click', async () => {
    $('sError').style.display = 'none';
    const stage = $('sStage').value;
    const day = num('sDay');
    const body = {
      profile: $('sProfile').value, stage,
      num_sows: parseInt($('sCount').value, 10), sow_weight_kg: num('sWeight'),
      t_outside_c: num('sTOut'), rh_outside_pct: num('sRhOut'),
      ua_w_per_k: num('sUa') || 0, heater_power_kw: num('sHeater') || 0,
      heater_type: $('sHeaterType').value, creep_heat_w_per_pen: num('sCreep') || 0,
    };
    if (stage === 'lactation') body.lactation_day = day;
    if (stage === 'gestation') body.pregnancy_day = day;
    const r = await fetch('./calc/sow-room', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify(body) });
    const j = await r.json();
    if (!r.ok) { showError($('sError'), j); return; }
    lastSow = j;
    renderSow(j);
  });

  // Тексты, собранные скриптом, перерисовываются при смене языка
  document.addEventListener('langchange', () => {
    if (lastDesign) renderDesign(lastDesign);
    if (lastSow) renderSow(lastSow);
  });
})();
