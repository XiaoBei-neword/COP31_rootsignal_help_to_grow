/* 收获方案存档 */
(function () {
  const btn = document.getElementById('savePlan');
  if (!btn) return;
  const msg = document.getElementById('saveMsg');

  function show(cls, text) {
    msg.className = 'save-msg ' + cls;
    msg.textContent = text;
    msg.hidden = false;
  }

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    msg.hidden = true;
    try {
      const resp = await fetch(btn.dataset.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await resp.json();
      if (resp.ok && data.ok) {
        show('ok', '已保存：窗口 ' + data.window.start + ' — ' + data.window.end +
          '，预计减损 ' + Math.round(data.saved_kg) + ' kg。刷新页面可在「我的历史方案」看到。');
      } else {
        show('err', '保存失败：' + (data.error || ('HTTP ' + resp.status)));
      }
    } catch (err) {
      show('err', '保存失败：' + err.message);
    } finally {
      btn.disabled = false;
    }
  });
})();
