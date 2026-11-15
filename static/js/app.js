/* 全局小交互 */
(function () {
  // 防止重复提交
  document.querySelectorAll('form').forEach((f) => {
    f.addEventListener('submit', () => {
      const btn = f.querySelector('button[type="submit"]');
      if (btn) {
        setTimeout(() => { btn.disabled = true; }, 0);
        setTimeout(() => { btn.disabled = false; }, 2500);
      }
    });
  });

  // 表格内的空值统一灰显
  document.querySelectorAll('dd, td').forEach((el) => {
    if (el.textContent.trim() === '—' || el.textContent.trim() === '') {
      el.classList.add('muted');
    }
  });
})();
