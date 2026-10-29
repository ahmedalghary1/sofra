(() => {
  const state = document.getElementById('kitchenConnection');
  const initial = window.SAFRA_KITCHEN.signature;
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
