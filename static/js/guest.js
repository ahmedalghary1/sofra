const guestEnglish = document.documentElement.lang.startsWith('en');
const guestCopy = (arabic, english) => guestEnglish ? english : arabic;
const items = JSON.parse(document.getElementById('menu-data').textContent);
function quantityButton(icon) {
  const button = document.createElement('button');
  button.type = 'button';
  const icons = document.getElementById('control-icons').content;
  button.append(icons.querySelector('.ui-icon-' + icon).cloneNode(true));
  return button;
}
const backdrop = document.getElementById('modalBackdrop');
const content = document.getElementById('modalContent');
const closeModal = document.getElementById('closeModal');
const demoMode = window.SAFRA.demoMode;
const onlineMode = !!window.SAFRA.onlineMode;
const onlineDetails = {fulfillment: window.SAFRA.pickupEnabled ? 'pickup' : 'delivery',
  customer_name: '', customer_phone: '', delivery_address: ''};
const deliveryFee = () => onlineMode ? (onlineDetails.fulfillment === 'delivery' ? Number(window.SAFRA.deliveryFee) : 0)
  : Number(window.SAFRA.fixedDeliveryFee || 0);
const cart = [];
const existingRows = window.SAFRA.editMode ? JSON.parse(document.getElementById('existing-data').textContent) : [];
let current = null;
let requestKey = newRequestKey();
let cartNote = '';
let editReason = '';
let submittedTableId = null;
let submitting = false;
let modalOpener = null;
const draftKey = window.SAFRA.cartSession ? (window.SAFRA.staffMode ? 'serveza:staff-cart:' : 'serveza:table-cart:') + window.SAFRA.cartTable : null;
let pendingOrder = null;
const money = v => Number(v).toLocaleString(guestEnglish ? 'en-EG' : 'ar-EG-u-nu-latn', {minimumFractionDigits: 0, maximumFractionDigits: 2});
const el = (tag, cls, txt) => { const n=document.createElement(tag); if(cls)n.className=cls; if(txt!==undefined)n.textContent=txt; return n; };

function newRequestKey() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // LAN QR previews can use HTTP, where only getRandomValues is available.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
}

function orderLines() {
  return cart.map(row => ({item_id: row.item.id, quantity: row.quantity, size_id: row.sizeId, options: row.options}));
}

function saveDraft() {
  if (!draftKey) return;
  try {
    if (!cart.length && !pendingOrder) {
      sessionStorage.removeItem(draftKey);
      return;
    }
    sessionStorage.setItem(draftKey, JSON.stringify({session: window.SAFRA.cartSession, requestKey,
      lines: orderLines(), prices: cart.map(row => row.unit.toFixed(2)), note: cartNote, pending: pendingOrder,
      fulfillment: onlineMode ? onlineDetails.fulfillment : null,
      deliveryFee: onlineMode ? deliveryFee().toFixed(2) : null}));
  } catch { /* Ordering also works when browser storage is unavailable. */ }
}

function restoreLine(line) {
  const item = items.find(item => item.id === line?.item_id);
  if (!item || !Number.isInteger(line.quantity) || line.quantity < 1 || line.quantity > 30 ||
      !Array.isArray(line.options)) return null;
  const size = item.sizes.find(size => size.id === line.size_id);
  if (line.size_id !== null && !size) return null;
  const options = new Set(line.options);
  let matches = 0;
  let unit = Number(size?.price ?? item.price);
  for (const group of item.groups) {
    const selected = group.options.filter(option => options.has(option.id));
    if (selected.length < group.min || selected.length > group.max) return null;
    matches += selected.length;
    for (const option of selected) unit += Number(option.price);
  }
  if (matches !== line.options.length) return null;
  return {item, sizeId: line.size_id, sizeName: size?.name || '', options: line.options, quantity: line.quantity, unit};
}

