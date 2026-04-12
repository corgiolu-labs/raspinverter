// settings.mod.js - bootstrap sicuro, validazioni e azioni Salva / Archivia / Relay - OTTIMIZZATO
(function () {
  // Avvio quando il DOM è pronto
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }

  // ----------------- Helpers DOM & UI - OTTIMIZZATI -----------------
  // Cache DOM per performance
  const domCache = new Map();
  function el(id){ 
    if (!domCache.has(id)) {
      domCache.set(id, document.getElementById(id));
    }
    return domCache.get(id);
  }
  
  // Funzione per aggiornare l'indicatore dello stato del relay
  function updateRelayStatusIndicator(state, enabled, gpioLevel = null) {
    const statusEl = el('relayStatus');
    const statusTextEl = el('relayStatusText');
    
    if (!statusEl || !statusTextEl) return;
    
    let statusText = '';
    let bgColor = '';
    let borderColor = '';
    let textColor = '';
    
    if (!enabled) {
      statusText = 'Relay disabilitato';
      bgColor = '#fef2f2';
      borderColor = '#fecaca';
      textColor = '#dc2626';
    } else if (state === null) {
      statusText = 'Stato relay: Sconosciuto';
      bgColor = '#f1f5f9';
      borderColor = '#e2e8f0';
      textColor = '#64748b';
    } else if (state) {
      statusText = `Relay: ON ${gpioLevel !== null ? `(GPIO: ${gpioLevel})` : ''}`;
      bgColor = '#f0fdf4';
      borderColor = '#bbf7d0';
      textColor = '#16a34a';
    } else {
      statusText = `Relay: OFF ${gpioLevel !== null ? `(GPIO: ${gpioLevel})` : ''}`;
      bgColor = '#fef2f2';
      borderColor = '#fecaca';
      textColor = '#dc2626';
    }
    
    statusEl.style.background = bgColor;
    statusEl.style.borderColor = borderColor;
    statusTextEl.style.color = textColor;
    statusTextEl.textContent = statusText;
  }
  
  function setVal(id, v){ 
    const e = el(id); 
    if (e) {
      if (e.type === 'checkbox') {
        e.checked = !!v;
      } else {
        e.value = (v ?? ''); 
      }
    }
  }
  function getVal(id){ 
    const e = el(id); 
    if (e) {
      if (e.type === 'checkbox') {
        return e.checked;
      } else {
        return e.value; 
      }
    }
    return '';
  }

  // Ottimizzazione messaggi con debouncing
  let messageTimeout = null;
  function showMsg(text, ok = true) {
    const m = el('msg');
    if (!m) return;
    
    // Debouncing per evitare flickering
    if (messageTimeout) clearTimeout(messageTimeout);
    messageTimeout = setTimeout(() => {
    m.textContent = text;
    m.classList.remove('ok', 'err');
    m.classList.add(ok ? 'ok' : 'err', 'banner');
    m.hidden = false;
      
      // Nascondi messaggio dopo 5 secondi se è di successo
      if (ok) {
        setTimeout(() => {
          m.hidden = true;
        }, 5000);
      }
    }, 50);
  }

  // Cache per errori per evitare re-render
  const errorCache = new Set();
  function clearErrors() {
    const errorIds = [
      'b_vnom','b_ah','soc_vmax','soc_vmin','b_type',
      'net_reset_voltage','relay_mode','relay_gpio_pin','relay_on_v','relay_off_v','relay_min_toggle_sec',
      'serial_port','serial_baudrate','serial_parity','serial_stopbits','serial_bytesize','serial_timeout','serial_unit_id',
      'poll_interval_sec'
    ];
    
    errorIds.forEach(id => { 
      const e = el(id); 
      if (e && errorCache.has(id)) {
        e.classList.remove('err');
        errorCache.delete(id);
      }
    });
  }

  function markInvalid(ids) {
    clearErrors();
    (ids || []).forEach(id => { 
      const e = el(id); 
      if (e && !errorCache.has(id)) {
        e.classList.add('err');
        errorCache.add(id);
      }
    });
  }

  // Ottimizzazione conversione numeri con cache
  const numCache = new Map();
  function toNum(v) {
    if (v === '' || v === null || v === undefined) return NaN;
    
    const key = String(v);
    if (numCache.has(key)) return numCache.get(key);
    
    const n = Number(v);
    const result = Number.isFinite(n) ? n : NaN;
    numCache.set(key, result);
    return result;
  }

  // ----------------- API - OTTIMIZZATE -----------------
  // Cache per configurazioni
  let configCache = null;
  let lastConfigFetch = 0;
  const CONFIG_CACHE_TTL = 30000; // 30 secondi
  
  async function apiGet() {
    const now = Date.now();
    
    // Usa cache se ancora valida
    if (configCache && (now - lastConfigFetch) < CONFIG_CACHE_TTL) {
      return configCache;
    }
    
    try {
    const r = await fetch('/api/config', { cache: 'no-store' });
    if (!r.ok) throw new Error('GET /api/config ' + r.status);
      
      const result = await r.json();
      
      // Aggiorna cache
      configCache = result;
      lastConfigFetch = now;
      
      return result;
    } catch (error) {
      console.error('Errore API GET:', error);
      throw error;
    }
  }

  // Ottimizzazione POST con timeout e retry
  async function apiPost(body) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 15000); // 15s timeout
    
    // DEBUG: Log della configurazione inviata (commentato per produzione)
    // console.log('🔍 Configurazione inviata al backend:', JSON.stringify(body, null, 2));
    // console.log('🔍 Campi SOC inclusi:', Object.keys(body.battery.soc));
    // console.log('🔍 Valori SOC:', body.battery.soc);
    
    try {
    const r = await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
        signal: controller.signal
    });
      
      clearTimeout(timeoutId);
      
    const t = await r.text();
    if (!r.ok) throw new Error('POST /api/config ' + r.status + ' ' + t);
      
      try { 
        const result = JSON.parse(t);
        // Invalida cache dopo modifica
        configCache = null;
        return result;
      } catch { 
        return { ok:false, error:'Invalid JSON' }; 
      }
    } catch (error) {
      clearTimeout(timeoutId);
      throw error;
    }
  }

  // ----------------- Validazione & Model - OTTIMIZZATI -----------------
  // Cache per validazioni
  const validationCache = new Map();
  
  function validate() {
    const bad = [];
    const errors = [];

    // --- battery/ui ---
    const b_type = (el('b_type')?.value || 'lifepo4').trim();
    const vnom   = toNum(el('b_vnom')?.value);
    const ah     = toNum(el('b_ah')?.value);
    const net_reset_v = toNum(el('net_reset_voltage')?.value);
    const soc_method = (el('soc_method')?.value || 'energy_balance').trim();
    const vmax   = toNum(el('soc_vmax')?.value);
    const vmin   = toNum(el('soc_vmin')?.value);
    const reset_voltage = toNum(el('soc_reset_voltage')?.value);
    let unit     = 'KW'; // Unità fissa a kW

    // Validazione con cache per performance
    const validationKey = `${vnom}_${ah}_${soc_method}_${vmax}_${vmin}_${reset_voltage}_${unit}_${b_type}`;
    if (validationCache.has(validationKey)) {
      return validationCache.get(validationKey);
    }

    if (!Number.isFinite(vnom)) { errors.push('Tensione nominale non valida.'); bad.push('b_vnom'); }
    else if (vnom < 10 || vnom > 100) { errors.push('Tensione nominale fuori range (10–100 V).'); bad.push('b_vnom'); }

    if (!Number.isFinite(ah)) { errors.push('Capacità nominale non valida.'); bad.push('b_ah'); }
    else if (ah < 1 || ah > 2000) { errors.push('Capacità fuori range (1–2000 Ah).'); bad.push('b_ah'); }

    // Validazione soglia reset net battery (relativa alla tensione nominale)
    if (Number.isFinite(net_reset_v) && Number.isFinite(vnom)) {
      const minNet = vnom * 0.8;
      const maxNet = vnom * 0.9;
      if (net_reset_v < minNet || net_reset_v > maxNet) {
        errors.push(`Soglia reset batteria netta fuori range (${minNet.toFixed(1)}–${maxNet.toFixed(1)} V).`); bad.push('net_reset_voltage');
      }
    } else {
      errors.push('Soglia reset batteria netta non valida.'); bad.push('net_reset_voltage');
    }

    // Validazione SOC in base al metodo
    if (soc_method === 'voltage_based') {
      if (!Number.isFinite(vmax)) { 
        errors.push('SOC Vmax non valido.'); 
        bad.push('soc_vmax'); 
      }
      if (!Number.isFinite(vmin)) { 
        errors.push('SOC Vmin non valido.'); 
        bad.push('soc_vmin'); 
      }
      if (Number.isFinite(vmax) && Number.isFinite(vmin) && vmax <= vmin) {
        errors.push('SOC: Vmax deve essere > Vmin.'); bad.push('soc_vmax','soc_vmin');
      }
    } else if (soc_method === 'energy_balance') {
      // Validazione tensione di reset per metodo energetico
      if (!Number.isFinite(reset_voltage)) { 
        errors.push('Tensione reset non valida.'); 
        bad.push('soc_reset_voltage'); 
      } else {
        // Calcola range valido per la tensione di reset (80-90% della tensione nominale)
        const minResetVoltage = vnom * 0.8;
        const maxResetVoltage = vnom * 0.9;
        
        if (reset_voltage < minResetVoltage || reset_voltage > maxResetVoltage) {
          errors.push(`Tensione reset deve essere tra ${minResetVoltage.toFixed(1)}V e ${maxResetVoltage.toFixed(1)}V (80-90% della tensione nominale).`); 
          bad.push('soc_reset_voltage'); 
        }
      }
    }

    // Unità potenza ora fissa a kW

    // --- relay ---
    const relay_mode  = (getVal('relay_mode') || 'gpio').trim();
    const relay_en    = !!el('relay_enabled')?.checked;
    const gpio_pin    = toNum(getVal('relay_gpio_pin'));
    const active_high = !!el('relay_active_high')?.checked;
    const on_v        = toNum(getVal('relay_on_v'));
    const off_v       = toNum(getVal('relay_off_v'));
    const min_toggle  = toNum(getVal('relay_min_toggle_sec'));

    if (relay_en && relay_mode === 'gpio') {
      if (!Number.isFinite(gpio_pin) || gpio_pin < 1 || gpio_pin > 40) {
        errors.push('Relay: GPIO pin non valido (1-40).'); bad.push('relay_gpio_pin');
      }
    }

    if (relay_en && Number.isFinite(on_v) && Number.isFinite(off_v)) {
      if (off_v <= on_v) {
        errors.push('Relay: V_off deve essere > V_on (spegnimento > accensione).'); bad.push('relay_off_v','relay_on_v');
      }
    }

    if (relay_en && Number.isFinite(min_toggle) && min_toggle < 1) {
      errors.push('Relay: tempo minimo toggle deve essere >= 1s.'); bad.push('relay_min_toggle_sec');
    }

    // --- serial ---
    const port = (getVal('serial_port') || '').trim();
    const baud = toNum(getVal('serial_baudrate'));
    const parity = (getVal('serial_parity') || 'N').toUpperCase();
    const stop = toNum(getVal('serial_stopbits'));
    const bytes = toNum(getVal('serial_bytesize'));
    const timeout = toNum(getVal('serial_timeout'));
    const unit_id = toNum(getVal('serial_unit_id'));

    if (port && !port.match(/^(\/dev\/|COM\d+|tty)/)) {
      errors.push('Serial: porta non valida.'); bad.push('serial_port');
    }

    if (Number.isFinite(baud) && ![9600,19200,38400,57600,115200].includes(baud)) {
      errors.push('Serial: baudrate non valido.'); bad.push('serial_baudrate');
    }

    if (parity && !['N','E','O'].includes(parity)) {
      errors.push('Serial: parità non valida (N/E/O).'); bad.push('serial_parity');
    }

    if (Number.isFinite(stop) && ![1,2].includes(stop)) {
      errors.push('Serial: stop bits non validi (1/2).'); bad.push('serial_stopbits');
    }

    if (Number.isFinite(bytes) && ![7,8].includes(bytes)) {
      errors.push('Serial: byte size non valido (7/8).'); bad.push('serial_bytesize');
    }

    if (Number.isFinite(timeout) && (timeout < 0.1 || timeout > 10)) {
      errors.push('Serial: timeout fuori range (0.1-10s).'); bad.push('serial_timeout');
    }

    if (Number.isFinite(unit_id) && (unit_id < 1 || unit_id > 247)) {
      errors.push('Serial: unit ID fuori range (1-247).'); bad.push('serial_unit_id');
    }

    // --- polling ---
    const poll_sec = toNum(getVal('poll_interval_sec'));
    if (Number.isFinite(poll_sec) && (poll_sec < 1 || poll_sec > 60)) {
      errors.push('Polling: intervallo fuori range (1-60s).'); bad.push('poll_interval_sec');
    }

    const result = { errors, bad, valid: errors.length === 0 };
    
    // Cache risultato validazione
    validationCache.set(validationKey, result);
    
    // Limita dimensione cache
    if (validationCache.size > 100) {
      const firstKey = validationCache.keys().next().value;
      validationCache.delete(firstKey);
    }
    
    return result;
  }

  // ----------------- UI Binding - OTTIMIZZATO -----------------
  function bindUI() {
    // Binding ottimizzato con event delegation
    const form = el('configForm');
    if (form) {
      form.addEventListener('submit', handleSubmit);
      
      // Event delegation per input changes
      form.addEventListener('change', (e) => {
        if (e.target.matches('input, select')) {
          // Pulisci cache validazione quando cambiano i valori
          validationCache.clear();
          clearErrors();
        }
      });
    }

    // Binding per pulsanti
    const saveBtn = el('btnSave');
    const loadBtn = el('btnLoad');
    const archiveBtn = el('btnArchive');
    const relayOnBtn = el('btnRelayOn');
    const relayOffBtn = el('btnRelayOff');
    const relayStateBtn = el('btnRelayState');

    if (saveBtn) saveBtn.addEventListener('click', handleSubmit);
    if (loadBtn) loadBtn.addEventListener('click', handleLoad);
    if (archiveBtn) archiveBtn.addEventListener('click', handleArchive);
    if (relayOnBtn) relayOnBtn.addEventListener('click', () => relayControl('on'));
    if (relayOffBtn) relayOffBtn.addEventListener('click', () => relayControl('off'));
    if (relayStateBtn) relayStateBtn.addEventListener('click', () => relayControl('state'));
    
    // Listener per il checkbox del relay enabled
    const relayEnabledCheckbox = el('relay_enabled');
    if (relayEnabledCheckbox) {
      relayEnabledCheckbox.addEventListener('change', () => {
        const enabled = relayEnabledCheckbox.checked;
        updateRelayStatusIndicator(null, enabled);
      });
    }
    
    // Listener per il pulsante reset batteria
    const batteryResetBtn = el('btnBatteryReset');
    if (batteryResetBtn) {
      batteryResetBtn.addEventListener('click', handleBatteryReset);
    }
    
    // Carica informazioni contatore batteria
    loadBatteryCounterInfo();

    // Gestione cambio metodo SOC
    setupSOCMethodToggle();
    // Calcolo automatico capacità in Wh
    updateCapacityDisplay();
    // Setup event listeners per calcolo automatico
    setupCapacityCalculation();
  }

  // ----------------- Handlers - OTTIMIZZATI -----------------
  async function handleSubmit(e) {
    if (e) e.preventDefault();
    
    const v = validate();
    if (!v.valid) {
      markInvalid(v.bad);
      showMsg(v.errors.join(' '), false);
      return;
    }

    try {
      showMsg('Salvataggio configurazione...', true);
      
      const cfg = buildConfig();
      const result = await apiPost(cfg);
      
      if (result.ok) {
        showMsg('✅ Configurazione salvata con successo!', true);
        // Ricarica configurazione per aggiornare cache
        configCache = null;
        await loadConfig();
      } else {
        showMsg('❌ Errore: ' + (result.error || 'sconosciuto'), false);
      }
    } catch (error) {
      showMsg('❌ Errore: ' + error.message, false);
    }
  }

  async function handleLoad() {
    try {
      showMsg('Caricamento configurazione...', true);
      
      // Invalida cache per forzare ricaricamento
      configCache = null;
      await loadConfig();
      
      showMsg('✅ Configurazione caricata!', true);
    } catch (error) {
      showMsg('❌ Errore caricamento: ' + error.message, false);
    }
  }

  async function handleArchive() {
    if (!confirm('🗄️ Archiviare i dati storici?\n\nQuesta operazione:\n• Archivia tutti i giorni precedenti ad oggi\n• Cancella i campioni minuti dei giorni passati\n• Esegue VACUUM per ridurre lo spazio\n• NON può essere annullata\n\nProcedere?')) {
        return;
      }

    try {
      const progressEl = el('archiveProgress');
      const archiveBtn = el('btnArchive');
      // Busy state
      if (archiveBtn) { archiveBtn.classList.add('loading'); archiveBtn.disabled = true; }
      showMsg('Archiviazione: preparazione...', true);
      if (progressEl) progressEl.textContent = 'Preparazione... (analisi dati, dry-run)';

      // 1) Dry-run
      const r0 = await fetch('/api/maintenance/archive?scope=upto_today&dry_run=1', { method: 'POST' });
      if (!r0.ok) throw new Error('HTTP ' + r0.status);
      const pre = await r0.json();
      const days0 = pre.days_to_archive ?? 0;
      const mins0 = pre.minutes_to_delete ?? 0;
      if (progressEl) progressEl.textContent = `Da archiviare: giorni=${days0}, campioni da cancellare=${mins0}...`;
      if (!pre.ok) {
        showMsg('❌ Errore dry-run: ' + (pre.error || 'sconosciuto'), false);
        return;
      }
      if (mins0 === 0) {
        showMsg('✅ Niente da archiviare (giorno corrente escluso).', true);
        if (progressEl) progressEl.textContent = 'Nessuna modifica necessaria.';
        return;
      }

      // 2) Applica con VACUUM
      showMsg('Archiviazione in corso...', true);
      if (progressEl) progressEl.textContent = `Archiviazione in corso... (giorni=${days0}, campioni=${mins0})`;
      const r = await fetch('/api/maintenance/archive?scope=upto_today&vacuum=1', { method: 'POST' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const result = await r.json();
      const delta = (Number(result.size_delta_bytes || 0)/1024/1024).toFixed(2);
      if (result.ok) {
        showMsg(`✅ Archiviazione completata: giorni=${days0}, campioni cancellati=${mins0}, Δ spazio=${delta} MB`, true);
        if (progressEl) progressEl.textContent = `Completata. Riduzione stimata: ${delta} MB`;
      } else {
        showMsg('❌ Errore: ' + (result.error || 'sconosciuto'), false);
        if (progressEl) progressEl.textContent = 'Errore durante archiviazione.';
      }
    } catch (error) {
      showMsg('❌ Errore: ' + error.message, false);
      const progressEl = el('archiveProgress');
      if (progressEl) progressEl.textContent = 'Errore.';
    } finally {
      const archiveBtn = el('btnArchive');
      if (archiveBtn) { archiveBtn.classList.remove('loading'); archiveBtn.disabled = false; }
    }
  }
  
  async function loadBatteryCounterInfo() {
    try {
      const response = await fetch('/api/battery/status');
      if (!response.ok) throw new Error('HTTP ' + response.status);
      
      const result = await response.json();
      if (result.ok) {
        updateBatteryCounterDisplay(result.counter);
      } else {
        throw new Error(result.error || 'Errore sconosciuto');
      }
    } catch (error) {
      console.warn('[battery] Errore caricamento info contatore:', error);
      updateBatteryCounterDisplay(null);
    }
  }
  
  function updateBatteryCounterDisplay(counter) {
    const infoEl = el('batteryCounterInfo');
    if (!infoEl) return;
    
    if (!counter) {
      infoEl.innerHTML = '<span style="color:#dc2626;">Errore caricamento contatore</span>';
      return;
    }
    
    const startDate = new Date(counter.start_timestamp).toLocaleString('it-IT');
    const netEnergy = (counter.total_batt_net_Wh / 1000).toFixed(3); // Converti in kWh
    const inEnergy = (counter.total_batt_in_Wh / 1000).toFixed(3);
    const outEnergy = (counter.total_batt_out_Wh / 1000).toFixed(3);
    
    infoEl.innerHTML = `
      <div style="margin-bottom:8px;">
        <strong>Inizio contatore:</strong> ${startDate}
      </div>
      <div style="margin-bottom:8px;">
        <strong>Energia netta:</strong> <span style="color:#16a34a;">${netEnergy} kWh</span>
      </div>
      <div style="margin-bottom:8px;">
        <strong>Carica totale:</strong> ${inEnergy} kWh | <strong>Scarica totale:</strong> ${outEnergy} kWh
      </div>
      <div style="font-size:12px;color:#94a3b8;">
        Reset: ${counter.reset_reason || 'Nessuno'}
      </div>
    `;
  }
  
  async function handleBatteryReset() {
    if (!confirm('Sei sicuro di voler azzerare il contatore della batteria netta?\n\nQuesto azzererà tutti i contatori e inizierà un nuovo periodo di misurazione.')) {
      return;
    }
    
    try {
      showMsg('Azzeramento contatore batteria...', true);
      
      const response = await fetch('/api/battery/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'manual_reset' })
      });
      
      if (!response.ok) throw new Error('HTTP ' + response.status);
      
      const result = await response.json();
      if (result.ok) {
        showMsg('✅ Contatore batteria azzerato con successo!', true);
        // Ricarica le informazioni del contatore
        await loadBatteryCounterInfo();
      } else {
        throw new Error(result.error || 'Errore sconosciuto');
      }
    } catch (error) {
      showMsg('❌ Errore: ' + error.message, false);
    }
  }

  async function relayControl(action) {
    try {
      showMsg(`Controllo relay ${action}...`, true);
      
      const r = await fetch(`/api/relay/${action}`, { method: 'POST' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      
      const result = await r.json();
      if (result.ok) {
        if (action === 'state') {
          // Per lo stato, mostra informazioni dettagliate
          const stateText = result.state ? 'ON' : 'OFF';
          const gpioInfo = result.gpio_level !== null ? ` (GPIO: ${result.gpio_level})` : '';
          const enabledText = result.enabled ? 'abilitato' : 'disabilitato';
          showMsg(`✅ Relay ${enabledText} - Stato: ${stateText}${gpioInfo}`, true);
          
          // Aggiorna l'indicatore visivo
          updateRelayStatusIndicator(result.state, result.enabled, result.gpio_level);
        } else {
          // Per on/off, mostra messaggio semplice
          const actionText = action === 'on' ? 'attivato' : 'disattivato';
          showMsg(`✅ Relay ${actionText} OK!`, true);
          
          // Aggiorna l'indicatore visivo dopo on/off
          if (action === 'on') {
            updateRelayStatusIndicator(true, true);
          } else if (action === 'off') {
            updateRelayStatusIndicator(false, true);
          }
        }
      } else {
        showMsg('❌ Errore: ' + (result.error || 'sconosciuto'), false);
      }
    } catch (error) {
      showMsg('❌ Errore: ' + error.message, false);
    }
  }

  // ----------------- Config Builder - OTTIMIZZATO -----------------
  function buildConfig() {
    const socMethod = getVal('soc_method') || 'energy_balance';
    
    // Costruisci configurazione SOC in base al metodo
    const socConfig = {};
    if (socMethod === 'voltage_based') {
      socConfig.method = 'voltage_based';
      socConfig.vmax_v = toNum(getVal('soc_vmax'));
      socConfig.vmin_v = toNum(getVal('soc_vmin'));
    } else if (socMethod === 'energy_balance') {
      socConfig.method = 'energy_balance';
      socConfig.reset_voltage = toNum(getVal('soc_reset_voltage'));
      
      // NON includere affatto i campi vmax_v e vmin_v per il metodo energetico
      // Il backend ora gestisce correttamente energy_balance
    }
    
    return {
      battery: {
        type: getVal('b_type') || 'lifepo4',
        nominal_voltage: toNum(getVal('b_vnom')),
        nominal_ah: toNum(getVal('b_ah')),
        net_reset_voltage: toNum(getVal('net_reset_voltage')),
        soc: socConfig
      },
      ui: {
        unit: 'kW' // Unità fissa
      },
      relay: {
        enabled: !!el('relay_enabled')?.checked,
        mode: getVal('relay_mode') || 'gpio',
        gpio_pin: toNum(getVal('relay_gpio_pin')),
        active_high: !!el('relay_active_high')?.checked,
        on_v: toNum(getVal('relay_on_v')),
        off_v: toNum(getVal('relay_off_v')),
        min_toggle_sec: toNum(getVal('relay_min_toggle_sec'))
      },
      serial: {
        port: getVal('serial_port') || '/dev/serial0',
        baudrate: toNum(getVal('serial_baudrate')) || 9600,
        parity: getVal('serial_parity') || 'N',
        stopbits: toNum(getVal('serial_stopbits')) || 1,
        bytesize: toNum(getVal('serial_bytesize')) || 8,
        timeout: toNum(getVal('serial_timeout')) || 1.0,
        unit_id: toNum(getVal('serial_unit_id')) || 1
      },
      polling: {
        interval_sec: toNum(getVal('poll_interval_sec')) || 5.0
      },
      persist: true  // FORZA SALVATAGGIO NEL FILE JSON
    };
  }

  // ----------------- Config Loader - OTTIMIZZATO -----------------
  async function loadConfig() {
    try {
      const cfg = await apiGet();
      
      // Popola form con debouncing per evitare flickering
      requestAnimationFrame(() => {
        populateForm(cfg);
      });
      
      // Aggiorna anche le informazioni del contatore batteria
      await loadBatteryCounterInfo();
      
    } catch (error) {
      showMsg('❌ Errore caricamento: ' + error.message, false);
    }
  }

  function populateForm(cfg) {
    if (!cfg) return;

    // Battery
    if (cfg.battery) {
      setVal('b_type', cfg.battery.type);
      setVal('b_vnom', cfg.battery.nominal_voltage);
      setVal('b_ah', cfg.battery.nominal_ah);
      if (cfg.battery.net_reset_voltage != null) {
        setVal('net_reset_voltage', cfg.battery.net_reset_voltage);
      }
      if (cfg.battery.soc) {
        setVal('soc_method', cfg.battery.soc.method || 'energy_balance');
        
        // Popola campi SOC in base al metodo
        if (cfg.battery.soc.method === 'voltage_based') {
          setVal('soc_vmax', cfg.battery.soc.vmax_v);
          setVal('soc_vmin', cfg.battery.soc.vmin_v);
          
          // Se i valori non sono presenti, imposta valori di default
          if (!cfg.battery.soc.vmax_v || !cfg.battery.soc.vmin_v) {
            const vnom = cfg.battery.nominal_voltage || 48;
            if (!cfg.battery.soc.vmax_v) {
              setVal('soc_vmax', Math.round(vnom * 0.95 * 10) / 10);
            }
            if (!cfg.battery.soc.vmin_v) {
              setVal('soc_vmin', Math.round(vnom * 0.20 * 10) / 10);
            }
          }
        } else if (cfg.battery.soc.method === 'energy_balance') {
          // Imposta il valore dalla configurazione solo se non c'è già un valore inserito dall'utente
          const currentResetVoltage = getVal('soc_reset_voltage');
          if (!currentResetVoltage || currentResetVoltage === '44.0') {
            setVal('soc_reset_voltage', cfg.battery.soc.reset_voltage || 44.0);
          }
        }
        
        // Aggiorna display capacità e mostra/nascondi righe appropriate
        updateCapacityDisplay();
        setupSOCMethodToggle();
      }
    }

    // UI
    if (cfg.ui) {
              // Unità potenza ora fissa a kW
    }

    // Relay
    if (cfg.relay) {
      const relayEnabled = el('relay_enabled');
      if (relayEnabled) relayEnabled.checked = !!cfg.relay.enabled;
      
      setVal('relay_mode', cfg.relay.mode);
      setVal('relay_gpio_pin', cfg.relay.gpio_pin);
      
      const relayActiveHigh = el('relay_active_high');
      if (relayActiveHigh) relayActiveHigh.checked = !!cfg.relay.active_high;
      
      setVal('relay_on_v', cfg.relay.on_v);
      setVal('relay_off_v', cfg.relay.off_v);
      setVal('relay_min_toggle_sec', cfg.relay.min_toggle_sec);
      
      // Aggiorna l'indicatore dello stato del relay
      updateRelayStatusIndicator(null, !!cfg.relay.enabled);
    }

    // Serial
    if (cfg.serial) {
      setVal('serial_port', cfg.serial.port);
      setVal('serial_baudrate', cfg.serial.baudrate);
      setVal('serial_parity', cfg.serial.parity);
      setVal('serial_stopbits', cfg.serial.stopbits);
      setVal('serial_bytesize', cfg.serial.bytesize);
      setVal('serial_timeout', cfg.serial.timeout);
      setVal('serial_unit_id', cfg.serial.unit_id);
    }

    // Polling
    if (cfg.polling) {
      setVal('poll_interval_sec', cfg.polling.interval_sec);
    }
  }

  // Gestione cambio metodo SOC
  function setupSOCMethodToggle() {
    const socMethodSelect = el('soc_method');
    const voltageRows = [el('soc_voltage_row'), el('soc_voltage_row2')];
    const energyRows = [el('soc_energy_row')];
    
    if (socMethodSelect) {
      // Imposta stato iniziale
      const currentMethod = socMethodSelect.value || 'energy_balance';
      toggleSOCFields(currentMethod);
      
      // Listener per cambio metodo
      socMethodSelect.addEventListener('change', function() {
        toggleSOCFields(this.value);
      });
    }
  }
  
  // Funzione per mostrare/nascondere campi SOC appropriati
  function toggleSOCFields(method) {
    const voltageRows = [el('soc_voltage_row'), el('soc_voltage_row2')];
    const energyRows = [el('soc_energy_row')];
    const resetVoltageInput = el('soc_reset_voltage');
    
    if (method === 'energy_balance') {
      // Nascondi campi tensione, mostra campo reset
      voltageRows.forEach(row => {
        if (row) row.style.display = 'none';
      });
      energyRows.forEach(row => {
        if (row) row.style.display = 'table-row';
      });
      
      // Imposta valore di default per tensione di reset SOLO se vuoto
      if (resetVoltageInput && !resetVoltageInput.value) {
        const vnom = parseFloat(getVal('b_vnom')) || 48;
        const defaultResetVoltage = Math.round(vnom * 0.85 * 10) / 10; // 85% della tensione nominale
        resetVoltageInput.value = defaultResetVoltage;
      }
    } else if (method === 'voltage_based') {
      // Mostra campi tensione, nascondi campo reset
      voltageRows.forEach(row => {
        if (row) row.style.display = 'table-row';
      });
      energyRows.forEach(row => {
        if (row) row.style.display = 'none';
      });
      
      // Imposta valori di default per vmax e vmin SOLO se vuoti
      const vmaxInput = el('soc_vmax');
      const vminInput = el('soc_vmin');
      const vnom = parseFloat(getVal('b_vnom')) || 48;
      
      if (vmaxInput && !vmaxInput.value) {
        // Vmax = 95% della tensione nominale
        const defaultVmax = Math.round(vnom * 0.95 * 10) / 10;
        vmaxInput.value = defaultVmax;
      }
      
      if (vminInput && !vminInput.value) {
        // Vmin = 20% della tensione nominale
        const defaultVmin = Math.round(vnom * 0.20 * 10) / 10;
        vminInput.value = defaultVmin;
      }
    }
  }
  
  // Calcolo automatico capacità in Wh
  function updateCapacityDisplay() {
    const vnom = parseFloat(getVal('b_vnom')) || 0;
    const ah = parseFloat(getVal('b_ah')) || 0;
    const capacityWh = vnom * ah;
    
    const displayEl = el('b_capacity_wh_display');
    if (displayEl) {
      if (capacityWh > 0) {
        displayEl.textContent = `${capacityWh.toFixed(0)} Wh`;
        displayEl.style.color = 'var(--text)';
      } else {
        displayEl.textContent = 'Calcolato automaticamente';
        displayEl.style.color = 'var(--muted)';
      }
    }
    

  }
  
  // Setup event listeners per calcolo automatico
  function setupCapacityCalculation() {
    const vnomInput = el('b_vnom');
    const ahInput = el('b_ah');
    
    if (vnomInput) {
      vnomInput.addEventListener('input', () => {
        updateCapacityDisplay();
        updateResetVoltageDefault();
      });
    }
    
    if (ahInput) {
      ahInput.addEventListener('input', updateCapacityDisplay);
        }
  }
  
  // Aggiorna valore di default per tensione di reset quando cambia tensione nominale
  function updateResetVoltageDefault() {
    const socMethod = getVal('soc_method');
    if (socMethod === 'energy_balance') {
      const resetVoltageInput = el('soc_reset_voltage');
      const vnom = parseFloat(getVal('b_vnom')) || 48;
      
      // NON sovrascrivere mai i valori esistenti - solo aggiornare se l'utente lo richiede esplicitamente
      // Rimuovo l'aggiornamento automatico per evitare di perdere i valori inseriti dall'utente
      // if (resetVoltageInput && resetVoltageInput.value) {
      //   const currentRatio = parseFloat(resetVoltageInput.value) / (vnom || 48);
      //   const newResetVoltage = Math.round(vnom * currentRatio * 10) / 10;
      //   resetVoltageInput.value = newResetVoltage;
      // }
    }
  }
  
  // ----------------- Bootstrap - OTTIMIZZATO -----------------
  function bootstrap() {
    // Inizializzazione lazy per performance con fallback cross-browser
    const ric = window.requestIdleCallback || function(fn){ return setTimeout(fn, 0); };
    ric(() => {
      bindUI();
      loadConfig();
    });
  }
})();
