/* Table requests and the branch service inbox; the public demo stays in this browser. */
(() => {
  const english=document.documentElement.lang.startsWith('en');
  const copy=(ar,en)=>english?en:ar;
  const kindName=kind=>kind==='bill'?copy('طلب الحساب','Bill request'):copy('استدعاء الويتر','Waiter call');
  const stateName=status=>({open:copy('تم إرسال الطلب وينتظر الاستلام.','Request sent, awaiting acceptance.'),
    acknowledged:copy('استلم فريق الخدمة طلبك.','The service team accepted your request.'),
    in_progress:copy('طلبك قيد التنفيذ.','Your request is in progress.')}[status]||'');
  const panel=document.querySelector('[data-service-panel]');
  if(panel){
    const forms=[...panel.querySelectorAll('form[data-service-kind]')];
    const demo=panel.dataset.demo==='true';
    let available=panel.dataset.available==='true',revision=0,checking=false;
    let states=Object.fromEntries(forms.filter(form=>form.dataset.currentStatus).map(form=>[form.dataset.serviceKind,{status:form.dataset.currentStatus}]));
    const sending=new Set(),errors={},closed={};
    function render(){
      panel.querySelector('[data-service-availability]').textContent=available?'':copy(
        'استدعاءات الخدمة متوقفة حاليًا. تواصل مع فريق المطعم مباشرة.',
        'Service requests are paused. Please speak to the restaurant team.');
      forms.forEach(form=>{
        const kind=form.dataset.serviceKind,status=states[kind]?.status;
        form.querySelector('button').disabled=!available||sending.has(kind)||Boolean(status);
        form.querySelector('[data-service-state]').textContent=sending.has(kind)?copy('جارٍ إرسال الطلب…','Sending request…')
          :errors[kind]||stateName(status)||closed[kind]||'';
      });
    }
    function updateStates(next){
      forms.forEach(form=>{
        const kind=form.dataset.serviceKind;
        if(states[kind]&&!next[kind])closed[kind]=copy('أُغلق الطلب. يمكنك إرسال طلب جديد.','The request closed. You can send a new request.');
        if(next[kind])delete errors[kind];
      });
      states=next;render();
    }
    const demoCards={};
    function demoCard(kind,status){
      const host=panel.querySelector('[data-demo-service-cards]');
      panel.querySelector('[data-demo-service-queue]').hidden=false;
      const card=demoCards[kind]||document.createElement('article');
      card.className='demo-service-ticket';card.dataset.demoCall=kind;
      const heading=document.createElement('h4');heading.textContent=copy('طاولة 05 · ','Table 05 · ')+kindName(kind);
      const state=document.createElement('p');state.textContent=status==='done'?copy('اكتمل الطلب التجريبي.','Demo request completed.'):stateName(status);
      card.replaceChildren(heading,state);
      if(status!=='done'){
        const button=document.createElement('button');button.type='button';button.className='btn btn-dark';
        button.textContent=status==='open'?copy('استلام الطلب','Accept request'):status==='acknowledged'?copy('بدء التنفيذ','Start service'):copy('تم التنفيذ','Mark complete');
        button.addEventListener('click',()=>{
          const next={open:'acknowledged',acknowledged:'in_progress',in_progress:'done'}[status];
          if(next==='done'){delete states[kind];closed[kind]=copy('اكتمل الطلب التجريبي. يمكنك التجربة مجددًا.','Demo completed. You can try again.');}
          else states[kind]={status:next};
          demoCard(kind,next);render();
          (card.querySelector('button')||forms.find(form=>form.dataset.serviceKind===kind).querySelector('button')).focus();
        });
        card.append(button);
      }
      if(!demoCards[kind])host.append(card);
      demoCards[kind]=card;
    }
    forms.forEach(form=>form.addEventListener('submit',async event=>{
      event.preventDefault();
      const kind=form.dataset.serviceKind;
      if(!available||sending.has(kind)||states[kind])return;
      delete errors[kind];delete closed[kind];
      if(demo){states[kind]={status:'open'};demoCard(kind,'open');render();
        panel.querySelector('[data-demo-service-queue]').scrollIntoView({block:'nearest'});return;}
      revision++;sending.add(kind);render();
      try{
        const body=new FormData(form);
        const csrf=document.cookie.match(/(?:^|;\s*)csrftoken=([A-Za-z0-9]{32})(?:;|$)/);
        if(csrf)body.set('csrfmiddlewaretoken',csrf[1]);
        const response=await fetch(form.action,{method:'POST',body,credentials:'same-origin',cache:'no-store',
          headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
        const data=await response.json();
        if(!response.ok||!data.ok)throw new Error(data.error||copy('تعذر إرسال الطلب.','Could not send the request.'));
        updateStates(data.calls);
      }catch(error){errors[kind]=error.name==='TypeError'||error.name==='TimeoutError'||error.name==='SyntaxError'
        ?copy('تعذر تأكيد الطلب. تحقق من الاتصال؛ ستظهر حالته عند عودة التحديث.','Could not confirm the request. Check your connection; its status will update when connected.')
        :error.message;
      }finally{revision++;sending.delete(kind);render();}
    }));
    async function refreshTable(){
      if(checking||document.hidden||sending.size)return;
      checking=true;const version=revision;
      try{
        const response=await fetch(panel.dataset.statusUrl,{cache:'no-store',credentials:'same-origin',
          headers:{'X-Table-Session':panel.dataset.session},signal:AbortSignal.timeout(8000)});
        const data=await response.json();
        if(version!==revision)return;
        if(response.status===409){available=false;forms.forEach(form=>errors[form.dataset.serviceKind]=data.error);render();return;}
        if(!response.ok||!data.ok)throw new Error('service status unavailable');
        available=data.available;updateStates(data.calls);
      }catch(_){/* Keep the last confirmed state; never announce an unsaved request as sent. */}
      finally{checking=false;}
    }
    render();
    if(!demo){refreshTable();setInterval(refreshTable,3000);window.addEventListener('online',refreshTable);
      document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshTable();});}
  }

  const inbox=document.querySelector('[data-service-inbox]');
  if(inbox){
    const board=document.getElementById('serviceBoard');
    const connection=inbox.querySelector('[data-service-connection]');
    const summary=inbox.querySelector('[data-service-summary]');
    const alert=inbox.querySelector('[data-service-alert]');
    let signature=board?.dataset.signature,checking=false,lastInteraction=0,maxSeen=null,alertTimer,previousFocus;
    inbox.querySelector('[data-service-alert-close]').addEventListener('click',()=>{
      alert.hidden=true;clearTimeout(alertTimer);
      const target=previousFocus?.isConnected&&!alert.contains(previousFocus)?previousFocus:document.getElementById('workspaceMain');
      target?.focus({preventScroll:true});
    });
    document.addEventListener('pointerdown',()=>{lastInteraction=Date.now();},true);
    async function refreshInbox(){
      if(checking||document.hidden)return;
      checking=true;
      try{
        const url=new URL(inbox.dataset.feed,location.href);
        url.searchParams.set('lang',english?'en':'ar');
        if(board){const current=new URLSearchParams(location.search);
          for(const key of ['queue','page'])if(current.has(key))url.searchParams.set(key,current.get(key));
          url.searchParams.set('board','1');url.searchParams.set('signature',signature);}
        const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
        if(!response.ok||response.redirected)throw new Error('service inbox unavailable');
        const data=await response.json();
        if(String(data.branch)!==inbox.dataset.branch){location.reload();return;}
        inbox.querySelector('[data-service-count]').textContent=data.active_count;
        inbox.classList.toggle('has-requests',data.active_count>0);
        const text=data.latest.map(call=>call.table+' · '+kindName(call.kind)).join(copy('، ', '; '))
          ||copy('لا توجد طلبات خدمة جارية في الفرع.','No active service requests in this branch.');
        if(summary.textContent!==text)summary.textContent=text;
        const incoming=data.latest.find(call=>call.status==='open'&&(maxSeen===null||call.id>maxSeen));
        if(incoming&&maxSeen!==null&&!board){
          previousFocus=document.activeElement;
          inbox.querySelector('[data-service-alert-text]').textContent=incoming.table+' · '+kindName(incoming.kind);
          alert.hidden=false;clearTimeout(alertTimer);
          alertTimer=setTimeout(()=>{if(!alert.contains(document.activeElement))alert.hidden=true;},8000);
        }
        maxSeen=Math.max(maxSeen||0,...data.latest.map(call=>call.id));
        if(board&&data.html&&Date.now()-lastInteraction>700){
          const focused=document.activeElement.closest('[data-call]')?.dataset.call;
          board.innerHTML=data.html;signature=data.signature;
          if(focused){const card=[...board.querySelectorAll('[data-call]')].find(node=>node.dataset.call===focused);
            (card?.querySelector('button')||document.querySelector('.call-filters [aria-current]'))?.focus({preventScroll:true});}
        }
        const overdue=document.querySelector('[data-service-overdue]');
        if(overdue)overdue.textContent=data.overdue_count;
        connection.textContent=copy('متصل · طلبات الخدمة تتحدث تلقائيًا.','Connected · service requests update automatically.');
        inbox.classList.remove('service-offline');
      }catch(_){
        connection.textContent=copy('التحديث متوقف مؤقتًا. نعيد الاتصال تلقائيًا؛ يمكنك تحديث الصفحة.','Updates paused. Reconnecting automatically; you can refresh the page.');
        inbox.classList.add('service-offline');
      }finally{checking=false;}
    }
    refreshInbox();setInterval(refreshInbox,2000);window.addEventListener('online',refreshInbox);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshInbox();});
  }
})();
