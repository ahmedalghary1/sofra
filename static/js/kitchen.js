(() => {
  const state = document.getElementById('kitchenConnection');
  const initial = window.SAFRA_KITCHEN.signature;
  const grid = document.getElementById('kitchenGrid');
  const statusFilter = document.getElementById('kitchenStatus');
  const tableFilter = document.getElementById('kitchenTable');
  const sortFilter = document.getElementById('kitchenSort');
  const cards = [...grid.querySelectorAll('.kitchen-ticket')];
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
    document.getElementById('kitchenVisibleCount').textContent = visible + ' طلب ظاهر';
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
      if (data.signature !== initial) { location.reload(); return; }
      state.textContent = 'متصل · الطلبات محدثة';
      state.classList.remove('offline');
    } catch (_) {
      state.textContent = 'الاتصال منقطع · ستُحدّث الطلبات تلقائيًا عند عودته';
      state.classList.add('offline');
    } finally { checking = false; }
  }
  setInterval(refresh, 10000);
  window.addEventListener('online', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
