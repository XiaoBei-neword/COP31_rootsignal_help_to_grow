/* 设置页：地点检索 + 作物参数联动 */
(function () {
  const input = document.getElementById('geoInput');
  const btn = document.getElementById('geoBtn');
  const box = document.getElementById('geoResults');
  if (!input || !btn || !box) return;

  function render(results) {
    if (!results.length) {
      box.innerHTML = '<div style="padding:10px 13px;font-size:12.5px;color:#7c8a81">没有找到，直接填经纬度也行。</div>';
      box.hidden = false;
      return;
    }
    box.innerHTML = results.map((r) => (
      '<button type="button" data-lat="' + r.latitude + '" data-lon="' + r.longitude +
      '" data-name="' + (r.name || '').replace(/"/g, '&quot;') + '">' +
      r.name + '<small>' + (r.label || '') + '</small></button>'
    )).join('');
    box.hidden = false;
    box.querySelectorAll('button').forEach((b) => {
      b.addEventListener('click', () => {
        document.getElementById('lat').value = Number(b.dataset.lat).toFixed(4);
        document.getElementById('lon').value = Number(b.dataset.lon).toFixed(4);
        const nameField = document.getElementById('locName');
        if (!nameField.value) nameField.value = b.dataset.name;
        box.hidden = true;
        input.value = b.dataset.name;
      });
    });
  }

  async function search() {
    const q = input.value.trim();
    if (!q) return;
    btn.disabled = true;
    btn.textContent = '搜索中…';
    try {
      const resp = await fetch('/api/geocode?q=' + encodeURIComponent(q));
      const data = await resp.json();
      render(data.results || []);
    } catch (err) {
      box.innerHTML = '<div style="padding:10px 13px;font-size:12.5px;color:#a43a2a">检索失败：' +
        err.message + '</div>';
      box.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = '搜索';
    }
  }

  btn.addEventListener('click', search);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); search(); }
  });

  // 作物切换 → 参数卡即时刷新
  const preview = document.getElementById('paramPreview');
  document.querySelectorAll('input[name="crop_key"]').forEach((radio) => {
    radio.addEventListener('change', async () => {
      try {
        const resp = await fetch('/api/crop/' + radio.value);
        const data = await resp.json();
        const c = data.crop;
        if (!c || !preview) return;
        preview.querySelector('[data-p="growth_days"]').textContent = c.growth_days + ' 天';
        preview.querySelector('[data-p="heat_threshold"]').textContent = c.heat_threshold + ' ℃';
        preview.querySelector('[data-p="cold_threshold"]').textContent = c.cold_threshold + ' ℃';
        preview.querySelector('[data-p="flood_threshold"]').textContent = c.flood_threshold + ' mm/日';
        preview.querySelector('[data-p="yield"]').textContent =
          c.yield_kg_per_mu[0] + '–' + c.yield_kg_per_mu[1] + ' kg';
        preview.querySelector('[data-p="loss"]').textContent = c.postharvest_loss + '%';
      } catch (err) {
        /* 静默：预览失败不影响表单提交 */
      }
    });
  });
})();