function restoreDraft() {
  if (!draftKey) return;
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey));
    if (draft?.session !== window.SAFRA.cartSession || !Array.isArray(draft.lines)) {
      saveDraft();
      return;
    }
    const restored = draft.lines.map(restoreLine);
    let changed = restored.some((row, index) => !row || row.unit.toFixed(2) !== draft.prices?.[index]);
    cart.push(...restored.filter(Boolean));
    if (onlineMode && ((draft.fulfillment === 'pickup' && window.SAFRA.pickupEnabled)
        || (draft.fulfillment === 'delivery' && window.SAFRA.deliveryEnabled))) onlineDetails.fulfillment = draft.fulfillment;
    if (onlineMode && draft.deliveryFee !== deliveryFee().toFixed(2)) changed = true;
    cartNote = typeof draft.note === 'string' ? draft.note.slice(0, 500) : '';
    if (/^[a-f0-9-]{36}$/i.test(draft.requestKey)) requestKey = draft.requestKey;
    if (draft.pending?.idempotency_key === requestKey && Array.isArray(draft.pending.lines)) {
      pendingOrder = draft.pending;
      if (window.SAFRA.staffMode && pendingOrder.table_id) {
        const select = document.getElementById('staffTable');
        select.value = String(pendingOrder.table_id);
        select.disabled = true;
      }
    }
    if (changed && document.getElementById('cartMenuChange')) document.getElementById('cartMenuChange').hidden = false;
    saveDraft();
  } catch { /* Ignore invalid or unavailable session storage. */ }
  renderCartBar();
  if (pendingOrder) showPendingOrder();
}

function showPendingOrder() {
  const panel = el('div', 'cart-modal');
  const status = el('p', 'muted', guestCopy('يمكنك إعادة إرسال نفس الطلب بأمان لمعرفة حالته.', 'Retry the same order to confirm its status.'));
  status.setAttribute('role', 'status');
  const retry = el('button', 'btn btn-primary btn-full', guestCopy('التحقق من الطلب', 'Check order'));
  retry.type = 'button';
  retry.onclick = () => sendGuestOrder(status, retry);
  panel.append(el('h2', '', guestCopy('بانتظار تأكيد المطعم', 'Awaiting order confirmation')),
    el('strong', '', guestCopy('إجمالي الطلب · ', 'Order total · ') + money(pendingOrder.reviewed_total) + guestCopy(' ج.م', ' EGP')), status, retry);
  show(panel);
}

async function sendGuestOrder(status, button) {
  if (submitting) return;
  submitting = true;
  button.disabled = closeModal.disabled = true;
  backdrop.querySelectorAll('textarea, .cart-list').forEach(node => { node.inert = true; });
  status.textContent = guestCopy('جارٍ إرسال الطلب…', 'Sending order…');
  try {
    const response = await fetch(window.SAFRA.orderUrl, {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': window.SAFRA.csrf},
      body: JSON.stringify({...pendingOrder, session_token: window.SAFRA.sessionToken})});
    const result = await response.json();
    if (response.ok && result.ok && result.order_url) {
      cart.length = 0;
      pendingOrder = null;
      cartNote = '';
      saveDraft();
      renderCartBar();
      location.assign(result.order_url);
    } else if ((response.status === 400 || (onlineMode && response.status === 429)) && result.ok === false) {
      pendingOrder = null;
      if (window.SAFRA.staffMode) document.getElementById('staffTable').disabled = false;
      saveDraft();
      renderCartBar();
      submitting = false;
      openCart();
      const error = el('p', 'form-error', result.error);
      error.setAttribute('role', 'alert');
      const refresh = el('button', 'text-button', guestCopy('تحديث المنيو والأسعار', 'Refresh menu and prices'));
      refresh.type = 'button';
      refresh.onclick = () => location.reload();
      content.querySelector('.cart-modal h2').after(error, refresh);
    } else {
      showPendingOrder();
    }
  } catch {
    showPendingOrder();
  } finally {
    submitting = false;
    button.disabled = closeModal.disabled = false;
    backdrop.querySelectorAll('[inert]').forEach(node => { node.inert = false; });
  }
}

function show(node) {
  if (backdrop.hidden) modalOpener = document.activeElement;
  content.replaceChildren(node);
  backdrop.hidden = false;
  document.body.style.overflow = 'hidden';
  const heading = content.querySelector('h2');
  heading.id = 'guestModalTitle';
  heading.tabIndex = -1;
  heading.focus();
}

function hide() {
  if (submitting || backdrop.hidden) return;
  backdrop.hidden = true;
  document.body.style.overflow = '';
  modalOpener?.focus();
}

closeModal.onclick = hide;
backdrop.addEventListener('click',e=>{if(e.target===backdrop)hide()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hide()});
const categoryButtons = [...document.querySelectorAll('#categoryPills button')];
const foodCards = [...document.querySelectorAll('.food-card')];
const menuSearch = document.getElementById('menuSearch');
const menuEmpty = document.getElementById('menuEmpty');
const filterBar = document.querySelector('.menu-filter-bar');
const categoryPicker = filterBar?.querySelector('.menu-category-picker');
let selectedCategory = 'all';

