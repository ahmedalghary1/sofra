'use strict';

(() => {
  const english = document.documentElement.lang.startsWith('en');
  const t = (ar, en) => english ? en : ar;
  const formatNumber = new Intl.NumberFormat(english ? 'en-EG' : 'ar-EG-u-nu-latn', { maximumFractionDigits: 2 });
  const money = value => formatNumber.format(value) + ' ' + t('ج.م', 'EGP');
  const text = (root, selector, value) => {
    root.querySelectorAll(selector).forEach(element => { element.textContent = value; });
  };
  const switches = new WeakMap();

  document.querySelectorAll('[data-tabset]').forEach(root => {
    const list = root.querySelector('[data-tabs]');
    const tabs = Array.from(list.querySelectorAll('a'));
    const panels = tabs.map(tab => document.getElementById(tab.hash.slice(1)));
    if (panels.some(panel => !panel)) return;
    list.setAttribute('role', 'tablist');
    tabs.forEach((tab, index) => {
      const panel = panels[index];
      tab.id = panel.id + '-tab';
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', panel.id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
      panel.tabIndex = 0;
    });
    function select(index, focus = false) {
      tabs.forEach((tab, i) => {
        const active = i === index;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
        panels[i].hidden = !active;
      });
      if (focus) tabs[index].focus();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => {
        event.preventDefault();
        select(index);
      });
      tab.addEventListener('keydown', event => {
        const rtl = getComputedStyle(list).direction === 'rtl';
        const step = rtl ? -1 : 1;
        const next = { ArrowRight: index + step, ArrowLeft: index - step, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        select((next + tabs.length) % tabs.length, true);
      });
    });
    switches.set(root, (id, focus = true) => {
      const index = panels.findIndex(panel => panel.id === id);
      if (index >= 0) select(index, focus);
    });
    const fragment = panels.findIndex(panel => '#' + panel.id === window.location.hash);
    select(fragment < 0 ? 0 : fragment);
  });

  const stageNames = {
    cart: t('لم يُرسل', 'Unsubmitted'), new: t('جديد', 'New'),
    accepted: t('تم الاستلام', 'Accepted'), preparing: t('قيد التحضير', 'Preparing'),
    ready: t('جاهز', 'Ready'), served: t('تم التقديم', 'Served')
  };
  const callNames = {
    idle: t('لا يوجد', 'None'), pending: t('بانتظار الاستجابة', 'Pending'),
    done: t('تمت التلبية', 'Completed')
  };
  function newTable() {
    return { cart: [], stage: 'cart', call: 'idle', bill: 'idle', events: [] };
  }
  document.querySelectorAll('[data-sandbox]').forEach(lab => {
    let table = newTable();
    const milk = lab.querySelector('[data-milk]');
    const tabs = switches.get(lab.querySelector('[data-tabset]'));
    const unitPrice = option => option === 'oat' ? 95 : 80;
    const milkName = option => option === 'oat' ? t('لبن شوفان', 'Oat milk') : t('لبن كامل الدسم', 'Whole milk');
    const emptyNotice = t('أضف مشروبًا من المنيو لبدء التجربة.', 'Add a drink from the menu to begin.');
    const announce = message => text(lab, '[data-notice]', message);

    function displayLines(list, items, empty) {
      const rows = (items.length ? items : [empty]).map(label => {
        const row = document.createElement('li');
        row.textContent = label;
        return row;
      });
      list.replaceChildren(...rows);
    }
    function render() {
      const submitted = table.stage !== 'cart';
      const sum = table.cart.reduce((total, option) => total + unitPrice(option), 0);
      const groups = ['regular', 'oat'].flatMap(option => {
        const count = table.cart.filter(item => item === option).length;
        return count ? [count + ' × ' + t('فلات وايت', 'Flat white') + ' · ' + milkName(option) + ' · ' + money(count * unitPrice(option))] : [];
      });
      displayLines(lab.querySelector('[data-lines]'), groups, t('السلة فارغة.', 'Your cart is empty.'));
      displayLines(lab.querySelector('[data-ticket-lines]'), submitted ? groups : [], t('تظهر الأصناف بعد تأكيد الضيف.', 'Items appear after the guest confirms.'));
      displayLines(lab.querySelector('[data-events]'), table.events, t('بانتظار أول إجراء على الطاولة.', 'Waiting for the first table event.'));
      text(lab, '[data-quantity]', table.cart.length);
      text(lab, '[data-total]', money(sum));
      text(lab, '[data-confirmed]', submitted ? 1 : 0);
      text(lab, '[data-confirmed-total]', money(submitted ? sum : 0));
      text(lab, '[data-stage]', stageNames[table.stage]);
      text(lab, '[data-call]', callNames[table.call]);
      text(lab, '[data-bill]', callNames[table.bill]);
      text(lab, '[data-order-title]', submitted ? t('طلب #104 · طاولة 4', 'Order #104 · Table 4') : t('لا توجد تذاكر بعد.', 'No tickets yet.'));
      // Keep the submit button usable with an empty cart so validation explains the next action.
      const enabled = {
        add: !submitted && table.cart.length < 30, remove: !submitted && table.cart.length > 0,
        submit: !submitted, accept: table.stage === 'new', prepare: table.stage === 'accepted',
        ready: table.stage === 'preparing', serve: table.stage === 'ready', reset: true,
        call: table.call !== 'pending', ackCall: table.call === 'pending',
        bill: submitted && table.bill === 'idle', ackBill: table.bill === 'pending'
      };
      lab.querySelectorAll('[data-act]').forEach(button => {
        button.disabled = !enabled[button.dataset.act];
      });
      milk.disabled = !enabled.add;
    }
    const transitions = {
      accept: ['new', 'accepted'], prepare: ['accepted', 'preparing'],
      ready: ['preparing', 'ready'], serve: ['ready', 'served']
    };
    lab.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      if (button.dataset.go) {
        tabs(button.dataset.go);
        return;
      }
      const action = button.dataset.act;
      let message;
      if (action === 'reset') {
        table = newTable();
        milk.value = 'regular';
        render();
        tabs('sandbox-menu');
        announce(emptyNotice);
        return;
      }
      if (action === 'add' && table.stage === 'cart' && table.cart.length < 30) {
        table.cart.push(milk.value);
        message = t('أُضيف مشروب للسلة.', 'One drink added to the cart.');
      } else if (action === 'remove' && table.stage === 'cart' && table.cart.length) {
        table.cart.pop();
        message = t('حُذف آخر مشروب.', 'Last drink removed.');
      } else if (action === 'submit' && table.stage === 'cart') {
        if (!table.cart.length) {
          announce(t('السلة فارغة. أضف مشروبًا من المنيو أولًا.', 'Your cart is empty. Add a drink from the menu first.'));
          return;
        }
        table.stage = 'new';
        message = t('تأكد الطلب التجريبي #104 ووصل للمطبخ.', 'Demo order #104 confirmed and delivered to the kitchen.');
      } else if (transitions[action] && transitions[action][0] === table.stage) {
        table.stage = transitions[action][1];
        message = t('طلب #104 · ', 'Order #104 · ') + stageNames[table.stage];
      } else if (action === 'call' || action === 'bill') {
        table[action] = 'pending';
        message = action === 'call' ? t('وصل النداء للصالة.', 'Waiter call sent to the floor.') : t('وصل طلب الحساب للصالة.', 'Bill request sent to the floor.');
      } else if (action === 'ackCall' || action === 'ackBill') {
        table[action === 'ackCall' ? 'call' : 'bill'] = 'done';
        message = action === 'ackCall' ? t('تمت تلبية نداء الويتر.', 'Waiter call answered.') : t('تم تسليم الحساب التجريبي.', 'Demo bill delivered.');
      } else {
        return;
      }
      table.events.push(message);
      render();
      announce(message);
      if (button.disabled) {
        const next = button.closest('[data-panel]')?.querySelector('button:not(:disabled)');
        if (next) next.focus({ preventScroll: true });
      }
    });
    render();
  });

  document.querySelectorAll('[data-upsell]').forEach(root => {
    const buttons = Array.from(root.querySelectorAll('[data-offer]'));
    root.addEventListener('click', event => {
      const action = event.target.closest('[data-offer]')?.dataset.offer;
      if (!action) return;
      const visible = { coffee: ['yes', 'no'], yes: ['reset'], no: ['reset'], reset: ['coffee'] }[action];
      buttons.forEach(button => { button.hidden = !visible.includes(button.dataset.offer); });
      text(root, '[data-offer-total]', money({ coffee: 80, yes: 150, no: 80, reset: 0 }[action]));
      buttons.find(button => !button.hidden).focus({ preventScroll: true });
    });
  });

  document.querySelectorAll('[data-roi]').forEach(root => {
    const inputs = Array.from(root.querySelectorAll('[data-input]'));
    function calculate() {
      const values = {};
      let invalid = null;
      for (const input of inputs) {
        const valid = input.validity.valid && Number.isFinite(input.valueAsNumber);
        input.setAttribute('aria-invalid', String(!valid));
        if (!valid && !invalid) invalid = input;
        values[input.dataset.input] = input.valueAsNumber;
      }
      text(root, '[data-validation]', invalid ? t('أدخل قيمة بين ', 'Enter a value between ') + invalid.min + t(' و', ' and ') + invalid.max + t(' في الحقل المحدد.', ' in the marked field.') : '');
      const revenue = values.bills * values.digital / 100 * values.acceptance / 100 * values.value;
      text(root, '[data-revenue]', invalid ? '—' : money(revenue));
      text(root, '[data-contribution]', invalid ? '—' : money(revenue * values.margin / 100 - values.subscription));
    }
    root.addEventListener('input', calculate);
    calculate();
  });
})();
