// ==UserScript==
// @name         툰 자동 다운로더 (toon_img)
// @namespace    https://github.com/sw3213/webtoon
// @version      5.1
// @description  id="toon_img" 이미지를 1번부터 순서대로 ZIP으로 저장하고 다음 화로 자동 이동. 이미 받은 화는 건너뜀.
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const box = document.getElementById('toon_img');
  if (!box) return;

  const KEY_DONE = 'toon_done';   // 받은 화 주소 목록
  const KEY_STOP = 'toon_stop';   // 중지 상태
  const pageKey = location.href.split('#')[0];
  const done = GM_getValue(KEY_DONE, {});
  const EXT = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/avif': 'avif' };

  // ---------- 작은 상태창
  const ui = document.createElement('div');
  ui.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:2147483647;background:#1d1c1a;color:#f1eee8;' +
    'padding:14px 16px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);font:13px/1.5 -apple-system,"Malgun Gothic",sans-serif;' +
    'min-width:240px;max-width:320px;border:1px solid #ff7a2e';
  ui.innerHTML = '<div id="tdMsg"></div><div style="margin-top:8px;display:flex;gap:6px"><button id="tdBtn"></button></div>';
  document.body.appendChild(ui);
  const msg = (h) => { ui.querySelector('#tdMsg').innerHTML = h; };
  const btn = ui.querySelector('#tdBtn');
  btn.style.cssText = 'border:0;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer;background:#ff7a2e;color:#1a0f08';

  const stopped = GM_getValue(KEY_STOP, false);
  btn.textContent = stopped ? '▶ 자동 다운로드 켜기' : '■ 중지';
  btn.onclick = () => {
    GM_setValue(KEY_STOP, !GM_getValue(KEY_STOP, false));
    location.reload();
  };
  if (stopped) { msg('⏸ 자동 다운로드 꺼짐'); return; }

  // ---------- 유틸
  const clean = (s) => (s || '').replace(/[\\/:*?"<>|\r\n\t]/g, '').replace(/\s+/g, ' ').trim().slice(0, 100);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  function nextUrl() {
    for (const a of document.querySelectorAll('a[href]')) {
      const t = (a.textContent + ' ' + (a.title || '')).trim();
      if (/다음\s*(화|회|편)|^다음$|next|▶/i.test(t) && !/이전|prev|◀|목록/i.test(t)) {
        const h = a.href;
        if (h && !h.startsWith('javascript:') && h.split('#')[0] !== pageKey) return h;
      }
    }
    return null;
  }

  function goNext() {
    const n = nextUrl();
    if (!n) { msg(ui.querySelector('#tdMsg').innerHTML + '<br>🎉 다음 화가 없습니다. 끝!'); return; }
    let sec = 3;
    const tick = () => {
      if (GM_getValue(KEY_STOP, false)) return;
      ui.querySelector('#tdNext').textContent = sec + '초 후 다음 화로 이동…';
      if (sec-- <= 0) location.href = n; else setTimeout(tick, 1000);
    };
    ui.querySelector('#tdMsg').insertAdjacentHTML('beforeend', '<div id="tdNext" style="color:#9b968c;margin-top:4px"></div>');
    tick();
  }

  // ZIP 직접 만들기 (무압축). 라이브러리 없이 동기적으로 만들어서 멈출 일이 없음
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(b) {
    let c = 0xFFFFFFFF;
    for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }
  function makeZip(files) {
    const enc = new TextEncoder(), parts = [], central = [];
    let offset = 0;
    for (const [name, data] of files) {
      const nm = enc.encode(name), crc = crc32(data);
      const h = new DataView(new ArrayBuffer(30));
      h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true);
      h.setUint32(14, crc, true); h.setUint32(18, data.length, true); h.setUint32(22, data.length, true);
      h.setUint16(26, nm.length, true);
      parts.push(new Uint8Array(h.buffer), nm, data);
      const c = new DataView(new ArrayBuffer(46));
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true);
      c.setUint32(16, crc, true); c.setUint32(20, data.length, true); c.setUint32(24, data.length, true);
      c.setUint16(28, nm.length, true); c.setUint32(42, offset, true);
      central.push(new Uint8Array(c.buffer), nm);
      offset += 30 + nm.length + data.length;
    }
    const csize = central.reduce((a, b) => a + b.length, 0);
    const e = new DataView(new ArrayBuffer(22));
    e.setUint32(0, 0x06054b50, true); e.setUint16(8, files.length, true); e.setUint16(10, files.length, true);
    e.setUint32(12, csize, true); e.setUint32(16, offset, true);
    return [...parts, ...central, new Uint8Array(e.buffer)];
  }

  // 이미지 받기: arraybuffer로 받아 이 스크립트 쪽 Uint8Array로 변환
  function getBytes(url) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET', url, responseType: 'arraybuffer', timeout: 30000,
        headers: { Referer: location.href },
        onload: (r) => {
          if (r.status !== 200 || !r.response) return reject(new Error('HTTP ' + r.status));
          const type = ((r.responseHeaders || '').match(/content-type:\s*([^;\r\n]+)/i) || [])[1] || '';
          resolve({ bytes: new Uint8Array(r.response), type: type.trim().toLowerCase() });
        },
        onerror: () => reject(new Error('네트워크 오류')),
        ontimeout: () => reject(new Error('시간 초과')),
      });
    });
  }

  function save(chunks, name) {
    const blob = new Blob(chunks, { type: 'application/zip' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.textContent = '⬇ 안 받아졌으면 여기 클릭';
    a.style.cssText = 'display:inline-block;margin-top:6px;color:#ff7a2e;font-weight:700';
    document.body.appendChild(a);
    a.click();
    return a;
  }

  // ---------- 실행
  const title = clean(document.title) || '웹툰';
  if (done[pageKey]) {
    msg('⏭ 이미 받은 화: ' + esc(done[pageKey]));
    goNext();
    return;
  }

  const urls = [];
  box.querySelectorAll('img').forEach((img) => {
    for (const k of ['data-src', 'data-original', 'data-url', 'data-lazy-src', 'src']) {
      const v = (img.getAttribute(k) || '').trim();
      if (!v || v.startsWith('data:') || (k === 'src' && /spacer|loading|blank/i.test(v))) continue;
      urls.push(new URL(v, location.href).href);
      return;
    }
  });
  if (!urls.length) { msg('❌ toon_img 안에서 이미지를 못 찾았어요.'); return; }

  (async () => {
    const total = urls.length, pad = Math.max(3, String(total).length), files = new Array(total);
    let cur = 0, ok = 0;
    const fails = [];
    msg(`📥 <b>${esc(title)}</b><br>받는 중 0 / ${total}`);

    async function worker() {
      while (cur < total) {
        const i = cur++;
        for (let t = 0; t < 3; t++) {
          try {
            const { bytes, type } = await getBytes(urls[i]);
            const m = urls[i].split(/[?#]/)[0].match(/\.(jpe?g|png|webp|gif|avif)$/i);
            const ext = EXT[type] || (m ? m[1].toLowerCase().replace('jpeg', 'jpg') : 'jpg');
            files[i] = [String(i + 1).padStart(pad, '0') + '.' + ext, bytes];
            ok++;
            break;
          } catch (e) {
            if (t === 2) { fails.push(i + 1); console.warn('[툰] 실패', i + 1, urls[i], e); }
          }
        }
        msg(`📥 <b>${esc(title)}</b><br>받는 중 ${ok} / ${total}` + (fails.length ? ` · 실패 ${fails.length}` : ''));
      }
    }
    await Promise.all(Array.from({ length: Math.min(4, total) }, worker));

    if (fails.length) {
      msg(`⚠️ <b>${esc(title)}</b><br>${fails.length}장 실패 (${fails.slice(0, 10).join(', ')}번)<br>새로고침하면 다시 시도합니다. 자동 이동 멈춤.`);
      return;
    }

    msg(`🗜 ZIP 만드는 중…`);
    await new Promise((r) => setTimeout(r, 50));   // 메시지가 화면에 보이도록
    const out = makeZip(files);   // 1번부터 순서대로
    const zipName = title + '.zip';
    msg(`✅ <b>${esc(zipName)}</b> 저장 (${total}장)`);
    ui.querySelector('#tdMsg').appendChild(save(out, zipName));

    done[pageKey] = zipName;
    GM_setValue(KEY_DONE, done);
    goNext();
  })().catch((e) => {
    console.error('[툰] 오류', e);
    msg('⚠️ 오류: ' + esc(e.message || e));
  });
})();
