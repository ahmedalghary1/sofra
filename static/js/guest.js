const items = JSON.parse(document.getElementById('menu-data').textContent);
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
const money = v => Number(v).toLocaleString('ar-EG', {minimumFractionDigits: 0, maximumFractionDigits: 2});
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
document.querySelectorAll('#categoryPills button').forEach(btn=>btn.addEventListener('click',()=>{
  document.querySelectorAll('#categoryPills button').forEach(x=>x.classList.remove('active'));btn.classList.add('active');
  document.querySelectorAll('.food-card').forEach(card=>card.hidden=btn.dataset.category!=='all'&&card.dataset.category!==btn.dataset.category);
}));
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
  wrap.append(el('span','eyebrow','اختار صنفك'),el('h2','',current.name),el('p','muted',current.description||'طبق محضّر بعناية'));
  const form=el('form','item-options');form.id='itemOptions';
  if(current.sizes?.length){
    const group=el('fieldset','option-group');group.append(el('legend','','اختر الحجم'));
    const choices=[{id:'',name:'الحجم الأساسي',price:current.price},...current.sizes];
    choices.forEach((size,index)=>{
      const label=el('label','option-row');const input=el('input');input.type='radio';input.name='size';input.value=size.id;if(index===0)input.checked=true;
      label.append(input,el('span','',size.name),el('small','',money(size.price)+' ج.م'));group.append(label);
    });form.append(group);
  }
  current.groups.forEach((g,index)=>{
    const group=el('fieldset','option-group');const legend=el('legend','',g.name+(g.min?' · مطلوب':''));group.append(legend);
    const hint=el('small','muted',`اختر من ${g.min} إلى ${g.max}`);group.append(hint);
    g.options.forEach(o=>{
      const label=el('label','option-row');const input=el('input');input.type='checkbox';input.name='group-'+index;input.value=o.id;
      const name=el('span','',o.name);const price=el('small','',o.price==='0.00'?'بدون زيادة':'+'+money(o.price)+' ج.م');label.append(input,name,price);group.append(label);
    });form.append(group);
  });
  const qty=el('div','qty-control');qty.innerHTML='<button type="button" aria-label="تقليل الكمية">−</button><strong>1</strong><button type="button" aria-label="زيادة الكمية">＋</button>';
  let count=1;const buttons=qty.querySelectorAll('button');buttons[0].onclick=()=>{count=Math.max(1,count-1);qty.querySelector('strong').textContent=count};buttons[1].onclick=()=>{count=Math.min(30,count+1);qty.querySelector('strong').textContent=count};
  form.append(qty);const err=el('p','form-error');form.append(err);
  const submit=el('button','btn btn-primary btn-full','أضف للسلة · '+money(current.price)+' ج.م');submit.type='submit';form.append(submit);
  form.querySelectorAll('input[name="size"]').forEach(input=>input.addEventListener('change',()=>{
    const size=current.sizes.find(x=>String(x.id)===input.value);
    submit.textContent='أضف للسلة · '+money(size?.price??current.price)+' ج.م';
  }));
  form.onsubmit=e=>{e.preventDefault();const selected=[];let extra=0;
    for(let index=0;index<current.groups.length;index++){const group=current.groups[index];const checked=[...form.querySelectorAll(`input[name="group-${index}"]:checked`)];if(checked.length<group.min||checked.length>group.max){err.textContent=`اختر العدد المناسب من ${group.name}.`;return}checked.forEach(input=>{const option=group.options.find(o=>o.id===Number(input.value));selected.push(option.id);extra+=Number(option.price)})}
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
  wrap.append(el('h2', '', editing ? 'تفاصيل الطلب بعد التعديل' : 'راجع طلبك'));
  if(!cart.length&&!existingRows.length){wrap.append(el('p','muted','السلة فارغة.'));show(wrap);return}
  const list=el('div','cart-list');
  existingRows.forEach((row,index)=>{const line=el('div','cart-line');const summary=el('span');summary.append(el('strong','',row.item_name+(row.size_name?' · '+row.size_name:'')),el('small','muted',row.modifiers.join('، ')||'السعر الأصلي محفوظ'));const controls=el('span','edit-line-controls');const qty=el('input');qty.type='number';qty.min='1';qty.max='30';qty.value=row.quantity;qty.setAttribute('aria-label','كمية '+row.item_name);qty.onchange=()=>{const value=Number(qty.value);if(!Number.isInteger(value)||value<1||value>30){qty.value=row.quantity;return}row.quantity=value;updateCart();openCart()};const remove=el('button','remove-item','حذف');remove.type='button';remove.onclick=()=>{existingRows.splice(index,1);updateCart();openCart()};controls.append(qty,remove);line.append(summary,controls);list.append(line)});
  const totalAmount = el('strong', '', money(cartTotal()) + ' ج.م');
  cart.forEach((row, index) => {
    const line = el('div', 'cart-line');
    const summary = el('span');
    const modifiers = row.options.map(id => row.item.groups.flatMap(group => group.options).find(option => option.id === id)?.name);
    summary.append(el('strong', '', row.item.name), el('small', 'muted', [row.sizeName, ...modifiers].filter(Boolean).join('، ')));
    const quantity = el('div', 'qty-control cart-quantity');
    const decrease = el('button', '', '−');
    const increase = el('button', '', '＋');
    const count = el('b');
    const amount = el('strong');
    decrease.type = increase.type = 'button';
    decrease.setAttribute('aria-label', 'قلّل كمية ' + row.item.name);
    increase.setAttribute('aria-label', 'زِد كمية ' + row.item.name);
    function renderLine() {
      count.textContent = row.quantity;
      decrease.disabled = row.quantity === 1;
      increase.disabled = row.quantity === 30;
      amount.textContent = money(row.unit * row.quantity) + ' ج.م';
    }
    function changeQuantity(delta) {
      row.quantity += delta;
      renderLine();
      updateCart();
      totalAmount.textContent = money(cartTotal()) + ' ج.م';
    }
    decrease.onclick = () => changeQuantity(-1);
    increase.onclick = () => changeQuantity(1);
    quantity.append(decrease, count, increase);
    summary.append(quantity);
    renderLine();
    const end = el('span');
    const remove = el('button', 'remove-item', 'حذف');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'احذف ' + row.item.name);
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
    const noteLabel=el('label','note-label',editing?'سبب التعديل (إلزامي)':'ملاحظة للطلب (اختياري)');
    note.maxLength=editing?240:500;note.placeholder=editing?'مثال: طلب العميل تغيير الكمية':'مثال: بدون بصل';note.value=editing?editReason:cartNote;
    note.oninput=()=>{if(editing)editReason=note.value;else cartNote=note.value};
    noteLabel.append(note);wrap.append(noteLabel);
    if(!editing)wrap.append(el('p','allergy-note','لو عندك حساسية غذائية، أخبر فريق المطعم مباشرة.'));
  }
  const total = el('div', 'cart-total');
  total.append(el('span', '', 'الإجمالي'), totalAmount);
  const error = el('p', 'form-error');
  error.setAttribute('role', 'status');
  const send = el('button', 'btn btn-primary btn-full');
  if (!window.SAFRA.accepting) {
    send.disabled = true;
    send.textContent = editing ? 'التعديل متوقف حاليًا' : 'استقبال الطلبات متوقف حاليًا';
  } else if (demoMode) {
    send.textContent = 'أرسل الطلب التجريبي ←';
  } else {
    send.textContent = editing ? 'حفظ التعديل ←' : 'تأكيد وإرسال الطلب ←';
  }

  send.onclick = async () => {
    if (submitting) return;
    const newLines = cart.map(row => ({item_id: row.item.id, quantity: row.quantity, size_id: row.sizeId, options: row.options}));
    let payload;
    if (demoMode) {
      payload = {lines: newLines};
    } else if (editing) {
      if (editReason.trim().length < 3) {
        error.textContent = 'اكتب سبب التعديل قبل الحفظ.';
        return;
      }
      payload = {edit_key: requestKey, expected_version: window.SAFRA.version, reason: editReason,
                 existing: existingRows.map(row => ({id: row.id, quantity: row.quantity})), add: newLines};
    } else {
      payload = {idempotency_key: requestKey, note: cartNote, lines: newLines, session_token: window.SAFRA.sessionToken};
      if (window.SAFRA.staffMode) {
        const chosen = document.getElementById('staffTable')?.value;
        if (!chosen) {
          error.textContent = 'اختر الطاولة قبل إرسال الطلب.';
          return;
        }
        if (submittedTableId && submittedTableId !== chosen) {
          error.textContent = 'تم بدء إرسال الطلب لطاولة أخرى. حدّث الصفحة قبل تغيير الطاولة.';
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
    send.textContent = editing ? 'جارٍ الحفظ...' : 'جارٍ إرسال الطلب...';
    error.textContent = '';
    try {
      const response = await fetch(window.SAFRA.orderUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': window.SAFRA.csrf},
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data?.ok) throw new Error(data?.error || 'تعذر إرسال الطلب.');
      if (demoMode) {
        cart.length = 0;
        renderCartBar();
        showDemoConfirmation(data.total);
      } else {
        location.href = data.order_url;
      }
    } catch (failure) {
      const message = failure instanceof TypeError ? 'تعذر الاتصال بالخادم.' : failure.message;
      error.textContent = message + ' يمكنك إعادة المحاولة.';
      send.disabled = false;
      send.textContent = 'إعادة المحاولة';
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
  amount.append(el('span', '', 'الإجمالي'), el('strong', '', money(total) + ' ج.م'));
  const back = el('button', 'btn btn-primary btn-full', 'ارجع للمنيو');
  back.type = 'button';
  back.onclick = () => {
    hide();
    document.querySelector('.demo-add-item')?.focus();
  };
  confirmation.append(el('h2', '', 'تم تأكيد طلبك التجريبي'),
    el('p', 'muted', 'الطلب ده للعرض فقط.'), amount, back);
  show(confirmation);
}
if(window.SAFRA.editMode)renderCartBar();
