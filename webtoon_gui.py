"""
웹툰 다운로더 - 화면(UI) 버전

실행하면 브라우저에 다운로더 화면이 열립니다.
다운로드는 이 컴퓨터의 Python이 직접 하고, 브라우저는 화면만 보여줍니다.
이 창(검은 창)을 닫으면 프로그램이 종료됩니다.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.chdir(os.path.dirname(os.path.abspath(__file__)))
from webtoon_downloader import DEFAULTS, Downloader, make_options  # noqa: E402

PREFERRED_PORT = 8517


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.worker = None
        self.dl = None
        self.out = DEFAULTS['out']
        self.gen = 0  # 시작할 때마다 1씩 증가 (화면이 새 작업을 구분하는 용도)

    @property
    def running(self):
        return bool(self.worker and self.worker.is_alive())

    def push(self, ev, data):
        with self.lock:
            # 이미지 진행 이벤트는 마지막 것만 남겨 목록이 너무 커지지 않게
            if ev == 'image' and self.events and self.events[-1]['ev'] == 'image':
                self.events[-1] = {'ev': ev, 'data': data, 'id': self.events[-1]['id']}
                return
            self.events.append({'ev': ev, 'data': data, 'id': len(self.events)})

    def since(self, k):
        with self.lock:
            # id가 바뀌지 않는 마지막 image 이벤트도 다시 보내기 위해 k-1 부터
            return [e for e in self.events if e['id'] >= max(0, k - 1)], len(self.events)

    def start(self, p):
        if self.running:
            return False, '이미 받는 중입니다.'
        url = (p.get('url') or '').strip()
        if not url:
            return False, '1화 링크를 넣어주세요.'
        try:
            n = int(p.get('n') or 0)
            delay = float(p.get('delay') or DEFAULTS['delay'])
        except ValueError:
            return False, '숫자를 확인해주세요.'
        opts = make_options(n=max(0, n), out=(p.get('out') or DEFAULTS['out']).strip(),
                            id=(p.get('id') or 'toon_img').strip(), next=p.get('next') or 'link',
                            delay=max(0.0, delay))
        self.out = opts.out
        with self.lock:
            self.events = []
            self.gen += 1
        self.dl = Downloader(opts, self.push)

        def job():
            try:
                self.dl.run(url)
            except Exception as e:  # 예상 못한 오류도 화면에 표시
                self.push('finished', {'new': 0, 'skipped': 0, 'partial': 0,
                                       'out': os.path.abspath(self.out), 'reason': f'오류: {e}', 'error': True})

        self.worker = threading.Thread(target=job, daemon=True)
        self.worker.start()
        return True, ''

    def stop(self):
        if self.dl:
            self.dl.stop()

    def open_folder(self):
        path = os.path.abspath(self.out)
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith('win'):
            os.startfile(path)  # noqa: S606 - 사용자가 누른 '폴더 열기'
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            return self.send(200, PAGE, 'text/html; charset=utf-8')
        if self.path.startswith('/api/events'):
            try:
                k = int(self.path.split('since=')[1])
            except (IndexError, ValueError):
                k = 0
            evs, nxt = APP.since(k)
            return self.send(200, {'events': evs, 'next': nxt, 'running': APP.running, 'gen': APP.gen})
        if self.path == '/api/defaults':
            return self.send(200, DEFAULTS)
        self.send(404, {'error': 'not found'})

    def do_POST(self):
        # 다른 사이트가 이 프로그램을 조작하지 못하게: 우리 화면에서 온 요청만 허용
        if self.headers.get('X-Toon') != '1':
            return self.send(403, {'error': 'forbidden'})
        length = int(self.headers.get('Content-Length') or 0)
        try:
            p = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            p = {}
        if self.path == '/api/start':
            ok, msg = APP.start(p)
            return self.send(200 if ok else 400, {'ok': ok, 'msg': msg, 'gen': APP.gen})
        if self.path == '/api/stop':
            APP.stop()
            return self.send(200, {'ok': True})
        if self.path == '/api/open':
            try:
                APP.open_folder()
                return self.send(200, {'ok': True})
            except Exception as e:
                return self.send(500, {'ok': False, 'msg': str(e)})
        self.send(404, {'error': 'not found'})


def free_port():
    for port in (PREFERRED_PORT, 0):
        try:
            with socket.socket() as s:
                s.bind(('127.0.0.1', port))
                return s.getsockname()[1]
        except OSError:
            continue
    return 0


PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>툰 다운로더</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>
  :root {
    --bg: #f4f2ee; --surface: #ffffff; --surface-2: #f8f7f4; --line: #e6e2da; --text: #1c1b19;
    --muted: #77736b; --accent: #e8590c; --accent-soft: #fff0e6; --accent-text: #ffffff;
    --ok: #2b8a3e; --ok-soft: #e9f7ec; --warn: #c27c0e; --warn-soft: #fff6e0; --bad: #d63939; --bad-soft: #fdecec;
    --skip: #6b7280; --skip-soft: #f0f1f3; --shadow: 0 1px 2px rgba(30,25,15,.04), 0 8px 24px rgba(30,25,15,.06);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #141312; --surface: #1d1c1a; --surface-2: #242320; --line: #33312d; --text: #f1eee8;
      --muted: #9b968c; --accent: #ff7a2e; --accent-soft: #3a2416; --accent-text: #1a0f08;
      --ok: #5cc777; --ok-soft: #173020; --warn: #f0b545; --warn-soft: #33290f; --bad: #ff6b6b; --bad-soft: #3a1a1a;
      --skip: #a0a4ad; --skip-soft: #2a2b2e; --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.25);
    }
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body { margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.55 "Pretendard", -apple-system, BlinkMacSystemFont, "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
    letter-spacing: -0.01em; }
  main { max-width: 760px; margin: 0 auto; padding: 40px 16px 80px; }

  header { display: flex; align-items: center; gap: 14px; margin-bottom: 28px; }
  .logo { width: 46px; height: 46px; border-radius: 14px; background: var(--accent); color: var(--accent-text);
    display: grid; place-items: center; box-shadow: var(--shadow); flex: none; }
  .logo svg { width: 24px; height: 24px; }
  h1 { font-size: 24px; margin: 0; letter-spacing: -0.03em; }
  .tagline { margin: 2px 0 0; color: var(--muted); font-size: 14px; }

  .card { background: var(--surface); border: 1px solid var(--line); border-radius: 20px; padding: 22px;
    box-shadow: var(--shadow); margin-bottom: 18px; }
  .label { display: block; font-size: 13px; font-weight: 600; color: var(--muted); margin-bottom: 8px; }
  .url-wrap { display: flex; gap: 8px; }
  input, select { width: 100%; padding: 13px 14px; border: 1.5px solid var(--line); border-radius: 12px;
    background: var(--surface-2); color: var(--text); font: inherit; outline: none; transition: border-color .15s, background .15s; }
  input:focus, select:focus { border-color: var(--accent); background: var(--surface); }
  #url { font-size: 16px; padding: 15px 16px; }
  .ghost { flex: none; padding: 0 16px; border-radius: 12px; border: 1.5px solid var(--line); background: var(--surface);
    color: var(--text); font: inherit; font-weight: 600; cursor: pointer; }
  .ghost:hover { border-color: var(--accent); color: var(--accent); }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 16px; }
  @media (max-width: 560px) { .grid { grid-template-columns: 1fr; } }
  details.more { margin-top: 16px; border-top: 1px dashed var(--line); padding-top: 14px; }
  details.more summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 600; list-style: none; }
  details.more summary::before { content: "＋ "; }
  details.more[open] summary::before { content: "－ "; }
  .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }

  .go { width: 100%; margin-top: 20px; padding: 16px; border: 0; border-radius: 14px; background: var(--accent);
    color: var(--accent-text); font: inherit; font-size: 17px; font-weight: 700; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 10px; transition: transform .08s, filter .15s; }
  .go:hover { filter: brightness(1.05); }
  .go:active { transform: scale(.99); }
  .go.stop { background: var(--text); color: var(--bg); }
  .go svg { width: 20px; height: 20px; }
  .err { color: var(--bad); font-size: 13px; margin-top: 10px; min-height: 0; }

  .now { display: none; }
  .now.show { display: block; }
  .now-top { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .ep-badge { font-size: 12px; font-weight: 700; color: var(--accent); background: var(--accent-soft);
    padding: 3px 9px; border-radius: 99px; white-space: nowrap; }
  .now-title { font-size: 18px; font-weight: 700; margin: 10px 0 2px; word-break: keep-all; overflow-wrap: anywhere; }
  .now-sub { color: var(--muted); font-size: 13px; }
  .bar { height: 10px; border-radius: 99px; background: var(--surface-2); border: 1px solid var(--line);
    overflow: hidden; margin: 14px 0 6px; }
  .bar i { display: block; height: 100%; width: 0; background: var(--accent); border-radius: 99px; transition: width .25s; }
  .bar.indet i { width: 35%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(290%); } }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 16px; }
  .stat { background: var(--surface-2); border-radius: 14px; padding: 12px 14px; }
  .stat b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
  .stat span { color: var(--muted); font-size: 12px; font-weight: 600; }
  .stat.ok b { color: var(--ok); } .stat.skip b { color: var(--skip); } .stat.warn b { color: var(--warn); }

  .done { display: none; margin-top: 16px; padding: 14px 16px; border-radius: 14px; background: var(--ok-soft);
    color: var(--text); font-size: 14px; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .done.show { display: flex; }
  .done.bad { background: var(--bad-soft); }
  .done .ghost { padding: 9px 14px; }

  .list-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .list-head h2 { font-size: 15px; margin: 0; }
  ul.eps { list-style: none; margin: 0; padding: 0; max-height: 420px; overflow: auto; }
  ul.eps li { display: flex; align-items: center; gap: 12px; padding: 11px 4px; border-top: 1px solid var(--line); }
  ul.eps li:first-child { border-top: 0; }
  .num { flex: none; width: 44px; font-weight: 700; font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; }
  .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  .chip { flex: none; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 99px; }
  .chip.ok { color: var(--ok); background: var(--ok-soft); }
  .chip.skip { color: var(--skip); background: var(--skip-soft); }
  .chip.partial { color: var(--warn); background: var(--warn-soft); }
  .chip.run { color: var(--accent); background: var(--accent-soft); }
  .empty { color: var(--muted); font-size: 14px; text-align: center; padding: 18px 0 6px; }
  .log { margin-top: 12px; font-size: 12px; color: var(--muted); }
  .log div { padding: 2px 0; } .log .warn { color: var(--warn); } .log .error { color: var(--bad); }
  footer { color: var(--muted); font-size: 12px; text-align: center; margin-top: 28px; }
</style>
</head>
<body>
<main>
  <header>
    <div class="logo" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
    </div>
    <div>
      <h1>툰 다운로더</h1>
      <p class="tagline">1화 링크만 넣으면 마지막 화까지, 화마다 ZIP으로 차곡차곡</p>
    </div>
  </header>

  <section class="card">
    <label class="label" for="url">1화 링크</label>
    <div class="url-wrap">
      <input id="url" type="url" placeholder="https://사이트주소/웹툰/1화" autocomplete="off" spellcheck="false">
      <button class="ghost" id="paste" type="button">붙여넣기</button>
    </div>

    <div class="grid">
      <div>
        <label class="label" for="n">몇 화까지</label>
        <input id="n" type="number" min="1" inputmode="numeric" placeholder="비우면 마지막 화까지">
      </div>
      <div>
        <label class="label" for="next">다음 화 찾기</label>
        <select id="next">
          <option value="link">'다음화' 버튼 따라가기</option>
          <option value="number">주소 숫자 +1</option>
          <option value="auto">버튼 → 없으면 숫자 +1</option>
        </select>
      </div>
    </div>

    <details class="more">
      <summary>고급 설정</summary>
      <div class="grid">
        <div>
          <label class="label" for="cid">이미지 영역 id</label>
          <input id="cid" type="text" value="toon_img" spellcheck="false">
        </div>
        <div>
          <label class="label" for="out">저장 폴더</label>
          <input id="out" type="text" value="웹툰다운로드" spellcheck="false">
        </div>
        <div>
          <label class="label" for="delay">화 사이 쉬는 시간(초)</label>
          <input id="delay" type="number" min="0" step="0.5" value="1.5">
        </div>
      </div>
      <p class="hint">이미 받은 화는 자동으로 건너뜁니다. 중간에 멈춰도 다시 시작하면 이어서 받아요.</p>
    </details>

    <button class="go" id="go" type="button">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
      <span>다운로드 시작</span>
    </button>
    <div class="err" id="err"></div>
  </section>

  <section class="card now" id="now">
    <div class="now-top">
      <span class="ep-badge" id="epBadge">준비 중</span>
      <span class="now-sub" id="imgCount"></span>
    </div>
    <div class="now-title" id="nowTitle">페이지 여는 중…</div>
    <div class="now-sub" id="nowUrl"></div>
    <div class="bar indet" id="bar"><i></i></div>
    <div class="stats">
      <div class="stat ok"><b id="sNew">0</b><span>새로 받음</span></div>
      <div class="stat skip"><b id="sSkip">0</b><span>이미 받음</span></div>
      <div class="stat warn"><b id="sPart">0</b><span>미완성</span></div>
    </div>
    <div class="done" id="done">
      <span id="doneMsg"></span>
      <button class="ghost" id="openFolder" type="button">📂 폴더 열기</button>
    </div>
    <div class="log" id="log"></div>
  </section>

  <section class="card" id="listCard">
    <div class="list-head"><h2>회차</h2><button class="ghost" id="openFolder2" type="button" style="padding:7px 12px;font-size:13px">📂 저장 폴더</button></div>
    <ul class="eps" id="eps"><li class="empty">시작하면 여기에 받은 회차가 쌓입니다.</li></ul>
  </section>

  <footer>이 창을 닫아도 다운로드는 계속됩니다 · 검은 창을 닫으면 프로그램이 종료돼요</footer>
</main>

<script>
const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
};
const FIELDS = ['url', 'n', 'next', 'cid', 'out', 'delay'];
FIELDS.forEach((f) => { const v = store.get('toon.' + f); if (v !== null && v !== '') $(f).value = v; });

let since = 0, gen = 0, running = false, stats = { new: 0, skip: 0, part: 0 }, rows = {};

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Toon': '1' }, body: JSON.stringify(body),
  });
  return res.json();
}

function setRunning(on) {
  running = on;
  $('go').classList.toggle('stop', on);
  $('go').querySelector('span').textContent = on ? '중지' : '다운로드 시작';
}

function resetView() {
  stats = { new: 0, skip: 0, part: 0 }; rows = {};
  ['sNew', 'sSkip', 'sPart'].forEach((id) => $(id).textContent = '0');
  $('eps').innerHTML = '';
  $('log').innerHTML = '';
  $('done').classList.remove('show', 'bad');
  $('now').classList.add('show');
  $('epBadge').textContent = '준비 중';
  $('nowTitle').textContent = '페이지 여는 중…';
  $('nowUrl').textContent = ''; $('imgCount').textContent = '';
  $('bar').classList.add('indet'); $('bar').firstElementChild.style.width = '';
}

function row(ep) {
  if (!rows[ep]) {
    const li = document.createElement('li');
    li.innerHTML = '<span class="num"></span><span class="t"></span><span class="chip"></span>';
    li.querySelector('.num').textContent = String(ep).padStart(3, '0');
    $('eps').prepend(li);
    rows[ep] = li;
  }
  return rows[ep];
}
function setRow(ep, title, chipCls, chipText) {
  const li = row(ep);
  if (title) { li.querySelector('.t').textContent = title; li.querySelector('.t').title = title; }
  const c = li.querySelector('.chip'); c.className = 'chip ' + chipCls; c.textContent = chipText;
}

function handle(e) {
  const d = e.data;
  if (e.ev === 'episode') {
    $('epBadge').textContent = d.ep + '화';
    $('nowUrl').textContent = d.url;
    if (d.title) {
      $('nowTitle').textContent = d.title;
      $('imgCount').textContent = '이미지 0 / ' + d.images;
      $('bar').classList.remove('indet'); $('bar').firstElementChild.style.width = '0%';
      setRow(d.ep, d.title, 'run', '받는 중');
    } else {
      $('nowTitle').textContent = '페이지 여는 중…';
      $('imgCount').textContent = '';
      $('bar').classList.add('indet');
    }
  } else if (e.ev === 'image') {
    $('imgCount').textContent = '이미지 ' + d.done + ' / ' + d.total;
    $('bar').classList.remove('indet');
    $('bar').firstElementChild.style.width = (d.done / d.total * 100) + '%';
  } else if (e.ev === 'episode_done') {
    if (d.status === 'ok') { stats.new++; setRow(d.ep, d.title, 'ok', '완료'); }
    else if (d.status === 'skip') { stats.skip++; setRow(d.ep, d.title, 'skip', '이미 받음'); }
    else { stats.part++; setRow(d.ep, d.title, 'partial', d.fails.length ? d.fails.length + '장 실패' : '중지됨'); }
    $('sNew').textContent = stats.new; $('sSkip').textContent = stats.skip; $('sPart').textContent = stats.part;
  } else if (e.ev === 'log') {
    if (d.level === 'warn' || d.level === 'error') {
      const div = document.createElement('div'); div.className = d.level; div.textContent = d.msg;
      $('log').prepend(div);
      while ($('log').children.length > 6) $('log').lastChild.remove();
    }
  } else if (e.ev === 'finished') {
    $('bar').classList.remove('indet');
    $('bar').firstElementChild.style.width = '100%';
    $('epBadge').textContent = '완료';
    $('nowTitle').textContent = d.error ? '문제가 생겼어요' : '끝났어요';
    $('doneMsg').textContent = d.reason + (d.partial ? ' 미완성 화는 다시 시작하면 빠진 이미지만 받아요.' : '');
    $('done').classList.add('show');
    $('done').classList.toggle('bad', !!d.error);
  }
}

async function poll() {
  try {
    const r = await api('/api/events?since=' + since);
    if (r.gen !== gen) {           // 새 작업이 시작됐거나 화면을 새로 열었음 → 처음부터 다시 그리기
      gen = r.gen; since = 0;
      if (gen) resetView();
      setTimeout(poll, 0);
      return;
    }
    r.events.forEach((e) => { if (e.id >= since || e.ev === 'image') handle(e); });
    since = r.next;
    setRunning(r.running);
  } catch (e) {
    $('err').textContent = '프로그램과 연결이 끊겼어요. 검은 창이 닫혔다면 실행.bat을 다시 실행하세요.';
    setTimeout(poll, 2000);
    return;
  }
  if ($('err').textContent.startsWith('프로그램과')) $('err').textContent = '';
  setTimeout(poll, running ? 400 : 1500);
}

$('go').addEventListener('click', async () => {
  $('err').textContent = '';
  if (running) { await api('/api/stop', {}); $('nowTitle').textContent = '멈추는 중…'; return; }
  const url = $('url').value.trim();
  if (!url) { $('err').textContent = '1화 링크를 넣어주세요.'; $('url').focus(); return; }
  FIELDS.forEach((f) => store.set('toon.' + f, $(f).value));
  resetView();
  since = 0;
  const r = await api('/api/start', {
    url, n: $('n').value, next: $('next').value, id: $('cid').value, out: $('out').value, delay: $('delay').value,
  });
  if (!r.ok) { $('err').textContent = r.msg; $('now').classList.remove('show'); return; }
  gen = r.gen; since = 0;
  setRunning(true);
});
$('url').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !running) $('go').click(); });
$('paste').addEventListener('click', async () => {
  try { $('url').value = (await navigator.clipboard.readText()).trim(); }
  catch (e) { $('url').focus(); $('err').textContent = 'Ctrl+V 로 붙여넣어 주세요.'; }
});
const openFolder = () => api('/api/open', {});
$('openFolder').addEventListener('click', openFolder);
$('openFolder2').addEventListener('click', openFolder);
poll();
</script>
</body>
</html>
"""


def main():
    port = free_port()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print('=' * 52)
    print('  툰 다운로더 화면이 브라우저에 열립니다.')
    print(f'  안 열리면 이 주소를 브라우저에 붙여넣으세요: {url}')
    print('  이 검은 창을 닫으면 프로그램이 종료됩니다.')
    print('=' * 52)
    if not os.environ.get('TOON_NO_BROWSER'):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
