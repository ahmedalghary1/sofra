(() => {
  const english=document.documentElement.lang.startsWith('en');
  const copy=(ar,en)=>english?en:ar;
  document.querySelectorAll('[data-geofence-editor]').forEach(panel=>{
    const form=panel.querySelector('form'),lat=form.querySelector('[name$="-latitude"]'),lon=form.querySelector('[name$="-longitude"]');
    const radius=form.querySelector('[name$="-geofence_radius_m"]'),status=panel.querySelector('[data-geofence-status]');
    const surface=panel.querySelector('[data-geofence-map]'),locate=panel.querySelector('[data-locate-branch]');
    let map,marker,circle;
    const point=()=>lat.value.trim()!==''&&lon.value.trim()!==''&&Number.isFinite(Number(lat.value))&&Number.isFinite(Number(lon.value))
      &&Math.abs(Number(lat.value))<=90&&Math.abs(Number(lon.value))<=180?[Number(lat.value),Number(lon.value)]:null;
    function preview(centre=false){
      if(!map)return;
      const p=point(),r=Number(radius.value);
      if(!p){if(marker){map.removeLayer(marker);map.removeLayer(circle);marker=circle=null;}return;}
      if(!marker){
        marker=L.marker(p,{draggable:true,icon:L.divIcon({className:'geofence-centre',iconSize:[22,22],iconAnchor:[11,11]}),
          title:copy('مركز الفرع: اسحب للتعديل','Branch centre: drag to edit'),alt:copy('مركز الفرع','Branch centre')}).addTo(map);
        marker.on('dragend',()=>choose(marker.getLatLng()));
        circle=L.circle(p,{radius:Number.isFinite(r)&&r>=10&&r<=5000?r:50,color:'#26786c',fillOpacity:0.14,interactive:false}).addTo(map);
      }
      marker.setLatLng(p);circle.setLatLng(p);
      if(Number.isFinite(r)&&r>=10&&r<=5000)circle.setRadius(r);
      if(centre)map.fitBounds(circle.getBounds(),{padding:[35,35],maxZoom:19,animate:false});
    }
    function choose(p){
      const wrapped=((p.lng+180)%360+360)%360-180;
      lat.value=Math.max(-90,Math.min(90,p.lat)).toFixed(6);lon.value=wrapped.toFixed(6);preview();
      status.textContent=copy('تم تحديث النقطة في المعاينة. احفظ لاعتمادها.','Preview point updated. Save to apply it.');
    }
    panel.querySelector('[data-open-geofence-map]').addEventListener('click',event=>{
      if(map){preview(true);return;}
      if(!window.L){status.textContent=copy('تعذر تحميل الخريطة. استخدم موقعك أو أدخل الإحداثيات يدويًا.','The map could not load. Use your location or enter coordinates manually.');return;}
      surface.hidden=false;panel.querySelector('[data-geofence-map-help]').hidden=false;
      map=L.map(surface,{scrollWheelZoom:false,zoomAnimation:false}).setView(point()||[20,0],point()?17:2);
      const tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
        maxZoom:19,referrerPolicy:'strict-origin-when-cross-origin',
        attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'}).addTo(map);
      tiles.on('tileerror',()=>{status.textContent=copy('تعذر تحميل خلفية الخريطة. يمكنك إدخال الإحداثيات أو استخدام موقعك ثم الحفظ.','Map tiles could not load. You can still enter coordinates or use your location and save.');});
      map.on('click',event=>choose(event.latlng));preview(true);
      event.currentTarget.textContent=copy('عرض النقطة والنطاق','Show point and radius');
    });
    [lat,lon,radius].forEach(input=>input.addEventListener('input',()=>preview()));
    locate.addEventListener('click',()=>{
      if(!window.isSecureContext||!navigator.geolocation){status.textContent=copy('تحديد الموقع يحتاج HTTPS ومتصفحًا يدعمه. يمكنك إدخال الإحداثيات يدويًا.','Location needs HTTPS and a supported browser. You can enter coordinates manually.');return;}
      locate.disabled=true;status.textContent=copy('جارٍ تحديد موقعك…','Finding your location…');
      navigator.geolocation.getCurrentPosition(position=>{
        locate.disabled=false;
        if(!Number.isFinite(position.coords.accuracy)||position.coords.accuracy>50){status.textContent=copy('دقة الموقع ضعيفة. فعّل الموقع الدقيق وأعد المحاولة أو حدد النقطة يدويًا.','Location accuracy is low. Enable precise location and retry, or set the point manually.');return;}
        choose({lat:position.coords.latitude,lng:position.coords.longitude});preview(true);
      },error=>{locate.disabled=false;status.textContent=error.code===1?
        copy('لم تسمح بتحديد الموقع. غيّر إذن الموقع أو أدخل الإحداثيات يدويًا.','Location permission denied. Change the permission or enter coordinates manually.'):
        copy('تعذر تحديد الموقع. أعد المحاولة أو أدخل الإحداثيات يدويًا.','Location could not be determined. Retry or enter coordinates manually.');
      },{enableHighAccuracy:true,maximumAge:0,timeout:15000});
    });
  });
})();
