(() => {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/public-worker.js').catch(() => {});
    });
  }
  const menu = document.querySelector('.mk-mega');
  const drawer = document.querySelector('.mk-drawer');
  const opener = document.querySelector('[data-nav-open]');
  opener.addEventListener('click', () => {
    drawer.showModal();
    opener.setAttribute('aria-expanded', 'true');
    document.body.classList.add('mk-lock');
  });
  drawer.querySelector('[data-nav-close]').addEventListener('click', () => drawer.close());
  drawer.addEventListener('click', event => {
    if (event.target === drawer) {
      const box = drawer.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY > box.bottom) drawer.close();
    }
    if (event.target.closest('a')) drawer.close();
  });
  drawer.addEventListener('close', () => {
    document.body.classList.remove('mk-lock');
    opener.setAttribute('aria-expanded', 'false');
    opener.focus();
  });
  document.addEventListener('click', event => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu.open) {
      menu.open = false;
      menu.querySelector('summary').focus();
    }
  });
  menu.addEventListener('focusout', event => {
    if (event.relatedTarget && !menu.contains(event.relatedTarget)) menu.open = false;
  });
  document.querySelectorAll('[data-contact]').forEach(form => {
    const button = form.querySelector('button[type=submit]');
    form.addEventListener('submit', () => {
      button.disabled = true;
      button.textContent = button.dataset.pending;
      form.setAttribute('aria-busy', 'true');
    });
    window.addEventListener('pageshow', () => {
      button.disabled = false;
      button.textContent = button.dataset.label;
      form.removeAttribute('aria-busy');
    });
  });
  const teaser = document.querySelector('[data-plan-prices]');
  if (teaser && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(async ([entry]) => {
      if (!entry.isIntersecting) return;
      observer.disconnect();
      try {
        const response = await fetch(teaser.dataset.planPrices);
        if (!response.ok) return;
        const {configured} = await response.json();
        for (const plan of configured) {
          const card = teaser.querySelector('[data-tier="' + CSS.escape(plan.slug) + '"]');
          if (!card) continue;
          card.querySelector('[data-tier-label]').textContent = teaser.dataset.configuredLabel;
          card.querySelector('[data-tier-price]').textContent = plan.price;
          card.querySelector('[data-tier-focus]').textContent = plan.focus;
          for (const value of card.querySelectorAll('[data-tier-limit]')) {
            value.textContent = plan.limits[value.dataset.tierLimit];
          }
          for (const value of card.querySelectorAll('[data-tier-role]')) {
            value.textContent = plan.staff[value.dataset.tierRole];
          }
          card.querySelector('.mk-tier-features').replaceChildren(...plan.highlights.slice(0, 2).map(text => {
            const item = document.createElement('li');
            item.textContent = text;
            return item;
          }));
        }
      } catch {
        // The cards retain their labelled proposed terms if the request fails.
      }
    }, {rootMargin: '250px'});
    observer.observe(teaser);
  }
})();
