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
const cart = [];
const existingRows = window.SAFRA.editMode ? JSON.parse(document.getElementById('existing-data').textContent) : [];
let current = null;
let requestKey = newRequestKey();
let cartNote = '';
let editReason = '';
let submittedTableId = null;
let submitting = false;
let modalOpener = null;
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
  categoryButtons.forEach(button => {
    const selected = selectedCategory === button.dataset.category;
    button.setAttribute('aria-pressed', String(selected));
    button.classList.toggle('active', selected);
  });
}
categoryButtons.forEach(button => button.addEventListener('click', () => {
  selectedCategory = button.dataset.category;
  filterMenu();
}));
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
}

function openItem(id){
  if (!backdrop.hidden) return;
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
function cartTotal(){return cart.reduce((s,x)=>s+x.quantity*x.unit,0)+existingRows.reduce((s,x)=>s+x.quantity*Number(x.unit_price),0)}
function renderCartBar(){const bar=document.getElementById('cartBar');const count=cart.reduce((s,x)=>s+x.quantity,0)+existingRows.reduce((s,x)=>s+x.quantity,0);bar.hidden=!count;document.getElementById('cartCount').textContent=count;document.getElementById('cartTotal').textContent=money(cartTotal());}
document.getElementById('openCart').onclick=openCart;
function openCart(){
  if (submitting) return;
  const editing=!!window.SAFRA.editMode;
  const wrap=el('div','cart-modal');
  wrap.append(el('h2', '', editing ? guestCopy("تفاصيل الطلب بعد التعديل", "Review order changes") : guestCopy("راجع طلبك", "Review your order")));
  if(!cart.length&&!existingRows.length){wrap.append(el('p','muted',guestCopy("السلة فارغة.", "Your cart is empty.")));show(wrap);return}
  const list=el('div','cart-list');
  existingRows.forEach((row,index)=>{const line=el('div','cart-line');const summary=el('span');summary.append(el('strong','',row.item_name+(row.size_name?' · '+row.size_name:'')),el('small','muted',row.modifiers.join(guestCopy("، ", ", "))||guestCopy("السعر الأصلي محفوظ", "Original price retained")));const controls=el('span','edit-line-controls');const qty=el('input');qty.type='number';qty.min='1';qty.max='30';qty.value=row.quantity;qty.setAttribute('aria-label',guestCopy("كمية ", "Quantity for ")+row.item_name);qty.onchange=()=>{const value=Number(qty.value);if(!Number.isInteger(value)||value<1||value>30){qty.value=row.quantity;return}row.quantity=value;updateCart();openCart()};const remove=el('button','remove-item',guestCopy("حذف", "Remove"));remove.type='button';remove.onclick=()=>{existingRows.splice(index,1);updateCart();openCart()};controls.append(qty,remove);line.append(summary,controls);list.append(line)});
  const totalAmount = el('strong', '', money(cartTotal()) + guestCopy(" ج.م", " EGP"));
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
      totalAmount.textContent = money(cartTotal()) + guestCopy(" ج.م", " EGP");
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

  let note;
  if (!demoMode) {
    note = el('textarea');
    const noteLabel=el('label','note-label',editing?guestCopy("سبب التعديل (إلزامي)", "Reason for editing (required)"):guestCopy("ملاحظة للطلب (اختياري)", "Order note (optional)"));
    note.maxLength=editing?240:500;note.placeholder=editing?guestCopy("مثال: طلب العميل تغيير الكمية", "For example, guest changed the quantity"):guestCopy("مثال: بدون بصل", "For example, no onions");note.value=editing?editReason:cartNote;
    note.oninput=()=>{if(editing)editReason=note.value;else cartNote=note.value};
    noteLabel.append(note);wrap.append(noteLabel);
    if(!editing)wrap.append(el('p','allergy-note',guestCopy("لو عندك حساسية غذائية، أخبر فريق المطعم مباشرة.", "Please tell the venue team directly about food allergies.")));
  }
  const total = el('div', 'cart-total');
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
    const newLines = cart.map(row => ({item_id: row.item.id, quantity: row.quantity, size_id: row.sizeId, options: row.options}));
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
  wrap.append(total, error, send);
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
