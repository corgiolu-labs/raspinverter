'use strict';
// Poller minimale stato connessione (connBadge / lastUpdate / btnRefresh).
// Usato dalle pagine senza app.mod.js (Analisi, Diagnostica, Impostazioni).
(function(){
  var b=document.getElementById('connBadge'),u=document.getElementById('lastUpdate'),r=document.getElementById('btnRefresh');
  function t(){
    fetch('/api/inverter').then(function(x){return x.ok?x.json():null;}).then(function(d){
      if(b){b.textContent='ON LINE';b.classList.add('badge-on');b.classList.remove('badge-off');}
      if(u&&d&&d.timestamp){var p=(d.timestamp||'').split(' ')[1]||'';u.textContent=p.slice(0,5);}
    }).catch(function(){
      if(b){b.textContent='OFF LINE';b.classList.add('badge-off');b.classList.remove('badge-on');}
    });
  }
  if(r)r.addEventListener('click',t);
  t();setInterval(t,10000);
})();
