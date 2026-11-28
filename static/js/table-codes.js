(() => {
  const form=document.querySelector('[data-table-batch]');if(!form)return;
  const english=document.documentElement.lang.startsWith('en'),output=form.querySelector('[data-batch-summary]');
  function preview(){const count=Number(form.elements.count.value),start=Number(form.elements.start_number.value);
    output.textContent=Number.isInteger(count)&&count>0&&count<=200&&Number.isInteger(start)&&start>0&&start+count-1<=99999
      ?(english?`${count} new tables, numbered ${start}–${start+count-1}.`:`${count} طاولة جديدة، بالأرقام من ${start} إلى ${start+count-1}.`):'';
  }
  form.addEventListener('input',preview);preview();
})();
