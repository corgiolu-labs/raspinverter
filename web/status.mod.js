'use strict';
// Poller minimale stato connessione (connBadge / lastUpdate / btnRefresh).
// Usato dalle pagine senza app.mod.js (Analisi, Diagnostica, Impostazioni).
(function(){
  var b=document.getElementById('connBadge'),u=document.getElementById('lastUpdate'),r=document.getElementById('btnRefresh');
  var fails=0;
  function t(){
    fetch('/api/inverter').then(function(x){if(!x.ok)throw new Error(x.status);return x.json();}).then(function(d){
      fails=0;
      if(b){b.textContent='ON LINE';b.classList.add('badge-on');b.classList.remove('badge-off');}
      if(u&&d&&d.timestamp){var p=(d.timestamp||'').split(' ')[1]||'';u.textContent=p.slice(0,5);}
    }).catch(function(){
      if(++fails<3)return;
      if(b){b.textContent='OFF LINE';b.classList.add('badge-off');b.classList.remove('badge-on');}
    });
  }
  if(r)r.addEventListener('click',t);
  document.addEventListener('visibilitychange',function(){if(!document.hidden){fails=0;t();}});
  t();setInterval(t,10000);
})();
