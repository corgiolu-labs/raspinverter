'use strict';
(function(){
  // === THEME / CHART DEFAULTS ==============================================
  function cssVar(name, fallback){
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  var Theme = {
    colors: {
      pv:   cssVar('--c-pv','#22c55e'),
      batt: cssVar('--c-batt','#3b82f6'),
      load: cssVar('--c-load','#f59e0b'),
      grid: cssVar('--c-grid','#a855f7'),
      text: cssVar('--text','#e5e7eb')
    },
    apply: function(){
      if(!window.Chart) return;
      Chart.defaults.color = this.colors.text;
      Chart.defaults.elements.line.tension = .25;
      Chart.defaults.elements.point.radius = 0;
      Chart.defaults.plugins.legend.labels.usePointStyle = true;
      Chart.defaults.maintainAspectRatio = false;
      Chart.defaults.animation = false;

      // Ottimizzazioni mobile (Galaxy A16 & simili)
      var isSmall = window.matchMedia('(max-width: 430px)').matches;
      Chart.defaults.font = Chart.defaults.font || {};
      Chart.defaults.font.size = isSmall ? 11 : 12;
      try {
        Chart.defaults.devicePixelRatio = Math.min(window.devicePixelRatio || 1, isSmall ? 1.5 : 2);
      } catch(e){}
    }
  };
  Theme.apply();

// Registrazione Service Worker (PWA) - Ottimizzato
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(() => console.log('SW registered'))
      .catch(err => console.warn('SW registration failed:', err));
  });
}

  // Flag CSS per schermi piccoli - Ottimizzato con debouncing
  (function(){
    var mq = window.matchMedia('(max-width: 430px)');
    var applyFlag = (function(){
      var timeout;
      return function(){
        clearTimeout(timeout);
        timeout = setTimeout(function(){
          document.documentElement.classList.toggle('mobile', mq.matches);
        }, 16); // 60fps debouncing
      };
    })();
    applyFlag();
    (mq.addEventListener ? mq.addEventListener('change', applyFlag) : mq.addListener(applyFlag));
  })();

  // === OTTIMIZZAZIONI: CACHE DOM E UTILS =================================
  const DOM_CACHE = new Map(); // Cambiato da oggetto a Map per performance
  function getElement(id) {
    if (!DOM_CACHE.has(id)) DOM_CACHE.set(id, document.getElementById(id));
    return DOM_CACHE.get(id);
  }
  
  // Regex precompilato per ottimizzazione
  const UNIT_REGEX = /\((W|kW)\)/;
  
  // Debouncing per aggiornamenti chart - Ottimizzato
  let updateTimeout = null;
  let tickTimeout = null;
  let lastUpdateTime = 0;
  const MIN_UPDATE_INTERVAL = 100; // ms minimo tra aggiornamenti

  // === UI HELPERS ===========================================================
  var badge  = getElement('connBadge');
  var banner = getElement('errorBanner');
  var lastUpdEl = getElement('lastUpdate');

  function setOnline(ok){
    if(!badge) return;
    badge.textContent = ok ? 'ON LINE' : 'OFF LINE';
    badge.classList.toggle('badge-on', ok);
    badge.classList.toggle('badge-off', !ok);
  }
  function showErr(msg){
    if(!banner) return;
    banner.textContent = msg || '';
    banner.hidden = !msg;
    if (msg) console.error(msg);
  }
  function setLastUpdate(ts){ if(lastUpdEl) lastUpdEl.textContent = ts; }

  window.addEventListener('error', function(e){ showErr('JS error: ' + (e.message||e)); });
  window.addEventListener('unhandledrejection', function(e){ showErr('Promise: ' + ((e.reason && e.reason.message) || e.reason)); });

  // === STORE / UTILS ========================================================
  var Store = {
    unit: (localStorage.getItem('UNIT') || 'W'),
    vmin: Number(localStorage.getItem('soc_vmin_v') || 44),
    vmax: Number(localStorage.getItem('soc_vmax_v') || 58),
    socMethod: (localStorage.getItem('soc_method') || 'voltage_based'),
    resetVoltage: Number(localStorage.getItem('soc_reset_voltage') || 44),
    batteryCapacityWh: Number(localStorage.getItem('battery_capacity_wh') || 25600),
    cap : 1440 // punti max nei grafici live
  };
  
  // Funzioni helper
  function fmt(v, d){ var n=Number(v); return isFinite(n) ? n.toFixed(d).replace('.', ',') : '-'; }
  function hhmm(ts){ var t=(ts||'').split(' ')[1]||ts||''; return t.slice(0,5); }
  
  // Ottimizzazione calcolo SOC con cache - Supporta entrambi i metodi
  const socCache = new Map();
  function socFromV(v, batteryNetEnergyWh = null){
    v = Number(v||0);
    
    if (Store.socMethod === 'energy_balance') {
      // Metodo Bilancio Energetico: basato su energia netta reale della batteria
      const key = `${v}_energy_${Store.resetVoltage}_${Store.batteryCapacityWh}_${batteryNetEnergyWh}`;
      if (socCache.has(key)) return socCache.get(key);
      

      
      if (batteryNetEnergyWh !== null && Store.batteryCapacityWh > 0) {
        // Calcolo basato su energia netta reale: SOC = (Energia netta / Energia nominale) * 100
        // L'energia netta viene dalla dashboard (sezione "Energia oggi - Batteria netta")
        
        // SOC basato su energia reale
        let s = (batteryNetEnergyWh / Store.batteryCapacityWh) * 100;
        

        
        // Applica limiti di sicurezza e smoothing
        if (s < 0) s = 0;
        if (s > 100) s = 100;
        
        // Smoothing per evitare salti bruschi
        s = Math.round(s * 10) / 10;
        

        
        socCache.set(key, s);
        return s;
      } else {
        // Fallback: se non abbiamo energia netta, usa il metodo tensione

        const fallbackKey = `${v}_energy_fallback_${Store.resetVoltage}`;
        if (socCache.has(fallbackKey)) return socCache.get(fallbackKey);
        
        // Calcolo semplificato basato su tensione per fallback
        const estimatedNominalVoltage = Store.resetVoltage / 0.85;
        const currentEnergyRatio = (v / estimatedNominalVoltage);
        let s = currentEnergyRatio * 100;
        
        if (s < 0) s = 0; if (s > 100) s = 100;
        s = Math.round(s * 10) / 10;
        
        socCache.set(fallbackKey, s);
        return s;
      }
    } else {
      // Metodo Basato su Tensione: classico vmin/vmax
      const key = `${v}_voltage_${Store.vmin}_${Store.vmax}`;
      if (socCache.has(key)) return socCache.get(key);
      
      if (Store.vmax <= Store.vmin) return 0;
      var s = 100*(v - Store.vmin) / (Store.vmax - Store.vmin);
      if (s<0) s=0; if (s>100) s=100;
      
      socCache.set(key, s);
      return s;
    }
  }

  // === BOXES ================================================================
  function setText(id, txt){ var el=getElement(id); if(el) el.textContent = txt; }
  
  // Ottimizzazione chart SOC con singleton pattern
  let socChart = null;
  function setSOC(p){
    var el=getElement('soc_label'); if(el) el.textContent = p.toFixed(1) + '%';
    var ctxEl=getElement('batteryDonut');
    if (window.Chart && ctxEl){
      if (!socChart){
        socChart = new Chart(ctxEl.getContext('2d'), {
          type:'doughnut',
          data:{ labels:['SOC','Scarica'], datasets:[{ data:[p,100-p], backgroundColor:[Theme.colors.batt,'rgba(255,255,255,.08)'] }] },
          options:{ plugins:{legend:{display:false}}, cutout:'65%' }
        });
      } else {
        socChart.data.datasets[0].data = [p,100-p];
        socChart.update('none'); // Disabilita animazioni per performance
      }
    }
  }
  
  // Ottimizzazione updateBoxes con batch update
  function updateBoxes(d){
    // Batch update per ridurre reflow
    const updates = [];
    
    // PV
    updates.push(() => setText('pv_power', fmt(Number(d.pv_w||0)/1000,2) + ' kW'));
    updates.push(() => setText('pv_v', fmt(d.pv_v,1) + ' V'));
    updates.push(() => setText('pv_a', fmt(d.pv_a,1) + ' A'));
    
    // Battery
    updates.push(() => setText('battery_power', fmt(Number(d.battery_w||0)/1000,2) + ' kW'));
    updates.push(() => setText('battery_v', fmt(d.battery_v,1) + ' V'));
    updates.push(() => setText('battery_a', fmt(d.battery_a,1) + ' A'));
    
    var bw = Number(d.battery_w||0);
    var state = bw>50 ? 'in carica' : (bw<-50 ? 'in scarica' : 'neutro');
    updates.push(() => {
      var stEl = getElement('battery_state');
      var socEl = document.querySelector('.soc-display');
      var donutEl = document.querySelector('.donut-wrap.compact');
      
      if (stEl) {
        // Determina la classe del badge in base allo stato
        let badgeClass = '';
        let stateClass = '';
        
        if (bw > 50) {
          badgeClass = 'badge-on';
          stateClass = 'charging';
        } else if (bw < -50) {
          badgeClass = 'badge-off';
          stateClass = 'discharging';
        } else {
          badgeClass = 'badge-neutral';
          stateClass = 'neutral';
        }
        
        // Aggiorna il badge
        const badgeElement = document.createElement('span');
        badgeElement.className = `badge ${badgeClass}`;
        badgeElement.textContent = state;
        stEl.innerHTML = '';
        stEl.appendChild(badgeElement);
        
        // Aggiorna i colori del SOC e della ciambella
        if (socEl) {
          socEl.className = `soc-display ${stateClass}`;
        }
        
        if (donutEl) {
          donutEl.className = `donut-wrap compact ${stateClass}`;
        }
      }
    });
    
    // Grid
    updates.push(() => setText('grid_power', fmt(Number(d.grid_w||0)/1000,2) + ' kW'));
    updates.push(() => setText('grid_v', fmt(d.grid_v,1) + ' V'));
    updates.push(() => setText('grid_hz', fmt(d.grid_hz,2) + ' Hz'));
    updates.push(() => setText('grid_a', fmt(d.grid_a,1) + ' A'));
    
    // Load
    updates.push(() => setText('load_power', fmt(Number(d.load_w||0)/1000,2) + ' kW'));
    updates.push(() => setText('load_v', fmt(d.load_v,1) + ' V'));
    updates.push(() => setText('load_hz', fmt(d.load_hz,2) + ' Hz'));
    updates.push(() => setText('load_a', fmt(d.load_a,1) + ' A'));
    updates.push(() => setText('load_pf', fmt(d.load_pf,2)));
    updates.push(() => setText('load_percent', fmt(d.load_percent,0)));
    
    // SOC
    updates.push(() => setSOC(socFromV(d.battery_v, d.battery_net_wh)));
    
    // I2C / ADC
    updates.push(() => {
      try {
        if (!d || !d.i2c) {
          var st = getElement('i2c_status'); if (st) st.textContent = 'Nessun dato';
          return;
        }
        var table = document.getElementById('i2c_table');
        if (!table) return;
        // Ricostruisci la tabella in modo semplice (i dati sono pochi)
        while (table.rows.length > 0) table.deleteRow(0);
        let any = false;
        const formatVal = (val) => {
          if (val === null || val === undefined || Number.isNaN(Number(val))) return '-';
          const num = Number(val);
          const abs = Math.abs(num);
          if (abs >= 1000) return num.toFixed(1);
          if (abs >= 100) return num.toFixed(2);
          return num.toFixed(3);
        };
        Object.keys(d.i2c).forEach(function(dev){
          const vals = d.i2c[dev];
          if (!vals) return;
          // Dev header
          const hdr = table.insertRow(-1);
          const c1 = hdr.insertCell(0); const c2 = hdr.insertCell(1);
          c1.textContent = dev;
          c1.style.fontWeight = 'bold';
          c2.textContent = '';
          // Channels
          Object.keys(vals).forEach(function(ch){
            const val = vals[ch];
            const row = table.insertRow(-1);
            const k = row.insertCell(0);
            const v = row.insertCell(1);
            k.textContent = ch;
            if (val && typeof val === 'object') {
              const parts = [];
              const lowerUnit = val.unit ? String(val.unit).toLowerCase() : '';

              if (val.value != null) {
                const unit = val.unit ? (' ' + val.unit) : '';
                parts.push(formatVal(val.value) + unit);
              } else if (val.current_a != null) {
                parts.push(formatVal(val.current_a) + ' A');
              } else if (val.scaled_v != null) {
                parts.push(formatVal(val.scaled_v) + ' V');
              } else if (val.mv != null) {
                parts.push(formatVal(val.mv) + ' mV');
              }

              if (val.current_a != null && !(lowerUnit === 'a' && val.value != null)) {
                parts.push(`I: ${formatVal(val.current_a)} A`);
              }
              if (val.scaled_v != null && !(lowerUnit === 'v' && val.value != null)) {
                parts.push(`V: ${formatVal(val.scaled_v)} V`);
              }
              if (val.raw_mv != null) {
                let rawLabel = `raw ${formatVal(val.raw_mv)} mV`;
                parts.push(rawLabel);
              }
              v.textContent = parts.length ? parts.join('  |  ') : '-';
            } else {
              v.textContent = (val == null ? '-' : String(val));
            }
          });
          any = true;
        });
        if (!any) {
          const r = table.insertRow(-1);
          r.insertCell(0).textContent = 'Stato';
          r.insertCell(1).textContent = 'Nessun dato';
        }
      } catch(e){}
    });
    
    // Esegui tutti gli aggiornamenti in batch
    requestAnimationFrame(() => {
      updates.forEach(update => update());
    });
  }

  // === CHARTS LIVE ==========================================================
  var charts = { pv:null, batt:null, load:null, grid:null, hist:null };
  var i2cLiveChart = null;
  var I2C_SERIES = new Map(); // key -> dataset index
  var I2C_COLORS = ['#ef4444','#22c55e','#3b82f6','#f59e0b','#a855f7','#06b6d4','#eab308','#14b8a6','#f97316','#84cc16'];
  var i2cChart = null;
  
  // Rilevazione visibilità dei contenitori grafici per ridurre lavoro quando compressi
  function isChartContainerVisible(containerId){
    var el = document.getElementById(containerId);
    if (!el) return false;
    var style = window.getComputedStyle ? getComputedStyle(el) : null;
    var displayOk = style ? style.display !== 'none' : (el.style.display !== 'none');
    return displayOk && el.offsetParent !== null;
  }
  function anyRealtimeChartVisible(){
    return (
      isChartContainerVisible('chartContainerPV') ||
      isChartContainerVisible('chartContainerBattery') ||
      isChartContainerVisible('chartContainerGrid') ||
      isChartContainerVisible('chartContainerLoad')
    );
  }

  // Factory pattern ottimizzato per chart
  function mkLine(id, color, label){
    var el=getElement(id);
    if (!el || !window.Chart) return null;
    return new Chart(el.getContext('2d'), {
      type:'line',
      data:{ labels:[], datasets:[{ label: label+' (kW)', data:[], borderColor:color, backgroundColor:color+'33', fill:true, pointRadius:0, tension:.25 }]},
      options:{ 
        responsive:true, 
        maintainAspectRatio:false, 
        animation:false,
        plugins:{ 
          legend:{ position:'top' }, 
          decimation:{ enabled:true, algorithm:'lttb' } 
        },
        scales:{ y:{ title:{display:true,text:'kW'} } }
      }
    });
  }
  function mkI2CLine(){
    var el = getElement('chartI2C');
    if (!el || !window.Chart) return null;
    return new Chart(el.getContext('2d'), {
      type:'line',
      data:{ labels:[], datasets:[{ label:'mV', data:[], borderColor:Theme.colors.grid, backgroundColor:Theme.colors.grid+'33', fill:true, pointRadius:0, tension:.25 }]},
      options:{
        responsive:true, maintainAspectRatio:false, animation:false,
        plugins:{ legend:{ position:'top' } },
        scales:{ y:{ title:{display:true,text:'mV'} } }
      }
    });
  }
  
  function ensureCharts(){
    if (!window.Chart) return;
    if (!charts.pv)   charts.pv   = mkLine('chartPV',   Theme.colors.pv,   'PV');
    if (!charts.batt) charts.batt = mkLine('chartBatt', Theme.colors.batt, 'Batteria');
    if (!charts.load) charts.load = mkLine('chartLoad', Theme.colors.load, 'Casa');
    if (!charts.grid) charts.grid = mkLine('chartGrid', Theme.colors.grid, 'Rete');
  }
  function mkI2CLive(){
    var el = getElement('chartI2CLive');
    if (!el || !window.Chart) return null;
    return new Chart(el.getContext('2d'), {
      type:'line',
      data:{ labels:[], datasets:[] },
      options:{ 
        responsive:true, 
        maintainAspectRatio:false, 
        animation:false,
        plugins:{ legend:{ position:'top' } },
        scales:{ y:{ title:{display:true,text:'mV / A'} } }
      }
    });
  }
  function ensureI2CLive(){
    if (!window.Chart) return;
    if (!i2cLiveChart) i2cLiveChart = mkI2CLive();
  }
  
  // Chart in tempo reale disabilitati - non più necessari
  
  // Ottimizzazione setDatasetsFromRows con batch processing
  function setDatasetsFromRows(rows){
    ensureCharts();
    var labels = rows.map(function(r){ return hhmm(r.timestamp); });
    var pv  = rows.map(function(r){ return Number(r.pv_w||0)/1000; });      // Converti W in kW
    var bt  = rows.map(function(r){ return Number(r.battery_w||0)/1000; }); // Converti W in kW
    var ld  = rows.map(function(r){ return Number(r.load_w||0)/1000; });    // Converti W in kW
    var gr  = rows.map(function(r){ return Number(r.grid_w||0)/1000; });    // Converti W in kW
    
    // Batch update per tutti i chart
    const chartUpdates = [
      [charts.pv,pv],
      [charts.batt,bt],
      [charts.load,ld],
      [charts.grid,gr],
    ];
    
    chartUpdates.forEach(function(pair){
      var c=pair[0], data=pair[1];
      if (!c) return;
      c.data.labels = labels.slice(-Store.cap);
      c.data.datasets[0].data = data.slice(-Store.cap);
    });
    
    // Update batch di tutti i chart con throttling solo se qualche grafico è visibile
    if (anyRealtimeChartVisible()){
      requestAnimationFrame(updateAllCharts);
    }
  }

  // Funzione helper per aggiornare tutti i chart
  function updateAllCharts() {
    Object.values(charts).forEach(chart => {
      if (chart) chart.update('none');
    });
  }

  // Ottimizzazione pushPoint con debouncing intelligente
  function pushPoint(ts, pvW, btW, ldW, grW){
    if (!window.Chart) return;
    var lbl = hhmm(ts);
    
    function apply(chart, v){
      if (!chart) return;
      var L = chart.data.labels;
      var D = chart.data.datasets[0].data;
      if (!L.length || L[L.length-1] !== lbl) {
        L.push(lbl); D.push(Number(v||0)/1000); // Converti W in kW
        if (D.length > Store.cap){ D.shift(); L.shift(); }
      } else {
        D[D.length-1] = Number(v||0)/1000; // Converti W in kW
      }
    }
    
    apply(charts.pv,   pvW);
    apply(charts.batt, btW);
    apply(charts.load, ldW);
    apply(charts.grid, grW);
    
    // Ottimizzazione: debounced update con throttling, solo se visibile almeno un grafico
    if (anyRealtimeChartVisible()){
      if (updateTimeout) clearTimeout(updateTimeout);
      updateTimeout = setTimeout(() => {
        requestAnimationFrame(updateAllCharts);
        updateTimeout = null;
      }, 100);
    }
  }

  // === I2C LIVE (multi-series) ==============================================
  function ensureI2CLiveVisible(){
    // Initialize chart only when its container is visible to save resources
    if (isChartContainerVisible('chartContainerI2C')){
      ensureI2CLive();
    }
  }
  function updateI2CLive(){
    if (i2cLiveChart) i2cLiveChart.update('none');
  }
  function pushI2CLive(ts, i2c){
    if (!i2c) return;
    ensureI2CLiveVisible();
    if (!i2cLiveChart) return;
    var lbl = hhmm(ts);
    var L = i2cLiveChart.data.labels;
    // append label if new
    if (!L.length || L[L.length-1] !== lbl){
      L.push(lbl);
    }
    // Iterate devices/channels
    var seriesCount = 0;
    Object.keys(i2c).forEach(function(dev){
      var channels = i2c[dev];
      if (!channels) return;
      Object.keys(channels).forEach(function(ch){
        var val = channels[ch];
        var key = dev+':'+ch;
        var idx = I2C_SERIES.has(key) ? I2C_SERIES.get(key) : -1;
        if (idx < 0){
          // Create dataset
          idx = i2cLiveChart.data.datasets.length;
          var color = I2C_COLORS[idx % I2C_COLORS.length];
          i2cLiveChart.data.datasets.push({
            label: key,
            data: [],
            borderColor: color,
            backgroundColor: color+'33',
            fill: true,
            pointRadius: 0,
            tension: .25
          });
          I2C_SERIES.set(key, idx);
        }
        var ds = i2cLiveChart.data.datasets[idx];
        // normalize value: prefer current_a, else mv, else number
        var y = null;
        if (val && typeof val === 'object'){
          if (val.current_a != null) y = Number(val.current_a);
          else if (val.value != null) y = Number(val.value);
          else if (val.mv != null)   y = Number(val.mv);
        } else if (val != null){
          y = Number(val);
        }
        // push value
        if (!L.length || ds.data.length < L.length){
          ds.data.push(y);
        } else {
          ds.data[ds.data.length-1] = y;
        }
        // cap length
        if (ds.data.length > Store.cap){
          ds.data.shift();
        }
        seriesCount++;
      });
    });
    // keep labels length within cap
    if (L.length > Store.cap) L.shift();
    // Update chart only if visible
    if (isChartContainerVisible('chartContainerI2C')){
      requestAnimationFrame(updateI2CLive);
    }
  }

  // === ENERGY HISTOGRAM (kWh) ===============================================
  function mkHistoryBar(){
    var el = getElement('chartHistory');
    if(!el || !window.Chart) return null;
    return new Chart(el.getContext('2d'), {
      type:'bar',
      data:{ labels:[], datasets:[
        {label:'PV (kWh)',             data:[], backgroundColor:Theme.colors.pv},
        {label:'Batteria netta (kWh)', data:[], backgroundColor:Theme.colors.batt},
        {label:'Rete (kWh)',           data:[], backgroundColor:Theme.colors.grid},
        {label:'Casa (kWh)',           data:[], backgroundColor:Theme.colors.load},
      ]},
      options:{
        responsive:true, maintainAspectRatio:false, animation:false,
        plugins:{legend:{position:'top'}},
        scales:{ x:{}, y:{ title:{display:true,text:'kWh'}, beginAtZero:true } }
      }
    });
  }
  var histChart = null;

  // Ottimizzazione loadEnergyHistogram con cache e debouncing
  let histogramUpdateTimeout = null;
  function loadEnergyHistogram(){
    if(!histChart) histChart = mkHistoryBar();
    
    // Debouncing per evitare chiamate multiple
    if (histogramUpdateTimeout) clearTimeout(histogramUpdateTimeout);
    histogramUpdateTimeout = setTimeout(() => {
      _loadEnergyHistogram();
    }, 100);
  }
  
  function _loadEnergyHistogram(){
    var gran = getElement('histGran').value;
    var dayEl = getElement('histDate');
    var date = (dayEl && dayEl.value) ? dayEl.value : new Date().toISOString().slice(0,10);

    var url = new URL(location.origin + '/api/energy');
    url.searchParams.set('granularity', gran);
    url.searchParams.set('unit', 'kWh');
    if (gran === 'hour'){ url.searchParams.set('date', date); }
    else if (gran === 'day'){ url.searchParams.set('from', date); }
    else if (gran === 'month'){ url.searchParams.set('from', date); }

    return fetch(url.toString()).then(function(r){
      if(!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function(payload){
      var arr = payload.data || [];
      histChart.data.labels = arr.map(function(r){ return r.bucket; });
      var pv   = arr.map(function(r){ return Number(r.pv_kWh||0); });
      var batt = arr.map(function(r){ return Number(r.batt_net_kWh||0); });
      var grid = arr.map(function(r){ return Number(r.grid_kWh||0); });
      var load = arr.map(function(r){ return Number(r.load_kWh||0); });
      
      histChart.data.datasets[0].label = 'PV (kWh)';
      histChart.data.datasets[1].label = 'Batteria netta (kWh)';
      histChart.data.datasets[2].label = 'Rete (kWh)';
      histChart.data.datasets[3].label = 'Casa (kWh)';
      histChart.options.scales.y.title.text = 'kWh';
      histChart.data.datasets[0].data = pv;
      histChart.data.datasets[1].data = batt;
      histChart.data.datasets[2].data = grid;
      histChart.data.datasets[3].data = load;

      // Toggle serie
      histChart.getDatasetMeta(0).hidden = !getElement('chkPV').checked;
      histChart.getDatasetMeta(1).hidden = !getElement('chkBatt').checked;
      histChart.getDatasetMeta(2).hidden = !getElement('chkGrid').checked;
      histChart.getDatasetMeta(3).hidden = !getElement('chkLoad').checked;

      histChart.update('none');
    }).catch(function(e){ showErr('Energy histogram: '+e); });
  }
  
  (function bindHistogramControls(){
    var date = getElement('histDate');
    var btn  = getElement('histApply');
    var toggles = ['chkPV','chkBatt','chkGrid','chkLoad'].map(function(id){ return getElement(id); });
    if (date && !date.value){ date.value = new Date().toISOString().slice(0,10); }
    if (btn){ btn.addEventListener('click', loadEnergyHistogram); }
    toggles.forEach(function(el){ if(el) el.addEventListener('change', function(){ if(histChart) histChart.update('none'); }); });
  })();

  // === TOTALS TODAY (kWh) ===================================================
  function updateTotalsToday(){
    return fetch('/api/totals/today?unit=kWh').then(function(r){
      if(!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function(t){
      function n2(v){ return Number(v||0).toFixed(2).replace('.', ','); }
      function put(id,val){ var el=getElement(id); if(el) el.textContent = n2(val); }
      // API ritorna *_kWh
      put('sum_pv',       t.pv_kWh);
      put('sum_batt_net', t.batt_net_kWh);
      put('sum_grid',     t.grid_kWh);
      put('sum_load',     t.load_kWh);
    }).catch(function(){});
  }

  // === API SAFE FETCH =======================================================
  var lastOk=0, fails=0, OFFLINE=15000;
  setInterval(function(){ if(Date.now()-lastOk>OFFLINE) setOnline(false); }, 3000);
  
  // Ottimizzazione fetch con retry e timeout
  function safe(path){
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000); // 10s timeout
    
    return fetch(path, { signal: controller.signal })
      .then(function(r){
        clearTimeout(timeoutId);
        if(!r.ok) throw new Error(r.status+' '+r.statusText);
        lastOk=Date.now(); fails=0; setOnline(true); showErr('');
        return r.json();
      })
      .catch(function(e){
        clearTimeout(timeoutId);
        fails++; if(Date.now()-lastOk>OFFLINE || fails>=2) setOnline(false);
        showErr('Errore API: '+(e.message||e)); throw e;
      });
  }

  // Gestione toggle grafici nelle card
  function setupChartToggles() {
    const toggleButtons = document.querySelectorAll('.btn-toggle');
    
    toggleButtons.forEach(btn => {
      btn.addEventListener('click', function() {
        const cardType = this.getAttribute('data-card');
        const card = this.closest('.card');
        const chartContainer = card.querySelector('.chart-container');
        const isExpanded = card.classList.contains('expanded');
        
        if (isExpanded) {
          // Comprimi
          card.classList.remove('expanded');
          this.classList.remove('expanded');
          this.textContent = '📊 Espandi Grafico';
          chartContainer.style.display = 'none';
          console.log(`[DEBUG] Grafico ${cardType} compresso`);
        } else {
          // Espandi
          card.classList.add('expanded');
          this.classList.add('expanded');
          this.textContent = '📉 Comprimi Grafico';
          chartContainer.style.display = 'block';
          
          // Assicurati che il grafico sia inizializzato
          ensureCharts();
          
          console.log(`[DEBUG] Grafico ${cardType} espanso`);
        }
      });
    });
  }

  // === APP FLOW =============================================================
  function bindUI(){
    // Selettore unità rimosso - ora usiamo solo kWh
    var btn = getElement('btnRefresh');
    if (btn) btn.addEventListener('click', tick); // refresh immediato del realtime
    
    // Setup toggle grafici nelle card
    setupChartToggles();
    
    // I2C: populate device/channel selectors from latest snapshot
    (function initI2CControls(){
      const dateEl = getElement('i2cDate');
      if (dateEl && !dateEl.value){ dateEl.value = new Date().toISOString().slice(0,10); }
      const devSel = getElement('i2cDev');
      const chSel  = getElement('i2cCh');
      const metricSel = getElement('i2cMetric');
      const btn = getElement('i2cApply');
      if (btn){
        btn.addEventListener('click', loadI2CHistory);
      }
      // load latest to fill selectors
      safe('/api/i2c/latest').then(function(p){
        const i2c = p && p.i2c ? p.i2c : {};
        const devs = Object.keys(i2c);
        if (devSel){
          devSel.innerHTML = '';
          devs.forEach(function(d){
            const o = document.createElement('option'); o.value=d; o.textContent=d; devSel.appendChild(o);
          });
        }
        if (devs.length){
          const first = devs[0];
          const channels = i2c[first] ? Object.keys(i2c[first]) : [];
          if (chSel){
            chSel.innerHTML = '';
            channels.forEach(function(c){
              const o = document.createElement('option'); o.value=c; o.textContent=c; chSel.appendChild(o);
            });
          }
        }
      }).catch(function(){});
    })();
  }

// --- Install prompt (PWA) - Ottimizzato ---
(function setupInstallPrompt(){
  let deferredPrompt = null;
  const btn = getElement('btnInstall');

  // Funzione per controllare se l'app è già installata
  function checkIfAppInstalled() {
    console.log('[DEBUG] Controllo se l\'app è installata...');
    
    // Controlla se l'app è in modalità standalone (installata)
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches;
    const isNavigatorStandalone = window.navigator.standalone;
    const hasAndroidAppReferrer = document.referrer.includes('android-app://');
    
    console.log('[DEBUG] isStandalone:', isStandalone);
    console.log('[DEBUG] isNavigatorStandalone:', isNavigatorStandalone);
    console.log('[DEBUG] hasAndroidAppReferrer:', hasAndroidAppReferrer);
    
    // Controlla se è su iOS e se è stata aggiunta alla home
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const isIOSStandalone = isIOS && window.navigator.standalone;
    
    console.log('[DEBUG] isIOS:', isIOS);
    console.log('[DEBUG] isIOSStandalone:', isIOSStandalone);
    
    // Controlla se l'app è stata installata tramite localStorage (fallback)
    const wasInstalled = localStorage.getItem('app_installed') === 'true';
    
    console.log('[DEBUG] wasInstalled (localStorage):', wasInstalled);
    
    // Controlli aggiuntivi per PWA
    const isInPWA = window.location.search.includes('source=pwa') || 
                    window.location.search.includes('utm_source=pwa') ||
                    window.location.hash.includes('pwa');
    
    console.log('[DEBUG] isInPWA:', isInPWA);
    
    // Se l'app è installata, restituisci true
    if (isStandalone || isNavigatorStandalone || hasAndroidAppReferrer || isIOSStandalone || wasInstalled || isInPWA) {
      console.log('[DEBUG] App installata rilevata!');
      return true;
    }
    
    console.log('[DEBUG] App NON installata.');
    return false;
  }

  // Funzione per mostrare/nascondere il pulsante
  function updateInstallButton() {
    if (!btn) return;
    
    const isInstalled = checkIfAppInstalled();
    
    // Se l'app è installata, nascondi SEMPRE il pulsante
    if (isInstalled) {
      btn.hidden = true;
      btn.style.display = 'none';
      btn.style.visibility = 'hidden';
      btn.classList.add('hidden');
      console.log('[DEBUG] Pulsante installazione: NASCOSTO (app installata)');
      return;
    }
    
    // Se l'app non è installata, mostra il pulsante solo se c'è un prompt disponibile
    if (deferredPrompt) {
      btn.hidden = false;
      btn.style.display = 'inline-block';
      btn.style.visibility = 'visible';
      btn.classList.remove('hidden');
      console.log('[DEBUG] Pulsante installazione: VISIBILE (prompt disponibile)');
    } else {
      // Per browser che non supportano beforeinstallprompt (come Samsung Internet)
      const isSamsung = /SamsungBrowser/i.test(navigator.userAgent);
      if (isSamsung) {
        btn.hidden = false;
        btn.style.display = 'inline-block';
        btn.style.visibility = 'visible';
        btn.classList.remove('hidden');
        console.log('[DEBUG] Pulsante installazione: VISIBILE (Samsung Internet)');
      } else {
        // Browser standard senza prompt - nascondi il pulsante
        btn.hidden = true;
        btn.style.display = 'none';
        btn.style.visibility = 'hidden';
        btn.classList.add('hidden');
        console.log('[DEBUG] Pulsante installazione: NASCOSTO (browser standard senza prompt)');
      }
    }
  }

  // Controlla subito se l'app è installata
  console.log('[DEBUG] Controllo iniziale installazione...');
  checkIfAppInstalled();

  // Chrome/Edge: intercetta evento e mostra il bottone
  window.addEventListener('beforeinstallprompt', (e) => {
    console.log('[DEBUG] Evento beforeinstallprompt ricevuto');
    e.preventDefault();
    deferredPrompt = e;
    updateInstallButton();
  });

  // Click ? mostra il prompt nativo
  if (btn) btn.addEventListener('click', async () => {
    console.log('[DEBUG] Click sul pulsante installazione');
    
    if (!deferredPrompt) {
      // Fallback Samsung Internet (nessun evento)
      const isSamsung = /SamsungBrowser/i.test(navigator.userAgent);
      if (isSamsung) {
        // Mostra istruzioni specifiche per Samsung Internet
        const instructions = 'Per installare l\'app su Samsung Internet:\n\n' +
                           '1. Tocca il menu (tre puntini)\n' +
                           '2. Seleziona "Aggiungi pagina a"\n' +
                           '3. Scegli "Schermata app"\n' +
                           '4. Conferma l\'installazione\n\n' +
                           'Dopo l\'installazione, il pulsante scomparirà automaticamente.';
        
        if (confirm(instructions)) {
          // Dopo che l'utente conferma di aver seguito le istruzioni,
          // nascondi il pulsante e marca l'app come installata
          console.log('[DEBUG] Utente conferma installazione Samsung Internet');
          localStorage.setItem('app_installed', 'true');
          updateInstallButton();
          
          // Mostra messaggio di successo
          setTimeout(() => {
            alert('Perfetto! L\'app è stata installata. Il pulsante è stato nascosto.');
          }, 1000);
        }
      } else {
        alert('Per installare l\'app, usa il menu del browser o aggiungi questa pagina alla home screen.');
      }
      return;
    }
    
    deferredPrompt.prompt();
    try { 
      const choiceResult = await deferredPrompt.userChoice;
      
      if (choiceResult.outcome === 'accepted') {
        localStorage.setItem('app_installed', 'true');
      }
    } finally {
      deferredPrompt = null;
      updateInstallButton();
    }
  });

  // Se l'app è installata, nascondi
  window.addEventListener('appinstalled', () => { 
    localStorage.setItem('app_installed', 'true');
    updateInstallButton();
  });

  // Controlla anche quando cambia la modalità display
  window.matchMedia('(display-mode: standalone)').addEventListener('change', (e) => {
    if (e.matches) {
      // L'app è passata in modalità standalone
      localStorage.setItem('app_installed', 'true');
      updateInstallButton();
    }
  });

  // Controlla periodicamente se l'app è stata installata (fallback)
  setInterval(updateInstallButton, 5000); // Controlla ogni 5 secondi

  // Controllo finale iniziale
  setTimeout(updateInstallButton, 1000);
})();

  function loadConfig(){
    return safe('/api/config').then(function(cfg){
      if (cfg && cfg.battery && cfg.battery.soc){
        // Aggiorna metodo SOC e parametri correlati
        if (cfg.battery.soc.method) { 
          Store.socMethod = cfg.battery.soc.method; 
          localStorage.setItem('soc_method', Store.socMethod); 
        }
        
        if (Store.socMethod === 'voltage_based') {
          // Metodo basato su tensione: usa vmax_v e vmin_v
          if (cfg.battery.soc.vmax_v != null){ 
            Store.vmax = Number(cfg.battery.soc.vmax_v); 
            localStorage.setItem('soc_vmax_v', String(Store.vmax)); 
          }
          if (cfg.battery.soc.vmin_v != null){ 
            Store.vmin = Number(cfg.battery.soc.vmin_v); 
            localStorage.setItem('soc_vmin_v', String(Store.vmin)); 
          }
        } else         if (Store.socMethod === 'energy_balance') {
          // Metodo bilancio energetico: usa reset_voltage
          if (cfg.battery.soc.reset_voltage != null){ 
            Store.resetVoltage = Number(cfg.battery.soc.reset_voltage); 
            localStorage.setItem('soc_reset_voltage', String(Store.resetVoltage)); 
          }
        }
        
        // Aggiorna capacità batteria per calcoli SOC
        if (cfg.battery.nominal_voltage && cfg.battery.nominal_ah) {
          const capacityWh = cfg.battery.nominal_voltage * cfg.battery.nominal_ah;
          Store.batteryCapacityWh = capacityWh;
          localStorage.setItem('battery_capacity_wh', String(capacityWh));
        }
        

      }
      // Unità potenza ora fissa a kWh
    });
  }

  function preload(){
    ensureCharts();
    return safe('/api/history?fill=0').then(function(rows){
      setDatasetsFromRows(rows||[]);
      // carica subito istogramma
      return loadEnergyHistogram();
    }).catch(function(){});
  }

  function tick(){
    return safe('/api/inverter').then(function(d){
      updateBoxes(d);
      setLastUpdate(hhmm(d.timestamp || new Date().toISOString()));
      pushPoint(d.timestamp || new Date().toISOString(),
        Number(d.pv_w||0), Number(d.battery_w||0), Number(d.load_w||0), Number(d.grid_w||0));
      // I2C live update
      pushI2CLive(d.timestamp || new Date().toISOString(), d.i2c || null);
    }).catch(function(){});
  }

  // Ottimizzazione: scheduling intelligente invece di setInterval fisso
  function scheduleTick() {
    if (tickTimeout) clearTimeout(tickTimeout);
    tickTimeout = setTimeout(() => {
      tick().finally(() => {
        tickTimeout = null;
        scheduleTick(); // Programma il prossimo
      });
    }, 5000);
  }

  function start(){
    bindUI();
    loadConfig()
      .then(preload)
      .then(function(){ return Promise.all([tick(), updateTotalsToday()]); })
      .then(function(){
        scheduleTick(); // Inizia il ciclo ottimizzato
        setInterval(updateTotalsToday, 30000);
      })
      .catch(function(error){
        console.error('Errore durante l\'avvio:', error);
      });
  }

  // === I2C HISTORY ==========================================================
  function ensureI2CChart(){
    if (!window.Chart) return;
    if (!i2cChart) i2cChart = mkI2CLine();
  }
  function loadI2CHistory(){
    const dev = getElement('i2cDev') ? getElement('i2cDev').value : '';
    const ch  = getElement('i2cCh') ? getElement('i2cCh').value : '';
    const metric = getElement('i2cMetric') ? getElement('i2cMetric').value : 'mv';
    const date = getElement('i2cDate') && getElement('i2cDate').value ? getElement('i2cDate').value : new Date().toISOString().slice(0,10);
    if (!dev || !ch) return Promise.resolve();
    ensureI2CChart();
    var url = new URL(location.origin + '/api/i2c/history');
    url.searchParams.set('device', dev);
    url.searchParams.set('channel', ch);
    url.searchParams.set('metric', metric);
    url.searchParams.set('date', date);
    return fetch(url.toString()).then(function(r){
      if(!r.ok) throw new Error(r.status);
      return r.json();
    }).then(function(payload){
      if (!i2cChart) return;
      const arr = (payload && payload.data) ? payload.data : [];
      const labels = arr.map(function(r){ return hhmm(r.timestamp); });
      const vals = arr.map(function(r){ return Number(r.value||0); });
      i2cChart.data.labels = labels;
      i2cChart.data.datasets[0].data = vals;
      // Axis/unit label
      const ylab = (metric === 'current_a') ? 'A' : 'mV';
      i2cChart.data.datasets[0].label = (metric === 'current_a') ? 'A' : 'mV';
      i2cChart.options.scales.y.title.text = ylab;
      i2cChart.update('none');
    }).catch(function(e){
      console.warn('I2C history error:', e);
    });
  }

  if (document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', start); }
  else { start(); }
})();

