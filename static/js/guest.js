const items = JSON.parse(document.getElementById('menu-data').textContent);
const backdrop = document.getElementById('modalBackdrop');
const content = document.getElementById('modalContent');
const cart = [];
const existingRows = window.SAFRA.editMode ? JSON.parse(document.getElementById('existing-data').textContent) : [];
let current = null;
let requestKey = crypto.randomUUID();
let cartNote = '';
let editReason = '';
let submittedTableId = null;
const money = v => Number(v).toLocaleString('ar-EG', {minimumFractionDigits: 0, maximumFractionDigits: 2});
const el = (tag, cls, txt) => { const n=document.createElement(tag); if(cls)n.className=cls; if(txt!==undefined)n.textContent=txt; return n; };
function show(node){content.replaceChildren(node);backdrop.hidden=false;document.body.style.overflow='hidden'}
function hide(){backdrop.hidden=true;document.body.style.overflow=''}
document.getElementById('closeModal').onclick=hide;
backdrop.addEventListener('click',e=>{if(e.target===backdrop)hide()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')hide()});
document.querySelectorAll('#categoryPills button').forEach(btn=>btn.addEventListener('click',()=>{
  document.querySelectorAll('#categoryPills button').forEach(x=>x.classList.remove('active'));btn.classList.add('active');
  document.querySelectorAll('.food-card').forEach(card=>card.hidden=btn.dataset.category!=='all'&&card.dataset.category!==btn.dataset.category);
}));
document.querySelectorAll('.add-item').forEach(btn=>btn.addEventListener('click',()=>openItem(Number(btn.dataset.id))));
function openItem(id){
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
    cart.push({item:current,quantity:count,options:selected,sizeId:size?.id||null,sizeName:size?.name||'',unit:Number(size?.price??current.price)+extra});requestKey=crypto.randomUUID();submittedTableId=null;renderCartBar();hide();};
  wrap.append(form);show(wrap);
}
function cartTotal(){return cart.reduce((s,x)=>s+x.quantity*x.unit,0)+existingRows.reduce((s,x)=>s+x.quantity*Number(x.unit_price),0)}
function renderCartBar(){const bar=document.getElementById('cartBar');const count=cart.reduce((s,x)=>s+x.quantity,0)+existingRows.reduce((s,x)=>s+x.quantity,0);bar.hidden=!count;document.getElementById('cartCount').textContent=count;document.getElementById('cartTotal').textContent=money(cartTotal());}
document.getElementById('openCart').onclick=openCart;
function openCart(){const editing=!!window.SAFRA.editMode;const wrap=el('div','cart-modal');wrap.append(el('span','eyebrow',editing?'تعديل الطلب':'راجع طلبك'),el('h2','',editing?'تفاصيل الطلب بعد التعديل':'سلتك جاهزة'));if(!cart.length&&!existingRows.length){wrap.append(el('p','muted','السلة فارغة.'));show(wrap);return}
  const list=el('div','cart-list');
  existingRows.forEach((row,index)=>{const line=el('div','cart-line');const summary=el('span');summary.append(el('strong','',row.item_name+(row.size_name?' · '+row.size_name:'')),el('small','muted',row.modifiers.join('، ')||'السعر الأصلي محفوظ'));const controls=el('span','edit-line-controls');const qty=el('input');qty.type='number';qty.min='1';qty.max='30';qty.value=row.quantity;qty.setAttribute('aria-label','كمية '+row.item_name);qty.onchange=()=>{const value=Number(qty.value);if(!Number.isInteger(value)||value<1||value>30){qty.value=row.quantity;return}row.quantity=value;requestKey=crypto.randomUUID();renderCartBar();openCart()};const remove=el('button','remove-item','حذف');remove.type='button';remove.onclick=()=>{existingRows.splice(index,1);requestKey=crypto.randomUUID();renderCartBar();openCart()};controls.append(qty,remove);line.append(summary,controls);list.append(line)});
  cart.forEach((row,index)=>{const line=el('div','cart-line');const summary=el('span');summary.append(el('strong','',row.quantity+'× '+row.item.name),el('small','muted',[row.sizeName,...row.options.map(id=>row.item.groups.flatMap(g=>g.options).find(o=>o.id===id)?.name)].filter(Boolean).join('، ')));const end=el('span');end.append(el('strong','',money(row.unit*row.quantity)+' ج.م'));const remove=el('button','remove-item','حذف');remove.type='button';remove.onclick=()=>{cart.splice(index,1);requestKey=crypto.randomUUID();submittedTableId=null;renderCartBar();openCart()};end.append(remove);line.append(summary,end);list.append(line)});wrap.append(list);
  const noteLabel=el('label','note-label',editing?'سبب التعديل (إلزامي)':'ملاحظة للطلب (اختياري)');const note=el('textarea');note.maxLength=editing?240:500;note.placeholder=editing?'مثال: طلب العميل تغيير الكمية':'مثال: بدون بصل';note.value=editing?editReason:cartNote;note.oninput=()=>{if(editing)editReason=note.value;else cartNote=note.value};noteLabel.append(note);wrap.append(noteLabel);
  if(!editing)wrap.append(el('p','allergy-note','لو عندك حساسية غذائية، أخبر فريق المطعم مباشرة.'));
  const total=el('div','cart-total');total.append(el('span','','الإجمالي'),el('strong','',money(cartTotal())+' ج.م'));wrap.append(total);
  const error=el('p','form-error');wrap.append(error);
  const send=el('button','btn btn-primary btn-full',editing?'حفظ التعديل ←':'تأكيد وإرسال الطلب ←');send.disabled=!window.SAFRA.accepting;if(!window.SAFRA.accepting)send.textContent='التعديل متوقف حاليًا';
  send.onclick=async()=>{const newLines=cart.map(x=>({item_id:x.item.id,quantity:x.quantity,size_id:x.sizeId,options:x.options}));let payload;
    if(editing){if(editReason.trim().length<3){error.textContent='اكتب سبب التعديل قبل الحفظ.';return}payload={edit_key:requestKey,expected_version:window.SAFRA.version,reason:editReason,existing:existingRows.map(x=>({id:x.id,quantity:x.quantity})),add:newLines}}
    else {payload={idempotency_key:requestKey,note:cartNote,lines:newLines};if(window.SAFRA.staffMode){const chosen=document.getElementById('staffTable')?.value;if(!chosen){error.textContent='اختر الطاولة قبل إرسال الطلب.';return}if(submittedTableId&&submittedTableId!==chosen){error.textContent='تم بدء إرسال الطلب لطاولة أخرى. حدّث الصفحة قبل تغيير الطاولة.';return}submittedTableId=chosen;payload.table_id=Number(submittedTableId)}}
    send.disabled=true;send.textContent='جارٍ الحفظ...';error.textContent='';try{const response=await fetch(window.SAFRA.orderUrl,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':window.SAFRA.csrf},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw new Error(data.error||'تعذر إرسال الطلب.');location.href=data.order_url}catch(e){error.textContent=e.message+' يمكنك إعادة المحاولة بأمان.';send.disabled=false;send.textContent='إعادة المحاولة'}};wrap.append(send);show(wrap)}
if(window.SAFRA.editMode)renderCartBar();