function filterMenu() {
  const query = menuSearch?.value.trim().toLocaleLowerCase() || '';
  let matches = 0;
  for (const card of foodCards) {
    card.hidden = (selectedCategory !== 'all' && card.dataset.category !== selectedCategory) ||
      !card.textContent.toLocaleLowerCase().includes(query);
    if (!card.hidden) matches++;
  }
  if (menuEmpty) menuEmpty.hidden = !foodCards.length || matches > 0;
  if (categoryPicker) categoryPicker.querySelector('select').value = selectedCategory;
  categoryButtons.forEach(button => {
    const selected = selectedCategory === button.dataset.category;
    button.setAttribute('aria-pressed', String(selected));
    button.classList.toggle('active', selected);
  });
}
function selectCategory(button) {
  const pinned = filterBar && filterBar.getBoundingClientRect().top <= 1;
  selectedCategory = button.dataset.category;
  filterMenu();
  if (pinned) {
    const grid = document.getElementById('foodGrid');
    const results = menuEmpty && !menuEmpty.hidden ? menuEmpty : grid;
    window.scrollBy({top: results.getBoundingClientRect().top - filterBar.offsetHeight, behavior: 'instant'});
  }
}
categoryButtons.forEach(button => button.addEventListener('click', () => selectCategory(button)));
if (categoryPicker) {
  const rail = document.getElementById('categoryPills');
  const picker = categoryPicker.querySelector('select');
  function sizePicker() {
    // Compare with the full bar so the picker's own width cannot keep it open.
    categoryPicker.hidden = rail.scrollWidth <= filterBar.clientWidth + 1;
  }
  if ('ResizeObserver' in window) new ResizeObserver(sizePicker).observe(filterBar);
  else window.addEventListener('resize', sizePicker);
  document.fonts.ready.then(sizePicker);
  sizePicker();
  picker.addEventListener('change', () => {
    const button = categoryButtons.find(button => button.dataset.category === picker.value);
    if (!button) return;
    selectCategory(button);
    rail.scrollBy({left: button.getBoundingClientRect().left + button.offsetWidth / 2 -
      rail.getBoundingClientRect().left - rail.clientWidth / 2, behavior: 'instant'});
  });
}
if (demoMode) {
  // Bookmarks from the original section-based sample menu.
  function openLinkedSection() {
    const button = categoryButtons.find(button => '#' + button.dataset.category === location.hash);
    if (button) {
      selectCategory(button);
      filterBar.scrollIntoView({block: 'start'});
    }
  }
  openLinkedSection();
  window.addEventListener('hashchange', openLinkedSection);
}
const photoGrid = document.querySelector('[data-photo-fallback]');
if (photoGrid) {
  function replaceMissingPhoto(photo) {
    if (photo.matches?.('img.menu-photo') && photo.getAttribute('src') !== photoGrid.dataset.photoFallback) {
      photo.src = photoGrid.dataset.photoFallback;
    }
  }
  photoGrid.addEventListener('error', event => replaceMissingPhoto(event.target), true);
  photoGrid.querySelectorAll('img').forEach(photo => {
    if (photo.complete && !photo.naturalWidth) replaceMissingPhoto(photo);
  });
}
menuSearch?.addEventListener('input', filterMenu);
document.getElementById('clearGuestSearch')?.addEventListener('click', () => {
  menuSearch.value = '';
  selectedCategory = 'all';
  filterMenu();
  menuSearch.focus();
});
filterMenu();
document.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || backdrop.hidden) return;
  const controls = [...backdrop.querySelectorAll('button, input, textarea, select, a[href]')]
    .filter(control => !control.disabled && !control.closest('[inert]') && control.offsetParent);
  if (!controls.length) { event.preventDefault(); return; }
  const first = controls[0], last = controls[controls.length - 1];
  if (event.shiftKey && (document.activeElement === first || document.activeElement === content.querySelector('h2'))) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
});
document.querySelectorAll('.add-item').forEach(btn=>btn.addEventListener('click',()=>openItem(Number(btn.dataset.id))));
document.querySelectorAll('.demo-add-item').forEach(button => button.addEventListener('click', () => {
  if (!backdrop.hidden) return;
  const item = items.find(entry => entry.id === Number(button.dataset.id));
  const row = cart.find(entry => entry.item === item);
  if (row) {
    row.quantity = Math.min(30, row.quantity + 1);
  } else {
    cart.push({item, quantity: 1, options: [], sizeId: null, sizeName: '', unit: Number(item.price)});
  }
  renderCartBar();
}));

