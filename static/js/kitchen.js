(() => {
  const english = document.documentElement.lang.startsWith('en');
  const full = document.getElementById('kitchenFullscreen');
  const sound = document.getElementById('kitchenSound');
  let audioContext;
  let soundOn = false;
  if (document.fullscreenEnabled) {
    full.hidden = false;
    full.addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await document.documentElement.requestFullscreen();
      } catch (_) { full.hidden = true; }
    });
    document.addEventListener('fullscreenchange', () => {
      full.setAttribute('aria-pressed', String(Boolean(document.fullscreenElement)));
    });
  }
  if (window.AudioContext) {
    sound.hidden = false;
    sound.addEventListener('click', async () => {
      try {
        if (!audioContext) audioContext = new AudioContext();
        await audioContext.resume();
        soundOn = !soundOn;
        sound.setAttribute('aria-pressed', String(soundOn));
      } catch (_) { sound.hidden = true; }
    });
  }
  function signalNewTickets() {
    if (!soundOn || audioContext?.state !== 'running') return;
    const tone = audioContext.createOscillator();
    const volume = audioContext.createGain();
    volume.gain.setValueAtTime(0.07, audioContext.currentTime);
    volume.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.16);
    tone.frequency.value = 640;
    tone.connect(volume).connect(audioContext.destination);
    tone.start();
    tone.stop(audioContext.currentTime + 0.18);
    tone.addEventListener('ended', () => { tone.disconnect(); volume.disconnect(); }, {once:true});
  }
  const state = document.getElementById('kitchenConnection');
  let signature = window.SAFRA_KITCHEN.signature;
  const grid = document.getElementById('kitchenGrid');
  const statusFilter = document.getElementById('kitchenStatus');
  const tableFilter = document.getElementById('kitchenTable');
  const sortFilter = document.getElementById('kitchenSort');
  let cards = [...grid.querySelectorAll('.kitchen-ticket')];
  const storageKey = 'safra-kitchen-' + window.SAFRA_KITCHEN.branch;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}');
    for (const [select, value] of [[statusFilter, saved.status], [tableFilter, saved.table], [sortFilter, saved.sort]]) {
      if ([...select.options].some(option => option.value === value)) select.value = value;
    }
  } catch (_) {}
  function applyFilters() {
    const direction = sortFilter.value === 'oldest' ? 1 : -1;
    cards.sort((a, b) => direction * (Number(a.dataset.time) - Number(b.dataset.time)));
    let visible = 0;
    cards.forEach(card => {
      card.hidden = (statusFilter.value !== 'all' && card.dataset.status !== statusFilter.value) ||
                    (tableFilter.value !== 'all' && card.dataset.table !== tableFilter.value);
      if (!card.hidden) visible++;
      grid.append(card);
    });
    document.getElementById('kitchenVisibleCount').textContent = visible + (english ? ' tickets shown' : ' طلب ظاهر');
    document.getElementById('kitchenFilteredEmpty').hidden = !cards.length || visible > 0;
    try { sessionStorage.setItem(storageKey, JSON.stringify({status:statusFilter.value, table:tableFilter.value, sort:sortFilter.value})); } catch (_) {}
  }
  [statusFilter, tableFilter, sortFilter].forEach(select => select.addEventListener('change', applyFilters));
  applyFilters();
  let checking = false;
  async function refresh() {
    if (checking || document.hidden) return;
    checking = true;
    try {
      const response = await fetch(window.SAFRA_KITCHEN.feedUrl, {cache:'no-store', credentials:'same-origin'});
      if (!response.ok) throw new Error('feed unavailable');
      const data = await response.json();
      if (data.signature !== signature) {
        const response = await fetch(location.href, {cache: 'no-store', credentials: 'same-origin'});
        if (!response.ok) throw new Error('ticket refresh unavailable');
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        const freshGrid = page.getElementById('kitchenGrid');
        if (!freshGrid || freshGrid.dataset.branch !== grid.dataset.branch) {
          location.reload();
          return;
        }
        const previous = new Set(cards.map(ticket => ticket.dataset.order));
        const focusedOrder = document.activeElement.closest('.kitchen-ticket')?.dataset.order;
        grid.replaceChildren(...freshGrid.children);
        signature = freshGrid.dataset.signature;
        for (const id of ['kitchenTotal', 'kitchenRouting']) {
          document.getElementById(id).replaceChildren(...page.getElementById(id).childNodes);
        }
        cards = [...grid.querySelectorAll('.kitchen-ticket')];
        applyFilters();
        if (focusedOrder) {
          const ticket = cards.find(ticket => ticket.dataset.order === focusedOrder && !ticket.hidden);
          (ticket?.querySelector('button') || statusFilter).focus({preventScroll:true});
        }
        if (cards.some(ticket => !previous.has(ticket.dataset.order))) signalNewTickets();
      }
      state.textContent = english ? 'Connected · tickets up to date' : 'متصل · الطلبات محدثة';
      state.classList.remove('offline');
    } catch (_) {
      state.textContent = english ? 'Offline · reconnecting automatically' : 'الاتصال منقطع · ستُحدّث الطلبات تلقائيًا عند عودته';
      state.classList.add('offline');
    } finally { checking = false; }
  }
  setInterval(refresh, 10000);
  window.addEventListener('online', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
