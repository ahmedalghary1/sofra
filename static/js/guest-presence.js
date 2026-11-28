window.acquireTablePresence = async function(status) {
  const english=document.documentElement.lang.startsWith('en'),copy=(ar,en)=>english?en:ar;
  function failure(message){const error=new Error(message);error.locationCheck=true;return error;}
  let policy;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  try {
    const response=await fetch(window.SAFRA.presenceUrl,{cache:'no-store',signal:controller.signal});
    policy=await response.json();
    if(!response.ok||!policy.ok)throw failure(policy.error||copy('تعذر بدء تحقق الموقع. أعد المحاولة.','Could not start the location check. Please retry.'));
    if(typeof policy.required!=='boolean')throw new Error('Invalid policy response');
  } catch(error) {
    throw error.locationCheck?error:failure(copy('تعذر الاتصال للتحقق من الموقع. أعد المحاولة.','Could not connect to check your location. Please retry.'));
  } finally {clearTimeout(timer);}
  if(!policy.required)return {};
  if(!window.isSecureContext||!navigator.geolocation)throw failure(copy('تحديد الموقع يحتاج اتصال HTTPS ومتصفحًا يدعمه. تواصل مع الويتر.','Location requires HTTPS and a supported browser. Please ask your waiter.'));
  status.textContent=copy('اسمح بتحديد موقعك للتحقق من وجودك قرب الفرع…','Allow location access to check that you are near the branch…');
  const position=await new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(resolve,error=>reject(failure(
    error.code===1?copy('لم تسمح بتحديد الموقع. اسمح بالموقع الدقيق من إعدادات المتصفح ثم أعد المحاولة، أو اطلب مساعدة الويتر.','Location permission denied. Allow precise location in browser settings and retry, or ask your waiter.'):
    error.code===3?copy('انتهت مهلة تحديد الموقع. فعّل الموقع وأعد المحاولة.','Location timed out. Enable location and try again.'):
    copy('تعذر تحديد موقعك. فعّل خدمات الموقع وأعد المحاولة أو اطلب مساعدة الويتر.','Your location is unavailable. Enable location services and retry, or ask your waiter.'))),
    {enableHighAccuracy:true,maximumAge:0,timeout:15000}));
  if(!Number.isFinite(position.coords.accuracy)||position.coords.accuracy>policy.max_accuracy_m)throw failure(copy(
    'دقة الموقع غير كافية. فعّل الموقع الدقيق وأعد المحاولة أو اطلب مساعدة الويتر.',
    'Location accuracy is too low. Enable precise location and retry, or ask your waiter.'));
  status.textContent=copy('جارٍ التحقق وإرسال الطلب…','Checking location and sending order…');
  return {presence:{challenge:policy.challenge,latitude:position.coords.latitude,longitude:position.coords.longitude,
    accuracy:position.coords.accuracy,timestamp:position.timestamp}};
};
