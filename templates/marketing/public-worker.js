// Only public GET navigations qualify for this recovery page. The worker stores no responses.
const publicPage = /^\/(?:$|(?:about|features|solutions|pricing|how-it-works|demo|resources|faq|contact|privacy|terms|cookies|offline|maintenance)\/(?:[\w-]+\/)?$)/;
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || request.mode !== 'navigate' ||
      url.origin !== self.location.origin || !publicPage.test(url.pathname)) return;
  event.respondWith(fetch(request).catch(() => {
    const en = url.searchParams.get('lang') === 'en';
    const title = en ? 'Connection interrupted' : 'انقطع الاتصال';
    const copy = en ? 'Reconnect to load this page. An order is received only after server confirmation.' :
      'اتصل بالشبكة مجددًا لفتح الصفحة. تأكيد الخادم هو ما يثبت استلام الطلب.';
    const retry = en ? 'Retry this page' : 'أعد محاولة فتح الصفحة';
    return new Response(`<!doctype html><html lang="${en ? 'en' : 'ar'}" dir="${en ? 'ltr' : 'rtl'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title} | Serveza</title>
    <style>body{margin:0;background:#f6f8fa;color:#0f2a3d;font:1.15rem/1.7 system-ui}main{max-width:34rem;margin:14vh auto;padding:2rem}b{color:#286e64}a{display:inline-block;background:#0f2a3d;color:white;padding:.65em 1.1em;border-radius:.6em}</style><main><b>Serveza</b><h1>${title}</h1><p>${copy}</p><a href="">${retry}</a></main></html>`,
      {status:503, headers:{'Content-Type':'text/html; charset=UTF-8', 'Cache-Control':'no-store'}});
  }));
});
