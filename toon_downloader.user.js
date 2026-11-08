// ==UserScript==
// @name         툰 자동 다운로더
// @namespace    https://github.com/sw3213/webtoon
// @version      5.7
// @description  이미지 영역(toon_img / scroll-list)의 이미지를 1번부터 순서대로 폴더 또는 ZIP으로 저장하고 다음 화로 자동 이동. 이미 받은 화는 건너뜀.
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_download
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(async function () {
  'use strict';

  // ---------- 사이트별 설정 (여러 사이트를 한꺼번에 지원)
  const CONTAINER_IDS = ['toon_img', 'scroll-list'];            // 이미지가 들어있는 영역 id
  const NEXT_BUTTONS = ['#episode-nav li:nth-child(3) button'];  // '다음 화' 버튼 (있으면 우선 사용)

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const findBox = () => CONTAINER_IDS.map((id) => document.getElementById(id)).find((el) => el && el.querySelector('img'));
  // 이미지가 늦게 붙는 사이트: 최대 15초 기다림
  let box = findBox();
  for (let i = 0; !box && i < 30; i++) { await sleep(500); box = findBox(); }
  if (!box) return;
  // 이미지 개수가 더 늘지 않을 때까지 잠깐 기다림
  for (let i = 0, last = -1; i < 10; i++) {
    const n = box.querySelectorAll('img').length;
    if (n === last) break;
    last = n;
    await sleep(700);
  }

  const KEY_DONE = 'toon_done';   // 받은 화 주소 목록
  const KEY_STOP = 'toon_stop';   // 중지 상태
  const KEY_MODE = 'toon_mode';   // 'folder' 또는 'zip'
  const VERSION = '5.7';
  const pageKey = location.href.split('#')[0];
  const done = GM_getValue(KEY_DONE, {});
  const EXT = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/avif': 'avif' };

  // ---------- 작은 상태창
  const ui = document.createElement('div');
  ui.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:2147483647;background:#1d1c1a;color:#f1eee8;' +
    'padding:14px 16px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.5);font:13px/1.5 -apple-system,"Malgun Gothic",sans-serif;' +
    'min-width:240px;max-width:320px;border:1px solid #ff7a2e';
  ui.innerHTML = '<div id="tdMsg"></div><div style="margin-top:8px;display:flex;gap:6px;align-items:center">' +
    '<button id="tdBtn"></button><button id="tdMode"></button><button id="tdRe">↻ 다시 받기</button><button id="tdSpeed"></button>' +
    '<span style="margin-left:auto;color:#9b968c;font-size:11px">v' + VERSION + '</span></div>';
  document.body.appendChild(ui);
  const msg = (h) => { ui.querySelector('#tdMsg').innerHTML = h; };
  const btn = ui.querySelector('#tdBtn');
  btn.style.cssText = 'border:0;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer;background:#ff7a2e;color:#1a0f08';

  const mode = GM_getValue(KEY_MODE, 'folder');
  const modeBtn = ui.querySelector('#tdMode');
  modeBtn.style.cssText = 'border:1px solid #ff7a2e;border-radius:8px;padding:6px 10px;font-weight:700;cursor:pointer;background:transparent;color:#ff7a2e';
  modeBtn.textContent = mode === 'zip' ? '저장: ZIP' : '저장: 폴더';
  modeBtn.title = '눌러서 폴더/ZIP 바꾸기';
  modeBtn.onclick = () => { GM_setValue(KEY_MODE, mode === 'zip' ? 'folder' : 'zip'); location.reload(); };

  // 다시 받기: 이 화의 기록을 지우고 다시 받음 (자동 이동 없이)
  const reBtn = ui.querySelector('#tdRe');
  reBtn.style.cssText = modeBtn.style.cssText;
  reBtn.title = '이 화를 처음부터 다시 받기';
  reBtn.onclick = () => {
    const d = GM_getValue(KEY_DONE, {});
    delete d[pageKey];
    GM_setValue(KEY_DONE, d);
    GM_setValue(KEY_STOP, false);
    location.reload();
  };

  // 속도: 보통 = 4장씩, 3초 쉼 / 빠름 = 8장씩, 1초 쉼
  const fast = GM_getValue('toon_fast', false);
  const PARALLEL = fast ? 8 : 4, WAIT = fast ? 1 : 3;
  const spBtn = ui.querySelector('#tdSpeed');
  spBtn.style.cssText = modeBtn.style.cssText;
  spBtn.textContent = fast ? '속도: 빠름' : '속도: 보통';
  spBtn.onclick = () => { GM_setValue('toon_fast', !fast); spBtn.textContent = !fast ? '속도: 빠름 (다음 화부터)' : '속도: 보통 (다음 화부터)'; };
  ui.querySelector('#tdMsg').parentElement.lastElementChild.style.flexWrap = 'wrap';

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
    const BAD = /이전|prev|◀|‹|«|목록|back/i;
    const okUrl = (h) => h && !/^javascript:/i.test(h) && h.split('#')[0] !== pageKey;
    const textOf = (el) => [el.textContent, el.title, el.getAttribute('aria-label'),
      ...Array.from(el.querySelectorAll('img')).map((i) => i.alt)].join(' ').replace(/\s+/g, ' ').trim();
    const links = Array.from(document.querySelectorAll('a[href]'));

    const rel = document.querySelector('a[rel~="next"][href], link[rel~="next"][href]');
    if (rel && okUrl(rel.href)) return rel.href;
    // 1) 글자: 다음화/다음 회/다음편/Next/▶
    for (const a of links) {
      const t = textOf(a);
      if (/다음\s*(화|회|편|에피소드)|^다음$|^next|▶|›|»/i.test(t) && !BAD.test(t) && okUrl(a.href)) return a.href;
    }
    // 2) id/class 에 next (아이콘만 있는 버튼)
    for (const a of links) {
      const at = (a.id || '') + ' ' + (a.getAttribute('class') || '') + ' ' +
        Array.from(a.querySelectorAll('*')).map((c) => c.getAttribute('class') || '').join(' ');
      if (/next|right|forward/i.test(at) && !/prev|left|back/i.test(at) && okUrl(a.href)) return a.href;
    }
    // 3) onclick 으로 이동하는 버튼
    for (const el of document.querySelectorAll('[onclick]')) {
      const t = textOf(el) + ' ' + (el.id || '') + ' ' + (el.getAttribute('class') || '');
      if (!/다음|next/i.test(t) || BAD.test(t)) continue;
      const m = (el.getAttribute('onclick') || '').match(/['"]([^'"]*[/?][^'"]*)['"]/);
      if (m) { try { const u = new URL(m[1], location.href).href; if (okUrl(u)) return u; } catch (e) { /* 무시 */ } }
    }
    // 4) 느슨하게: '다음' 이 들어간 링크
    for (const a of links) {
      const t = textOf(a);
      if (/다음/.test(t) && !BAD.test(t) && t.length <= 15 && okUrl(a.href)) return a.href;
    }
    console.log('[툰] 다음 화 후보를 못 찾음. 다음/next 관련 요소:',
      Array.from(document.querySelectorAll('a,button,[onclick]')).filter((e) => /다음|next/i.test(e.outerHTML)).map((e) => e.outerHTML.slice(0, 200)));
    return null;
  }

  function nextButton() {
    for (const sel of NEXT_BUTTONS) {
      const b = document.querySelector(sel);
      if (b && !b.disabled && b.getAttribute('aria-disabled') !== 'true' && !/disabled/i.test(b.className)) return b;
    }
    return null;
  }

  function goNext() {
    const b = nextButton();
    const n = b ? null : nextUrl();
    if (!b && !n && NEXT_BUTTONS.some((sel) => document.querySelector(sel))) {
      msg(ui.querySelector('#tdMsg').innerHTML + '<br>🎉 마지막 화까지 받았어요. 끝!');   // 다음 화 버튼이 꺼져 있음
      return;
    }
    if (!b && !n) { msg(ui.querySelector('#tdMsg').innerHTML + '<br>🎉 다음 화 버튼을 못 찾아 끝냈어요. 끝!<br><span style="color:#9b968c">다음 화가 있다면 F12 → Console의 [툰] 줄을 캡처해 주세요</span>'); return; }
    let sec = WAIT;
    const tick = () => {
      if (GM_getValue(KEY_STOP, false)) return;
      ui.querySelector('#tdNext').textContent = sec + '초 후 다음 화로 이동…';
      if (sec-- > 0) { setTimeout(tick, 1000); return; }
      if (n) { location.href = n; return; }
      b.click();
      // 페이지가 새로 열리지 않고 화면만 바뀌는 사이트: 주소가 바뀌었으면 새로고침해서 다시 시작
      setTimeout(() => {
        if (location.href.split('#')[0] !== pageKey) location.reload();
        else ui.querySelector('#tdNext').textContent = '다음 화 버튼을 눌렀지만 넘어가지 않았어요. (마지막 화일 수 있어요)';
      }, 4000);
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

  // 폴더 저장: 다운로드 폴더/작품명/회차/001.jpg
  function series(t) {
    const m = t.match(/^(.*?)\s*(?:제?\s*\d+\s*(?:화|회|편)|ep\.?\s*\d+)/i);
    return clean((m && m[1].trim() ? m[1] : t).split(/\s[-|:]\s/)[0]) || '웹툰';
  }
  function gmDownload(url, name) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('시간 초과')), 90000);
      GM_download({
        url, name, saveAs: false, conflictAction: 'overwrite',
        headers: { Referer: location.href },
        onload: () => { clearTimeout(timer); resolve(); },
        onerror: (e) => { clearTimeout(timer); reject(new Error((e && (e.error || e.details)) || '다운로드 실패')); },
        ontimeout: () => { clearTimeout(timer); reject(new Error('시간 초과')); },
      });
    });
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
  const PLACEHOLDER = /spacer|loading|blank|placeholder/i;
  box.querySelectorAll('img').forEach((img) => {
    // 1) 이미 화면에 제대로 떠 있는 이미지는 실제로 보이는 주소를 그대로 사용
    const cur = img.currentSrc || img.src || '';
    if (img.complete && img.naturalWidth >= 100 && cur && !cur.startsWith('data:') && !PLACEHOLDER.test(cur)) {
      urls.push(cur);
      return;
    }
    // 2) 아직 안 뜬 이미지는 지연 로딩 속성에서 진짜 주소 찾기 (자리표시 주소는 건너뜀)
    for (const k of ['data-src', 'data-original', 'data-url', 'data-lazy-src', 'src']) {
      const v = (img.getAttribute(k) || '').trim();
      if (!v || v.startsWith('data:') || PLACEHOLDER.test(v)) continue;
      urls.push(new URL(v, location.href).href);
      return;
    }
    console.warn('[툰] 주소를 못 찾은 이미지:', img.outerHTML.slice(0, 300));
  });
  console.log('[툰] 이미지 ' + urls.length + '장', urls);
  if (!urls.length) { msg('❌ #' + esc(box.id) + ' 안에서 이미지를 못 찾았어요.'); return; }

  (async () => {
    const total = urls.length, pad = Math.max(3, String(total).length), files = new Array(total);
    let cur = 0, ok = 0;
    const fails = [];
    msg(`📥 <b>${esc(title)}</b><br>받는 중 0 / ${total}`);

    const folder = series(title) + '/' + title + '/';

    async function worker() {
      while (cur < total) {
        const i = cur++;
        for (let t = 0; t < 3; t++) {
          try {
            if (mode === 'folder') {
              const m = urls[i].split(/[?#]/)[0].match(/\.(jpe?g|png|webp|gif|avif)$/i);
              const name = folder + String(i + 1).padStart(pad, '0') + '.' + (m ? m[1].toLowerCase().replace('jpeg', 'jpg') : 'jpg');
              try {
                await gmDownload(urls[i], name);
              } catch (e) {
                // 주소로 직접 저장이 안 되면 내용을 받아서 저장
                const { bytes } = await getBytes(urls[i]);
                const bu = URL.createObjectURL(new Blob([bytes]));
                try { await gmDownload(bu, name); } finally { setTimeout(() => URL.revokeObjectURL(bu), 60000); }
              }
              ok++;
              break;
            }
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
    await Promise.all(Array.from({ length: Math.min(PARALLEL, total) }, worker));

    if (fails.length) {
      msg(`⚠️ <b>${esc(title)}</b><br>${fails.length}장 실패 (${fails.slice(0, 10).join(', ')}번)<br>새로고침하면 다시 시도합니다. 자동 이동 멈춤.`);
      return;
    }

    if (mode === 'folder') {
      msg(`✅ <b>${esc(title)}</b> 저장 (${total}장)<br><span style="color:#9b968c">다운로드/${esc(folder)}</span>`);
      done[pageKey] = folder;
      GM_setValue(KEY_DONE, done);
      goNext();
      return;
    }

    msg(`🗜 ZIP 만드는 중…`);
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
