/* Progressive help for existing forms and tables; no pricing or submission logic. */
(() => {
  const english = document.documentElement.lang.startsWith('en');
  const copy = (ar,en) => english ? en : ar;
  let sequence = 0;
  const identifier = prefix => prefix + '-' + (++sequence);
  function labelFields(root) {
    root.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]),select,textarea').forEach(field => {
      if (field.labels?.length || field.hasAttribute('aria-label') || field.hasAttribute('aria-labelledby')) return;
      const text = field.getAttribute('placeholder') || (field.matches('select') && field.options[0]?.textContent.trim());
      if (!text) return;
      const label = document.createElement('label');
      label.className = 'ux-field';
      const title = document.createElement('span');
      title.textContent = text;
      field.before(label);
      label.append(title,field);
    });
  }
  labelFields(document);
  const modalContent = document.getElementById('modalContent');
  if (modalContent) new MutationObserver(() => labelFields(modalContent)).observe(modalContent,{childList:true,subtree:true});
  function validationMessage(field) {
    const validity=field.validity;
    return validity.valueMissing ? copy('هذا الحقل مطلوب.','This field is required.')
      : validity.typeMismatch && field.type==='email' ? copy('اكتب بريدًا إلكترونيًا صالحًا.','Enter a valid email address.')
      : validity.tooShort ? copy('أكمل هذا الحقل بالعدد المطلوب من الحروف.','Use the required number of characters.')
      : validity.rangeUnderflow || validity.rangeOverflow || validity.stepMismatch ? copy('راجع الرقم والحدود الموضحة لهذا الحقل.','Check the number and the allowed range for this field.')
      : copy('راجع هذا الحقل قبل المتابعة.','Check this field before continuing.');
  }
  function clearError(field) {
    if (!field.dataset.uxError) return;
    const id = field.dataset.uxError;
    document.getElementById(id)?.remove();
    const described = (field.getAttribute('aria-describedby') || '').split(/\s+/).filter(value => value && value !== id);
    if (described.length) field.setAttribute('aria-describedby',described.join(' '));
    else field.removeAttribute('aria-describedby');
    field.removeAttribute('aria-invalid');
    delete field.dataset.uxError;
  }
  document.addEventListener('invalid',event => {
    const field = event.target;
    if (!field.matches('input,select,textarea')) return;
    let parent = field.parentElement;
    while (parent) {if (parent.matches('details')) parent.open=true;parent=parent.parentElement;}
    if (!field.dataset.uxError) {
      const error = document.createElement('small');
      error.className='ux-field-error';
      error.id=identifier('field-error');
      const password=field.closest('.ops-password');
      const target=password || field.closest('label') || field;
      target.after(error);
      field.dataset.uxError=error.id;
      field.setAttribute('aria-describedby',[field.getAttribute('aria-describedby'),error.id].filter(Boolean).join(' '));
    }
    document.getElementById(field.dataset.uxError).textContent=validationMessage(field);
    field.setAttribute('aria-invalid','true');
  },true);
  function updateError(event) {
    const field=event.target;
    if (!field.dataset?.uxError) return;
    if (field.validity.valid) clearError(field);
    else document.getElementById(field.dataset.uxError).textContent=validationMessage(field);
  }
  document.addEventListener('input',updateError);
  document.addEventListener('change',updateError);
  const tables = [...document.querySelectorAll('.table-scroll')].map(container => {
    const hint=document.createElement('p');
    hint.className='ux-table-hint';hint.id=identifier('table-help');hint.hidden=true;
    hint.textContent=copy('↔ مرّر داخل الجدول لعرض باقي الأعمدة.','↔ Scroll inside the table to see more columns.');
    container.before(hint);
    return {container,hint};
  });
  function updateTables() {
    tables.forEach(({container,hint})=>{
      const overflow=container.scrollWidth>container.clientWidth+2;
      hint.hidden=!overflow;
      if(overflow){container.tabIndex=0;container.setAttribute('role','region');
        container.setAttribute('aria-label',copy('جدول قابل للتمرير','Scrollable table'));
        container.setAttribute('aria-describedby',hint.id);
      } else {container.removeAttribute('tabindex');container.removeAttribute('role');container.removeAttribute('aria-label');container.removeAttribute('aria-describedby');}
    });
  }
  if (tables.length) {
    const observer=new ResizeObserver(updateTables);
    tables.forEach(({container})=>observer.observe(container));
    document.addEventListener('toggle',updateTables,true);
    document.fonts.ready.then(updateTables);
    updateTables();
  }
  document.querySelectorAll('.section-tabs').forEach(nav=>{
    function active(){nav.querySelectorAll('a').forEach(link=>{
      if(link.hash===location.hash)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');
    });}
    window.addEventListener('hashchange',active);active();
  });
})();
