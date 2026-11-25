(() => {
  const form = document.getElementById('menuBrandingForm');
  if (!form) return;
  const preview = document.getElementById('menuBrandingPreview');
  const defaults = JSON.parse(document.getElementById('menu-branding-defaults').textContent);
  const fields = Object.fromEntries([...form.querySelectorAll('[data-menu-color]')].map(field => [field.dataset.menuColor, field]));
  const english = document.documentElement.lang.startsWith('en');
  const hint = document.getElementById('menuContrastHint');
  const originalHint = hint.textContent;
  function luminance(color) {
    const channels = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255);
    return channels.map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
      .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
  }
  function contrast(a, b) {
    const [light, dark] = [luminance(a), luminance(b)].sort((a, b) => b - a);
    return (light + .05) / (dark + .05);
  }
  function mix(a, b, weight) {
    return '#' + [1, 3, 5].map(i => Math.round(parseInt(a.slice(i, i + 2), 16) * weight + parseInt(b.slice(i, i + 2), 16) * (1 - weight)).toString(16).padStart(2, '0')).join('');
  }
  function update() {
    const colors = Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value.toLowerCase()]));
    const primary = colors.menu_primary_color, background = colors.menu_background_color, text = colors.menu_text_color;
    const surface = mix(background, luminance(background) > luminance(text) ? '#ffffff' : '#000000', .3);
    let muted = text;
    for (const weight of [.75, .8, .85, .9, .95]) {
      const candidate = mix(text, background, weight);
      if (Math.min(contrast(candidate, background), contrast(candidate, surface)) >= 4.5) { muted = candidate; break; }
    }
    const theme = {primary, background, text, surface, muted, border:mix(text, background, .25), soft:mix(primary, background, .10),
      'on-primary':contrast(primary, '#ffffff') >= contrast(primary, '#000000') ? '#ffffff' : '#000000'};
    Object.entries(theme).forEach(([key, value]) => preview.style.setProperty('--menu-' + key, value));
    Object.entries(colors).forEach(([key, value]) => form.querySelector('[data-color-value="' + key + '"]').value = value);
    const invalid = contrast(text, background) < 4.5;
    const message = english ? 'Text and background are too similar. Choose clearer colors (at least 4.5:1 contrast).' : 'لون النص قريب من الخلفية. اختر لونين أوضح (تباين 4.5:1 على الأقل).';
    fields.menu_text_color.setCustomValidity(invalid ? message : '');
    hint.textContent = invalid ? message : originalHint;
    hint.classList.toggle('form-errors', invalid);
  }
  Object.values(fields).forEach(field => field.addEventListener('input', update));
  document.getElementById('resetMenuColors').addEventListener('click', () => {
    Object.entries(defaults).forEach(([key, value]) => fields[key].value = value);
    update();
  });
  const upload = form.querySelector('[data-menu-logo]');
  const remove = form.querySelector('[data-menu-remove-logo]');
  const image = document.getElementById('brandingPreviewLogo');
  const fallback = document.getElementById('brandingPreviewFallback');
  const savedLogo = image.getAttribute('src');
  image.addEventListener('error', () => { image.hidden = true; fallback.hidden = false; });
  let objectUrl;
  function showLogo(url) {
    image.hidden = !url;
    fallback.hidden = !!url;
    if (url) image.src = url;
    else image.removeAttribute('src');
  }
  upload.addEventListener('change', () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = upload.files[0] ? URL.createObjectURL(upload.files[0]) : null;
    if (objectUrl) remove.checked = false;
    showLogo(remove.checked ? null : objectUrl || savedLogo);
  });
  remove.addEventListener('change', () => {
    if (remove.checked) {
      upload.value = '';
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    }
    showLogo(remove.checked ? null : savedLogo);
  });
  window.addEventListener('pagehide', () => { if (objectUrl) URL.revokeObjectURL(objectUrl); });
  update();
})();