function updateCart() {
  requestKey = newRequestKey();
  submittedTableId = null;
  renderCartBar();
  saveDraft();
}

function openItem(id){
  if (!backdrop.hidden) return;
  if (pendingOrder) { showPendingOrder(); return; }
  current=items.find(x=>x.id===id); if(!current)return;
  const wrap=el('div','item-modal');
  wrap.append(el('span','eyebrow',guestCopy("اختار صنفك", "Customize item")),el('h2','',current.name),el('p','muted',current.description||guestCopy("طبق محضّر بعناية", "Prepared to order")));
  const form=el('form','item-options');form.id='itemOptions';
  if(current.sizes?.length){
    const group=el('fieldset','option-group');group.append(el('legend','',guestCopy("اختر الحجم", "Choose a size")));
    const choices=[{id:'',name:guestCopy("الحجم الأساسي", "Regular"),price:current.price},...current.sizes];
    choices.forEach((size,index)=>{
      const label=el('label','option-row');const input=el('input');input.type='radio';input.name='size';input.value=size.id;if(index===0)input.checked=true;
      label.append(input,el('span','',size.name),el('small','',money(size.price)+guestCopy(" ج.م", " EGP")));group.append(label);
    });form.append(group);
  }
  current.groups.forEach((g,index)=>{
    const group=el('fieldset','option-group');const legend=el('legend','',g.name+(g.min?guestCopy(" · مطلوب", " · Required"):''));group.append(legend);
    const hint=el('small','muted',guestCopy(`اختر من ${g.min} إلى ${g.max}`, `Choose ${g.min} to ${g.max}`));group.append(hint);
    g.options.forEach(o=>{
      const label=el('label','option-row');const input=el('input');input.type='checkbox';input.name='group-'+index;input.value=o.id;
      const name=el('span','',o.name);const price=el('small','',o.price==='0.00'?guestCopy("بدون زيادة", "Included"):'+'+money(o.price)+guestCopy(" ج.م", " EGP"));label.append(input,name,price);group.append(label);
    });form.append(group);
  });
  const quantityControl = el('div', 'qty-control');
  const minus = quantityButton('minus');
  const plus = quantityButton('plus');
  const quantity = el('strong', '', '1');
  minus.type = plus.type = 'button';
  minus.setAttribute('aria-label', guestCopy('تقليل الكمية', 'Decrease quantity'));
  plus.setAttribute('aria-label', guestCopy('زيادة الكمية', 'Increase quantity'));
  quantityControl.append(minus, quantity, plus);
  const err = el('p', 'form-error');
  err.setAttribute('role', 'status');
  const submit = el('button', 'btn btn-primary btn-full');
  submit.type = 'submit';
  let count = 1;
  function updatePrice() {
    const sizeId = form.querySelector('[name="size"]:checked')?.value;
    const size = current.sizes?.find(size => String(size.id) === sizeId);
    const chosen = new Set([...form.querySelectorAll('input[type="checkbox"]:checked')].map(input => Number(input.value)));
    let unitPrice = Number(size?.price ?? current.price);
    for (const group of current.groups) {
      for (const option of group.options) {
        if (chosen.has(option.id)) unitPrice += Number(option.price);
      }
    }
    submit.textContent = guestCopy('أضف للسلة · ', 'Add to cart · ') + money(unitPrice * count) + guestCopy(' ج.م', ' EGP');
    quantity.textContent = count;
    minus.disabled = count === 1;
    plus.disabled = count === 30;
  }
  minus.onclick = () => { count = Math.max(1, count - 1); updatePrice(); };
  plus.onclick = () => { count = Math.min(30, count + 1); updatePrice(); };
  form.append(quantityControl, err, submit);
  form.addEventListener('change', updatePrice);
  updatePrice();
  form.onsubmit=e=>{e.preventDefault();const selected=[];let extra=0;
    for(let index=0;index<current.groups.length;index++){const group=current.groups[index];const checked=[...form.querySelectorAll(`input[name="group-${index}"]:checked`)];if(checked.length<group.min||checked.length>group.max){err.textContent=guestCopy(`اختر العدد المناسب من ${group.name}.`, `Check the selection count for ${group.name}.`);return}checked.forEach(input=>{const option=group.options.find(o=>o.id===Number(input.value));selected.push(option.id);extra+=Number(option.price)})}
    const sizeId=form.querySelector('input[name="size"]:checked')?.value||null;
    const size=current.sizes?.find(x=>String(x.id)===sizeId);
    cart.push({item:current,quantity:count,options:selected,sizeId:size?.id||null,sizeName:size?.name||'',unit:Number(size?.price??current.price)+extra});updateCart();hide();};
  wrap.append(form);show(wrap);
}
function cartTotal(){return deliveryFee()+cart.reduce((s,x)=>s+x.quantity*x.unit,0)+existingRows.reduce((s,x)=>s+x.quantity*Number(x.unit_price),0)}
function renderCartBar() {
  const rows = pendingOrder?.lines ?? [...cart, ...existingRows];
  const count = rows.reduce((sum, row) => sum + row.quantity, 0);
  document.getElementById('cartBar').hidden = !count;
  document.getElementById('cartCount').textContent = count;
  document.getElementById('cartTotal').textContent = money(pendingOrder?.reviewed_total ?? cartTotal());
}
document.getElementById('openCart').onclick=openCart;
function openCart(){
  if (submitting) return;
  if (pendingOrder) { showPendingOrder(); return; }
  const editing=!!window.SAFRA.editMode;
  const wrap=el('div','cart-modal');
  wrap.append(el('h2', '', editing ? guestCopy("تفاصيل الطلب بعد التعديل", "Review order changes") : guestCopy("راجع طلبك", "Review your order")));
  if(!cart.length&&!existingRows.length){wrap.append(el('p','muted',guestCopy("السلة فارغة.", "Your cart is empty.")));show(wrap);return}
  const list=el('div','cart-list');
  existingRows.forEach((row,index)=>{const line=el('div','cart-line');const summary=el('span');summary.append(el('strong','',row.item_name+(row.size_name?' · '+row.size_name:'')),el('small','muted',row.modifiers.join(guestCopy("، ", ", "))||guestCopy("السعر الأصلي محفوظ", "Original price retained")));const controls=el('span','edit-line-controls');const qty=el('input');qty.type='number';qty.min='1';qty.max='30';qty.value=row.quantity;qty.setAttribute('aria-label',guestCopy("كمية ", "Quantity for ")+row.item_name);qty.onchange=()=>{const value=Number(qty.value);if(!Number.isInteger(value)||value<1||value>30){qty.value=row.quantity;return}row.quantity=value;updateCart();openCart()};const remove=el('button','remove-item',guestCopy("حذف", "Remove"));remove.type='button';remove.onclick=()=>{existingRows.splice(index,1);updateCart();openCart()};controls.append(qty,remove);line.append(summary,controls);list.append(line)});
  const totalAmount = el('strong', '', money(cartTotal()) + guestCopy(" ج.م", " EGP"));
  let subtotalAmount, deliveryAmount;
  function refreshTotal() {
    totalAmount.textContent = money(cartTotal()) + guestCopy(' ج.م', ' EGP');
    if (subtotalAmount) subtotalAmount.textContent = money(cartTotal() - deliveryFee()) + guestCopy(' ج.م', ' EGP');
    if (deliveryAmount) deliveryAmount.textContent = money(deliveryFee()) + guestCopy(' ج.م', ' EGP');
  }
  cart.forEach((row, index) => {
    const line = el('div', 'cart-line');
    const summary = el('span');
    const modifiers = row.options.map(id => row.item.groups.flatMap(group => group.options).find(option => option.id === id)?.name);
    summary.append(el('strong', '', row.item.name), el('small', 'muted', [row.sizeName, ...modifiers].filter(Boolean).join(guestCopy("، ", ", "))));
    const quantity = el('div', 'qty-control cart-quantity');
    const decrease = quantityButton('minus');
    const increase = quantityButton('plus');
    const count = el('b');
    const amount = el('strong');
    decrease.type = increase.type = 'button';
    decrease.setAttribute('aria-label', guestCopy("قلّل كمية ", "Decrease quantity for ") + row.item.name);
    increase.setAttribute('aria-label', guestCopy("زِد كمية ", "Increase quantity for ") + row.item.name);
    function renderLine() {
      count.textContent = row.quantity;
      decrease.disabled = row.quantity === 1;
      increase.disabled = row.quantity === 30;
      amount.textContent = money(row.unit * row.quantity) + guestCopy(" ج.م", " EGP");
    }
    function changeQuantity(delta) {
      row.quantity += delta;
      renderLine();
      updateCart();
      refreshTotal();
    }
    decrease.onclick = () => changeQuantity(-1);
    increase.onclick = () => changeQuantity(1);
    quantity.append(decrease, count, increase);
    summary.append(quantity);
    renderLine();
    const end = el('span');
    const remove = el('button', 'remove-item', guestCopy("حذف", "Remove"));
    remove.type = 'button';
    remove.setAttribute('aria-label', guestCopy("احذف ", "Remove ") + row.item.name);
    remove.onclick = () => {
      cart.splice(index, 1);
      updateCart();
      openCart();
    };
    end.append(amount, remove);
    line.append(summary, end);
    list.append(line);
  });
  wrap.append(list);

  let onlineForm;
  if (onlineMode) {
    const fields = document.getElementById('onlineCustomerFields').content.cloneNode(true);
    onlineForm = fields.querySelector('form');
    for (const [name, value] of Object.entries(onlineDetails)) onlineForm.elements[name].value = value;
    function updateOnlineDetails() {
      for (const name of Object.keys(onlineDetails)) onlineDetails[name] = onlineForm.elements[name].value;
      const delivery = onlineDetails.fulfillment === 'delivery';
      onlineForm.querySelector('[data-delivery-address]').hidden = !delivery;
      onlineForm.elements.delivery_address.required = delivery;
      refreshTotal();
      renderCartBar();
      saveDraft();
    }
    onlineForm.addEventListener('input', updateOnlineDetails);
    onlineForm.addEventListener('change', updateOnlineDetails);
    onlineForm.addEventListener('submit', event => event.preventDefault());
    updateOnlineDetails();
    wrap.append(fields);
  }

  let note;
  if (!demoMode) {
    note = el('textarea');
    const noteLabel=el('label','note-label',editing?guestCopy("سبب التعديل (إلزامي)", "Reason for editing (required)"):guestCopy("ملاحظة للطلب (اختياري)", "Order note (optional)"));
    note.maxLength=editing?240:500;note.placeholder=editing?guestCopy("مثال: طلب العميل تغيير الكمية", "For example, guest changed the quantity"):guestCopy("مثال: بدون بصل", "For example, no onions");note.value=editing?editReason:cartNote;
    note.oninput=()=>{if(editing)editReason=note.value;else {cartNote=note.value;saveDraft()}};
    noteLabel.append(note);wrap.append(noteLabel);
    if(!editing)wrap.append(el('p','allergy-note',guestCopy("لو عندك حساسية غذائية، أخبر فريق المطعم مباشرة.", "Please tell the venue team directly about food allergies.")));
  }
  const total = el('div', 'cart-total');
  const priceRows = [];
  if (onlineMode || window.SAFRA.fixedDeliveryFee) {
    const subtotalRow = el('div', 'online-price-row');
    subtotalAmount = el('strong');
    subtotalRow.append(el('span', '', guestCopy('إجمالي الأصناف', 'Items subtotal')), subtotalAmount);
    const feeRow = el('div', 'online-price-row');
    deliveryAmount = el('strong');
    feeRow.append(el('span', '', guestCopy('رسوم التوصيل', 'Delivery fee')), deliveryAmount);
    priceRows.push(subtotalRow, feeRow);
    refreshTotal();
  }
  total.append(el('span', '', guestCopy("الإجمالي", "Total")), totalAmount);
  const error = el('p', 'form-error');
  error.setAttribute('role', 'status');
  const send = el('button', 'btn btn-primary btn-full');
  if (!window.SAFRA.accepting) {
    send.disabled = true;
    send.textContent = editing ? guestCopy("التعديل متوقف حاليًا", "Order editing paused") : guestCopy("استقبال الطلبات متوقف حاليًا", "Ordering paused");
  } else if (demoMode) {
    send.textContent = guestCopy("أرسل الطلب التجريبي ←", "Submit demo order");
  } else {
    send.textContent = editing ? guestCopy("حفظ التعديل ←", "Save changes") : guestCopy("تأكيد وإرسال الطلب ←", "Confirm and place order");
  }

  send.onclick = async () => {
    if (submitting) return;
    const newLines = orderLines();
    if (draftKey) {
      if (onlineMode && !onlineForm.reportValidity()) return;
      const chosen = window.SAFRA.staffMode ? document.getElementById('staffTable')?.value : null;
      if (window.SAFRA.staffMode && !chosen) {
        error.textContent = guestCopy('اختر الطاولة قبل إرسال الطلب.', 'Choose a table before sending the order.');
        return;
      }
      pendingOrder = {idempotency_key: requestKey, note: cartNote, lines: newLines, reviewed_total: cartTotal().toFixed(2)};
      if (onlineMode) Object.assign(pendingOrder, onlineDetails, {reviewed_delivery_fee: deliveryFee().toFixed(2)});
      if (window.SAFRA.staffMode) {
        pendingOrder.table_id = Number(chosen);
        document.getElementById('staffTable').disabled = true;
      }
      saveDraft();
      await sendGuestOrder(error, send);
      return;
    }
    let payload;
    if (demoMode) {
      payload = {lines: newLines};
    } else if (editing) {
      if (editReason.trim().length < 3) {
        error.textContent = guestCopy("اكتب سبب التعديل قبل الحفظ.", "Enter a reason before saving.");
        return;
      }
      payload = {edit_key: requestKey, expected_version: window.SAFRA.version, reason: editReason,
                 existing: existingRows.map(row => ({id: row.id, quantity: row.quantity})), add: newLines};
    } else {
      payload = {idempotency_key: requestKey, note: cartNote, lines: newLines,
                 session_token: window.SAFRA.sessionToken, reviewed_total: cartTotal().toFixed(2)};
      if (window.SAFRA.staffMode) {
        const chosen = document.getElementById('staffTable')?.value;
        if (!chosen) {
          error.textContent = guestCopy("اختر الطاولة قبل إرسال الطلب.", "Choose a table before sending the order.");
          return;
        }
        if (submittedTableId && submittedTableId !== chosen) {
          error.textContent = guestCopy("تم بدء إرسال الطلب لطاولة أخرى. حدّث الصفحة قبل تغيير الطاولة.", "This request started at another table. Refresh before changing the table.");
          return;
        }
        submittedTableId = chosen;
        payload.table_id = Number(chosen);
      }
    }

    submitting = true;
    send.disabled = true;
    closeModal.disabled = true;
    list.inert = true;
    if (note) note.disabled = true;
    send.textContent = editing ? guestCopy("جارٍ الحفظ...", "Saving…") : guestCopy("جارٍ إرسال الطلب...", "Sending order…");
    error.textContent = '';
    try {
      const response = await fetch(window.SAFRA.orderUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': window.SAFRA.csrf},
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data?.ok) throw new Error(data?.error || guestCopy("تعذر إرسال الطلب.", "Could not submit the order."));
      if (demoMode) {
        cart.length = 0;
        renderCartBar();
        showDemoConfirmation(data.total);
      } else {
        location.href = data.order_url;
      }
    } catch (failure) {
      const message = failure instanceof TypeError ? guestCopy("تعذر الاتصال بالخادم.", "Could not connect to the server.") : failure.message;
      error.textContent = message + guestCopy(" يمكنك إعادة المحاولة.", " You can retry.");
      send.disabled = false;
      send.textContent = guestCopy("إعادة المحاولة", "Retry");
      send.focus();
    } finally {
      submitting = false;
      closeModal.disabled = false;
      list.inert = false;
      if (note) note.disabled = false;
    }
  };
  const footer = el('div', 'cart-review-footer');
  footer.append(...priceRows, total, error, send);
  wrap.append(footer);
  show(wrap);
}

function showDemoConfirmation(total) {
  const confirmation = el('div', 'cart-modal');
  const amount = el('div', 'cart-total');
  amount.append(el('span', '', guestCopy("الإجمالي", "Total")), el('strong', '', money(total) + guestCopy(" ج.م", " EGP")));
  const back = el('button', 'btn btn-primary btn-full', guestCopy("ارجع للمنيو", "Back to menu"));
  back.type = 'button';
  back.onclick = () => {
    hide();
    document.querySelector('.demo-add-item')?.focus();
  };
  confirmation.append(el('h2', '', guestCopy("تم تأكيد طلبك التجريبي", "Demo order confirmed")),
    el('p', 'muted', guestCopy("الطلب ده للعرض فقط.", "This order is for demonstration only.")), amount, back);
  show(confirmation);
}
if(window.SAFRA.editMode)renderCartBar();
restoreDraft();
if (draftKey) window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
