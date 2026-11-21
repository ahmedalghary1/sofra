(() => {
  const english = document.documentElement.lang.startsWith('en');
  const controlIcons = document.getElementById('control-icons').content;
  function useCurrentCsrfCookie(form) {
    const token = document.cookie.match(/(?:^|;\s*)csrftoken=([A-Za-z0-9]{32})(?:;|$)/);
    if (!token) return false;
    form.elements.csrfmiddlewaretoken.value = token[1];
    return true;
  }
  const accountForm = document.querySelector('[data-account-form]');
  accountForm?.addEventListener('submit', () => useCurrentCsrfCookie(accountForm));
  const loginForm = document.querySelector('[data-login-form]');
  if (loginForm) {
    const error = document.querySelector('[data-login-error]');
    const separateWindow = document.querySelector('[data-login-window]');
    separateWindow.hidden = window.self === window.top;
    let refreshing = false;
    loginForm.addEventListener('submit', async event => {
      if (!refreshing && useCurrentCsrfCookie(loginForm)) return;
      if (!window.fetch || !window.AbortSignal?.timeout) return;
      event.preventDefault();
      if (refreshing) return;
      refreshing = true;
      error.hidden = true;
      const button = loginForm.querySelector('button[type="submit"]');
      button.disabled = true;
      try {
        // Another tab can rotate the CSRF cookie after this form was opened.
        const response = await fetch(window.location.href, {
          credentials: 'same-origin', mode: 'same-origin', cache: 'no-store',
          signal: AbortSignal.timeout(10000),
        });
        if (!response.ok) throw new Error('Could not refresh the login form');
        const freshPage = new DOMParser().parseFromString(await response.text(), 'text/html');
        const token = freshPage.querySelector('[data-login-form] input[name="csrfmiddlewaretoken"]');
        if (!token?.value) throw new Error('Missing form token');
        loginForm.elements.csrfmiddlewaretoken.value = token.value;
        HTMLFormElement.prototype.submit.call(loginForm);
      } catch {
        error.textContent = english
          ? 'Could not refresh the sign-in form. Check your connection and try again. If this page is in a preview, open it in a separate window.'
          : 'تعذّر تحديث نموذج الدخول. تحقق من الاتصال وأعد المحاولة. إذا كنت تستخدم المعاينة، افتح الصفحة في نافذة مستقلة.';
        error.hidden = false;
        separateWindow.hidden = false;
        button.disabled = false;
        refreshing = false;
      }
    });
  }
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('menuToggle');
  const overlay = document.getElementById('sidebarOverlay');
  const main = document.querySelector('.main-wrap');
  const closeButton = document.getElementById('closeNav');
  if (sidebar && toggle) {
    const narrow = matchMedia('(max-width: 780px)');
    function setOpen(open, restoreFocus = true) {
      sidebar.classList.toggle('open', open);
      overlay.classList.toggle('show', open);
      toggle.setAttribute('aria-expanded', String(open));
      main.inert = open;
      document.body.classList.toggle('nav-open', open);
      sidebar.inert = !open && narrow.matches;
      if (open) {
        sidebar.setAttribute('role', 'dialog');
        sidebar.setAttribute('aria-modal', 'true');
        sidebar.setAttribute('aria-label', toggle.getAttribute('aria-label'));
        closeButton.focus();
      } else {
        sidebar.removeAttribute('role');
        sidebar.removeAttribute('aria-modal');
        if (restoreFocus) toggle.focus();
      }
    }
    toggle.addEventListener('click', () => setOpen(true));
    closeButton.addEventListener('click', () => setOpen(false));
    overlay.addEventListener('click', () => setOpen(false));
    document.addEventListener('keydown', event => {
      if (!sidebar.classList.contains('open')) return;
      if (event.key === 'Escape') setOpen(false);
      if (event.key !== 'Tab') return;
      const controls = [...sidebar.querySelectorAll('a[href],button,select,summary')]
        .filter(element => !element.disabled && element.getClientRects().length);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    narrow.addEventListener('change', () => setOpen(false, false));
    setOpen(false, false);
  }
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
  document.querySelectorAll('.toast-stack .toast').forEach(notice => {
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.className = 'notice-dismiss';
    dismiss.setAttribute('aria-label', english ? 'Dismiss message' : 'إغلاق الرسالة');
    dismiss.append(controlIcons.querySelector('.ui-icon-x').cloneNode(true));
    dismiss.addEventListener('click', () => notice.remove());
    notice.append(dismiss);
  });
  document.querySelectorAll('input[type="password"]').forEach((input, index) => {
    const wrapper = document.createElement('span');
    const button = document.createElement('button');
    input.id ||= 'account-password-' + index;
    wrapper.className = 'ops-password';
    const field = input.closest('label') || input;
    field.after(wrapper);
    wrapper.append(field, button);
    button.type = 'button';
    button.setAttribute('aria-controls', input.id);
    button.setAttribute('aria-pressed', 'false');
    const label = english ? 'Show password' : 'إظهار كلمة المرور';
    button.setAttribute('aria-label', label);
    button.title = label;
    button.append(controlIcons.querySelector('.ui-icon-eye').cloneNode(true));
    button.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-pressed', String(show));
      button.replaceChildren(controlIcons.querySelector(show ? '.ui-icon-eye-off' : '.ui-icon-eye').cloneNode(true));
      button.title = show ? (english ? 'Hide password' : 'إخفاء كلمة المرور') : label;
    });
  });
  const filters = document.querySelector('[data-menu-filters]');
  if (filters) {
    const search = filters.querySelector('[data-menu-search]');
    const state = filters.querySelector('[data-menu-state]');
    const count = filters.querySelector('output');
    const empty = document.querySelector('[data-menu-empty]');
    const categories = document.querySelectorAll('[data-menu-category]');
    const normalize = text => text.normalize('NFKD').replace(/[\u0640\p{M}]/gu, '').toLocaleLowerCase().trim();
    function filterMenu() {
      const query = normalize(search.value);
      let shown = 0;
      categories.forEach(category => {
        let matches = 0;
        category.querySelectorAll('[data-menu-name]').forEach(entry => {
          const hasText = normalize(category.dataset.menuCategory + ' ' + entry.dataset.menuName).includes(query);
          entry.hidden = !hasText || Boolean(state.value && state.value !== entry.dataset.menuAvailable);
          if (!entry.hidden) matches++;
        });
        category.hidden = Boolean(query || state.value) && !matches;
        shown += matches;
      });
      count.textContent = (english ? 'Items shown · ' : 'الأصناف المعروضة · ') + shown;
      empty.hidden = shown !== 0;
    }
    filters.hidden = false;
    search.addEventListener('input', filterMenu);
    state.addEventListener('change', filterMenu);
    empty.querySelector('button').addEventListener('click', () => {
      search.value = state.value = '';
      filterMenu();
      search.focus();
    });
    filterMenu();
  }
  document.querySelectorAll('.table-scroll').forEach(container => {
    if (container.scrollWidth > container.clientWidth) container.tabIndex = 0;
  });
})();
