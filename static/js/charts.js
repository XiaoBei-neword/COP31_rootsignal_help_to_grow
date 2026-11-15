/* 手写 SVG 图表：不依赖任何 CDN，断网也能渲染。
   用法：RS.chart.riskTimeline(el, daily) / waterBalance / harvestWindow */
(function (global) {
  const COLOR = {
    low: '#46E39B',
    medium: '#FFC24B',
    high: '#FF7F50',
    critical: '#FF4D6D',
    grid: 'rgba(255,255,255,.10)',
    axis: 'rgba(124,255,178,.45)',
    ink: '#DCEFE4',
    ink3: '#74909F',
    tmax: '#FF9999',
    tmin: '#2FE8FF',
    etc: '#00E86A',
    pe: '#2FE8FF',
    wheat: '#FFC24B',
    band: 'rgba(0,255,65,.06)',
  };
  const FONT = '"Microsoft YaHei","PingFang SC",system-ui,sans-serif';

  const NS = 'http://www.w3.org/2000/svg';

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
    ));
  }

  function parts(dateStr) {
    const [y, m, d] = String(dateStr).split('-').map(Number);
    const wd = new Date(y, (m || 1) - 1, d || 1).getDay();
    return { m, d, wd };
  }

  const WD = ['日', '一', '二', '三', '四', '五', '六'];

  function svgWrap(w, h, inner) {
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" xmlns="' + NS + '">' + inner + '</svg>';
  }

  function text(x, y, content, opt) {
    opt = opt || {};
    return '<text x="' + x + '" y="' + y + '" font-family=\'' + FONT + '\' font-size="' + (opt.size || 11) +
      '" fill="' + (opt.fill || COLOR.ink3) + '" text-anchor="' + (opt.anchor || 'middle') +
      '"' + (opt.weight ? ' font-weight="' + opt.weight + '"' : '') + '>' + esc(content) + '</text>';
  }

  /* ---------------------------------------------------------------- 风险曲线 */
  function riskTimeline(el, daily) {
    if (!el || !daily || !daily.length) return;
    const W = 920, H = 300;
    const pad = { l: 42, r: 44, t: 30, b: 46 };
    const iw = W - pad.l - pad.r;
    const ih = H - pad.t - pad.b;
    const n = daily.length;
    const band = iw / n;

    const temps = daily.flatMap((d) => [d.tmax, d.tmin]);
    let tMin = Math.min.apply(null, temps), tMax = Math.max.apply(null, temps);
    const tSpan = Math.max(4, tMax - tMin);
    tMin = Math.floor(tMin - tSpan * 0.35);
    tMax = Math.ceil(tMax + tSpan * 0.25);
    const yT = (v) => pad.t + ih - ((v - tMin) / (tMax - tMin)) * ih;
    const yR = (v) => pad.t + ih - (Math.max(0, Math.min(100, v)) / 100) * ih;

    let out = '';

    // 网格与左轴（风险 0–100）
    [0, 25, 50, 75, 100].forEach((v) => {
      const y = yR(v);
      out += '<line x1="' + pad.l + '" y1="' + y + '" x2="' + (W - pad.r) + '" y2="' + y +
        '" stroke="' + COLOR.grid + '" stroke-width="1"' + (v === 0 ? '' : ' stroke-dasharray="3 5"') + '/>';
      out += text(pad.l - 8, y + 4, v, { anchor: 'end', size: 9.5 });
    });
    out += text(pad.l - 8, pad.t - 12, '风险', { anchor: 'end', size: 9.5 });

    // 右轴（温度）
    out += text(W - pad.r + 8, pad.t - 12, '℃', { anchor: 'start', size: 9.5 });
    const tTicks = 4;
    for (let i = 0; i <= tTicks; i++) {
      const v = tMin + ((tMax - tMin) * i) / tTicks;
      out += text(W - pad.r + 8, yT(v) + 3.5, Math.round(v), { anchor: 'start', size: 9.5 });
    }

    // 逐日柱
    daily.forEach((d, i) => {
      const x = pad.l + i * band + band * 0.5;
      const bw = Math.min(34, band * 0.42);
      const y = yR(d.score);
      const col = COLOR[d.css] || COLOR.low;
      out += '<rect x="' + (x - bw / 2) + '" y="' + y + '" width="' + bw + '" height="' +
        Math.max(2, pad.t + ih - y) + '" rx="3.5" fill="' + col + '" opacity="0.9">' +
        '<title>' + esc(d.date) + ' 风险 ' + d.score + '（' + d.level + '）· ' +
        esc(d.text || '') + ' · 降水 ' + d.precip + 'mm · ' + d.tmax + '/' + d.tmin + '℃</title></rect>';
      out += text(x, y - 6, Math.round(d.score), { size: 9.5, fill: col, weight: 700 });
    });

    // 温度折线
    const line = (key, color) => {
      const pts = daily.map((d, i) => (pad.l + i * band + band * 0.5) + ',' + yT(d[key])).join(' ');
      let s = '<polyline points="' + pts + '" fill="none" stroke="' + color +
        '" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity="0.85"/>';
      daily.forEach((d, i) => {
        const cx = pad.l + i * band + band * 0.5;
        s += '<circle cx="' + cx + '" cy="' + yT(d[key]) + '" r="2.6" fill="#001A2B" stroke="' + color +
          '" stroke-width="1.5"><title>' + (key === 'tmax' ? '最高 ' : '最低 ') + d[key] + '℃</title></circle>';
      });
      return s;
    };
    out += line('tmax', COLOR.tmax);
    out += line('tmin', COLOR.tmin);

    // x 轴日期
    daily.forEach((d, i) => {
      const p = parts(d.date);
      const cx = pad.l + i * band + band * 0.5;
      out += text(cx, H - pad.b + 17, p.m + '/' + p.d,
        { size: 10.5, fill: COLOR.ink, weight: 600 });
      out += text(cx, H - pad.b + 31, '周' + WD[p.wd], { size: 9.5 });
    });

    // 图例
    out += '<line x1="' + (W - 168) + '" y1="14" x2="' + (W - 152) + '" y2="14" stroke="' + COLOR.tmax +
      '" stroke-width="2"/>' + text(W - 148, 17, '最高温', { anchor: 'start', size: 10 });
    out += '<line x1="' + (W - 98) + '" y1="14" x2="' + (W - 82) + '" y2="14" stroke="' + COLOR.tmin +
      '" stroke-width="2"/>' + text(W - 78, 17, '最低温', { anchor: 'start', size: 10 });

    el.innerHTML = svgWrap(W, H, out);
  }

  /* ---------------------------------------------------------------- 水分平衡 */
  function waterBalance(el, daily) {
    if (!el || !daily || !daily.length) return;
    const W = 920, H = 210;
    const pad = { l: 38, r: 16, t: 18, b: 40 };
    const iw = W - pad.l - pad.r;
    const ih = H - pad.t - pad.b;
    const n = daily.length;
    const band = iw / n;
    const peak = Math.max.apply(null, daily.map((d) => Math.max(d.etc, d.pe, 1)));
    const maxV = Math.ceil(peak / 2) * 2 + 2;
    const y = (v) => pad.t + ih - (v / maxV) * ih;

    let out = '';
    [0, 0.5, 1].forEach((f) => {
      const v = maxV * f;
      out += '<line x1="' + pad.l + '" y1="' + y(v) + '" x2="' + (W - pad.r) + '" y2="' + y(v) +
        '" stroke="' + COLOR.grid + '"' + (f === 0 ? '' : ' stroke-dasharray="3 5"') + '/>';
      out += text(pad.l - 7, y(v) + 3.5, Math.round(v), { anchor: 'end', size: 9.5 });
    });

    daily.forEach((d, i) => {
      const cx = pad.l + i * band + band * 0.5;
      const bw = Math.min(13, band * 0.26);
      const etcH = Math.max(1, (d.etc / maxV) * ih);
      const peH = d.pe > 0 ? Math.max(1, (d.pe / maxV) * ih) : 0;
      out += '<rect x="' + (cx - bw - 2) + '" y="' + (pad.t + ih - etcH) + '" width="' + bw +
        '" height="' + etcH + '" rx="2" fill="' + COLOR.etc + '" opacity="0.85">' +
        '<title>' + esc(d.date) + ' 作物需水 ETc ' + d.etc + ' mm</title></rect>';
      if (peH) {
        out += '<rect x="' + (cx + 2) + '" y="' + (pad.t + ih - peH) + '" width="' + bw +
          '" height="' + peH + '" rx="2" fill="' + COLOR.pe + '" opacity="0.85">' +
          '<title>' + esc(d.date) + ' 有效降水 Pe ' + d.pe + ' mm</title></rect>';
      }
      const p = parts(d.date);
      out += text(cx, H - pad.b + 16, p.m + '/' + p.d, { size: 10, fill: COLOR.ink });
      if (d.deficit > 4) {
        out += text(cx, pad.t + ih - Math.max(etcH, peH) - 5, '−' + d.deficit,
          { size: 9, fill: COLOR.high, weight: 700 });
      }
    });

    out += '<rect x="' + (W - 176) + '" y="6" width="9" height="9" rx="2" fill="' + COLOR.etc + '"/>' +
      text(W - 162, 14, '作物需水 ETc', { anchor: 'start', size: 10 });
    out += '<rect x="' + (W - 88) + '" y="6" width="9" height="9" rx="2" fill="' + COLOR.pe + '"/>' +
      text(W - 74, 14, '有效降水', { anchor: 'start', size: 10 });

    el.innerHTML = svgWrap(W, H, out);
  }

  /* ---------------------------------------------------------------- 收获窗口 */
  function harvestWindow(el, cands, bestDate) {
    if (!el || !cands || !cands.length) return;
    const rows = cands.slice(0, 16);
    const rowH = 30;
    const W = 920;
    const H = rows.length * rowH + 46;
    const pad = { l: 104, r: 96, t: 16, b: 30 };
    const iw = W - pad.l - pad.r;
    const maxScore = Math.max.apply(null, rows.map((r) => r.score).concat([30]));
    const x = (v) => pad.l + (Math.min(v, maxScore) / maxScore) * iw;

    let out = '';
    // 阈值参考线：评分 8（适宜上限）、22（可作业上限）
    [8, 22].forEach((v) => {
      const gx = x(v);
      out += '<line x1="' + gx + '" y1="' + pad.t + '" x2="' + gx + '" y2="' + (H - pad.b) +
        '" stroke="' + COLOR.grid + '" stroke-dasharray="4 5"/>';
      out += text(gx, H - pad.b + 17, v === 8 ? '适宜线 8' : '可作业线 22', { size: 9.5 });
    });

    rows.forEach((r, i) => {
      const cy = pad.t + i * rowH + rowH / 2;
      const isBest = r.date === bestDate;
      const col = r.suitability === '适宜' ? COLOR.low : (r.suitability === '可作业' ? COLOR.medium : COLOR.high);
      const p = parts(r.date);

      if (isBest) {
        out += '<rect x="4" y="' + (cy - rowH / 2 + 2) + '" width="' + (W - 8) + '" height="' + (rowH - 4) +
          '" rx="9" fill="rgba(0,255,65,.07)" stroke="rgba(0,255,65,.26)"/>';
      }
      out += text(pad.l - 12, cy + 4, p.m + '/' + p.d + ' 周' + WD[p.wd],
        { anchor: 'end', size: 10.5, fill: isBest ? COLOR.ink : COLOR.ink3, weight: isBest ? 700 : 400 });

      const bw = Math.max(6, x(r.score) - pad.l);
      out += '<rect x="' + pad.l + '" y="' + (cy - 8) + '" width="' + bw + '" height="16" rx="4" fill="' +
        col + '" opacity="' + (r.suitability === '不宜' ? 0.92 : 0.8) + '">' +
        '<title>' + esc(r.date) + ' 不适合作业评分 ' + r.score + '（' + r.suitability + '）' +
        (r.notes.length ? ' · ' + esc(r.notes.join('、')) : '') + '</title></rect>';

      out += text(pad.l + bw + 10, cy + 4,
        (isBest ? '★ 最佳 · ' : '') + r.suitability + (r.notes.length ? ' · ' + r.notes.join('/') : ''),
        { anchor: 'start', size: 9.5, fill: isBest ? COLOR.ink : COLOR.ink3, weight: isBest ? 700 : 400 });
    });

    el.innerHTML = svgWrap(W, H, out);
  }

  global.RS = global.RS || {};
  global.RS.chart = { riskTimeline, waterBalance, harvestWindow };
})(window);
