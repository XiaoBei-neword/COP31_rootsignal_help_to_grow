/* 灌溉决策 Agent 前端：加载态 / 结果渲染 / 失败提示与降级 */
(function () {
  const panel = document.getElementById('agentPanel');
  if (!panel) return;

  const idle = document.getElementById('agentIdle');
  const loading = document.getElementById('agentLoading');
  const resultBox = document.getElementById('agentResult');
  const errorBox = document.getElementById('agentError');
  const runBtn = document.getElementById('runAgent');
  const ruleBtn = document.getElementById('runRule');

  const chatBox = document.getElementById('agentChat');
  const chatLog = document.getElementById('chatLog');
  const chatForm = document.getElementById('chatForm');
  const chatInput = document.getElementById('chatInput');
  const chatSend = document.getElementById('chatSend');
  const chatReset = document.getElementById('chatReset');
  const chatSuggest = document.getElementById('chatSuggest');

  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
  ));

  let stepTimer = null;

  function startLoading() {
    idle.hidden = true;
    resultBox.hidden = true;
    errorBox.hidden = true;
    loading.hidden = false;
    const items = loading.querySelectorAll('.loading-steps li');
    items.forEach((li) => li.classList.remove('done'));
    let i = 0;
    clearInterval(stepTimer);
    stepTimer = setInterval(() => {
      if (i < items.length) {
        items[i].classList.add('done');
        i += 1;
      } else {
        clearInterval(stepTimer);
      }
    }, 900);
  }

  function stopLoading() {
    clearInterval(stepTimer);
    loading.hidden = true;
  }

  function setBusy(busy) {
    [runBtn, ruleBtn].forEach((b) => { if (b) b.disabled = busy; });
  }

  function renderResult(data, note) {
    const isHold = /暂缓/.test(data.decision || '');
    const srcLabel = data.source === 'llm'
      ? '大模型 · ' + esc(data.model || '')
      : '本地规则引擎';

    const reasons = (data.reasons || []).map((r) => '<li>' + esc(r) + '</li>').join('');
    const risks = (data.risks || []).map((r) => '<li>' + esc(r) + '</li>').join('');

    resultBox.innerHTML =
      '<div class="result-head">' +
        '<h3>' + esc(data.decision) + '</h3>' +
        '<span class="result-decision ' + (isHold ? 'hold' : '') + '">置信度 ' + esc(data.confidence) + '</span>' +
        '<span class="src-tag ' + (data.source === 'llm' ? 'llm' : 'rule') + '">' + srcLabel + '</span>' +
      '</div>' +
      '<div class="result-amounts">' +
        '<div><span>净灌水量</span><b>' + esc(data.net_mm) + '<small>mm</small></b></div>' +
        '<div><span>毛灌水量</span><b>' + esc(data.gross_mm) + '<small>mm</small></b></div>' +
        '<div><span>每亩用水</span><b>' + esc(data.m3_per_mu) + '<small>m³/亩</small></b></div>' +
      '</div>' +
      '<div class="result-meta">' +
        '<span>时段：' + esc(data.timing) + '</span>' +
        '<span>地块总用水：' + esc(data.total_m3) + ' m³</span>' +
      '</div>' +
      (note ? '<div class="banner banner-warn" style="margin:14px 0 0">' + esc(note) + '</div>' : '') +
      '<div class="result-block"><h4>决策理由</h4><ul>' + reasons + '</ul></div>' +
      (risks ? '<div class="result-block risks"><h4>风险提示</h4><ul>' + risks + '</ul></div>' : '') +
      '<div class="result-block"><h4>下次复查</h4><p style="font-size:12.5px;color:#43564c;margin:0">' +
        esc(data.next_check) + '</p></div>';

    resultBox.hidden = false;
    const rect = panel.getBoundingClientRect();
    if (rect.top < 0 || rect.top > window.innerHeight * 0.55) {
      panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function renderError(message, hint, fallback, retryMode) {
    errorBox.innerHTML =
      '<b>生成失败</b>' + esc(message) +
      (hint ? '<div style="margin-top:8px;font-size:12px;opacity:.9">' + esc(hint) + '</div>' : '') +
      (retryMode ? '<button class="btn" id="retryRule">改用本地规则引擎</button>' : '');
    errorBox.hidden = false;
    if (retryMode) {
      const btn = document.getElementById('retryRule');
      if (btn) btn.addEventListener('click', () => run('rule'));
    }
    if (fallback) {
      renderResult(fallback, '以上数值来自本地规则引擎（未调用大模型），可继续演示流程。');
    }
  }

  /* --------------------------- 多轮追问 --------------------------- */
  let chatHistory = [];
  let chatDecision = null;
  let chatBusy = false;

  function chatHint(text) {
    chatLog.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'chat-hint';
    div.textContent = text;
    chatLog.appendChild(div);
  }

  function appendMsg(role, text) {
    const row = document.createElement('div');
    row.className = 'chat-msg ' + role;
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble';
    bubble.innerHTML = esc(text).replace(/\n/g, '<br>');
    row.appendChild(bubble);
    chatLog.appendChild(row);
    chatLog.scrollTop = chatLog.scrollHeight;
    return row;
  }

  function openChat(decision) {
    if (!chatBox) return;
    chatDecision = decision || null;
    chatHistory = [];
    chatBox.hidden = false;
    chatHint('已围绕这条建议开启对话。点下面的问题可快速提问，也可以自己输入。');
    chatInput.value = '';
    chatInput.disabled = false;
    chatSend.disabled = false;
  }

  async function sendChat(text) {
    if (!chatForm || chatBusy) return;
    const q = String(text == null ? '' : text).trim();
    if (!q) { chatInput.focus(); return; }

    chatBusy = true;
    chatSend.disabled = true;
    chatInput.disabled = true;
    chatInput.value = '';
    const hint = chatLog.querySelector('.chat-hint');
    if (hint) hint.remove();
    appendMsg('user', q);
    const pending = appendMsg('assistant', '正在思考…');
    pending.classList.add('pending');

    try {
      const resp = await fetch(chatForm.dataset.url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: q,
          history: chatHistory.slice(-12),
          decision: chatDecision || {},
        }),
      });
      let data = {};
      try { data = await resp.json(); } catch (e) { data = {}; }
      pending.remove();
      if (!resp.ok || !data.ok) {
        appendMsg('error', data.error || ('请求失败（HTTP ' + resp.status + '）'));
      } else {
        appendMsg('assistant', data.answer);
        chatHistory.push({ role: 'user', content: q });
        chatHistory.push({ role: 'assistant', content: data.answer });
      }
    } catch (err) {
      pending.remove();
      appendMsg('error', '网络或前端异常：' + err.message);
    } finally {
      chatBusy = false;
      chatSend.disabled = false;
      chatInput.disabled = false;
      chatInput.focus();
    }
  }

  if (chatForm) {
    chatForm.addEventListener('submit', (e) => {
      e.preventDefault();
      sendChat(chatInput.value);
    });
  }
  if (chatSuggest) {
    chatSuggest.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-q]');
      if (btn) sendChat(btn.dataset.q);
    });
  }
  if (chatReset) {
    chatReset.addEventListener('click', () => {
      if (chatBusy) return;
      chatHistory = [];
      chatHint('对话已重置，可以重新问起。');
      chatInput.focus();
    });
  }

  async function run(mode) {
    const url = runBtn ? runBtn.dataset.url : null;
    if (!url) return;
    setBusy(true);
    startLoading();
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode || 'auto' }),
      });
      const data = await resp.json();
      stopLoading();
      if (!resp.ok || !data.ok) {
        renderError(data.error || ('请求失败（HTTP ' + resp.status + '）'), data.hint, data.fallback,
          mode !== 'rule');
      } else {
        errorBox.hidden = true;
        renderResult(data.result, data.note);
        if (data.result && data.result.source === 'llm') openChat(data.result);
        else if (chatBox) chatBox.hidden = true;
      }
    } catch (err) {
      stopLoading();
      renderError('网络或前端异常：' + err.message, '检查 Flask 终端是否有报错。', null, true);
    } finally {
      setBusy(false);
    }
  }

  if (runBtn) runBtn.addEventListener('click', () => run('auto'));
  if (ruleBtn) ruleBtn.addEventListener('click', () => run('rule'));

  // 演示便利：/irrigation?autorun=1 自动跑一次（现场讲解时省一次点击）
  if (new URLSearchParams(location.search).get('autorun') === '1' && runBtn) {
    setTimeout(() => run('auto'), 350);
  }
})();
