"""
웹툰 전 회차 자동 다운로더 (다운로드 엔진 + 명령 창 버전)

  - 각 화: <div id="toon_img"> 안 이미지를 1번부터 마지막까지 순서대로 받아 ZIP 하나로 저장
  - 1화부터 '다음화'를 따라가며 마지막 화(또는 N화)까지 자동으로 진행
  - 이미 받은 화는 건너뜀, 중간에 끊긴 화는 받은 이미지는 두고 나머지만 받음

화면(UI)으로 쓰려면 webtoon_gui.py (또는 실행.bat) 를 실행하세요.

명령 창 사용법:
  python webtoon_downloader.py "1화링크"              (마지막 화까지)
  python webtoon_downloader.py "1화링크" -n 30        (30화까지만)
  python webtoon_downloader.py -h                    (전체 옵션)
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # 처음 실행이면 필요한 라이브러리를 자동 설치
    print('필요한 라이브러리(requests, beautifulsoup4)를 설치합니다... (처음 한 번만)')
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', 'requests', 'beautifulsoup4'])
    except Exception:
        print('[!] 자동 설치 실패. 명령 창에서 직접 실행해 보세요:  py -m pip install requests beautifulsoup4')
        if not os.environ.get('TOON_BAT'):
            input('엔터를 누르면 창이 닫힙니다.')
        sys.exit(1)
    import requests
    from bs4 import BeautifulSoup

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')
IMG_ATTRS = ('data-src', 'data-original', 'data-url', 'data-lazy-src', 'src')
EXT_BY_TYPE = {'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
               'image/gif': 'gif', 'image/avif': 'avif', 'image/bmp': 'bmp'}
NEXT_TEXT = re.compile(r'다음\s*(화|회|편)|다음$|^다음|next|▶|›|»', re.I)
PREV_TEXT = re.compile(r'이전|prev|◀|‹|«|목록', re.I)
LOG_NAME = '_다운로드기록.json'

DEFAULTS = dict(n=0, out='웹툰다운로드', name='', id='toon_img', next='link',
                delay=1.5, workers=4, retries=3)


# ---------------------------------------------------------------- 유틸
def clean_name(s, limit=100):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', '', s or '')
    return re.sub(r'\s+', ' ', s).strip()[:limit]


def series_name_from(title):
    """'작품명 12화 - 사이트' → '작품명'"""
    m = re.match(r'^(.*?)\s*(?:제?\s*\d+\s*(?:화|회|편|話)|ep\.?\s*\d+|#\d+)', title or '', re.I)
    name = m.group(1) if m and m.group(1).strip() else (title or '')
    return clean_name(re.split(r'\s[-|:]\s', name)[0]) or '웹툰'


def load_log(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_log(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def make_options(**kw):
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in kw.items() if v is not None})
    return SimpleNamespace(**opts)


# ---------------------------------------------------------------- 다운로더
class Downloader:
    """
    on(event, data) 콜백으로 진행 상황을 알려줍니다.
      log            {msg, level}               level: info / ok / warn / error / skip
      episode        {ep, url, title, images}   한 화 시작
      image          {ep, done, total}          이미지 진행
      episode_done   {ep, status, zip, title, fails}   status: ok / skip / partial
      finished       {new, skipped, partial, out, reason}
    """

    def __init__(self, opts, on=None):
        self.a = opts
        self.on = on or (lambda ev, data: None)
        self.stop_event = threading.Event()
        self.s = requests.Session()
        self.s.headers['User-Agent'] = USER_AGENT
        adapter = requests.adapters.HTTPAdapter(pool_maxsize=opts.workers + 2)
        self.s.mount('https://', adapter)
        self.s.mount('http://', adapter)

    def stop(self):
        self.stop_event.set()

    def log(self, msg, level='info'):
        self.on('log', {'msg': msg, 'level': level})

    # --- 네트워크
    def get(self, url, referer):
        last = None
        for i in range(self.a.retries):
            if self.stop_event.is_set():
                raise RuntimeError('중지됨')
            try:
                r = self.s.get(url, headers={'Referer': referer}, timeout=30)
                if r.status_code in (200, 404):
                    return r  # 404: 없는 페이지, 재시도 의미 없음
                last = f'HTTP {r.status_code}'
            except requests.RequestException as e:
                last = e
            time.sleep(1.5 * (i + 1))
        raise RuntimeError(last)

    def fetch_page(self, url):
        origin = '{0.scheme}://{0.netloc}/'.format(urlparse(url))
        r = self.get(url, origin)
        if r.status_code == 404:
            return None
        if not r.encoding or r.encoding.lower() == 'iso-8859-1':
            r.encoding = r.apparent_encoding
        return BeautifulSoup(r.text, 'html.parser')

    # --- 페이지 분석
    def image_urls(self, soup, page_url):
        box = soup.find(id=self.a.id)
        if not box:
            return None
        urls = []
        for img in box.find_all('img'):
            for attr in IMG_ATTRS:
                v = (img.get(attr) or '').strip()
                if not v or v.startswith('data:'):
                    continue
                if attr == 'src' and re.search(r'spacer|loading|blank', v, re.I):
                    continue
                urls.append(urljoin(page_url, v))
                break
        return urls

    def next_url(self, soup, page_url):
        cand = None
        if self.a.next in ('auto', 'link'):
            a = soup.find('a', rel=lambda v: v and 'next' in v, href=True)
            if not a:
                for x in soup.find_all('a', href=True):
                    text = ' '.join(filter(None, [x.get_text(' ', strip=True), x.get('title'), x.get('aria-label')]))
                    if NEXT_TEXT.search(text) and not PREV_TEXT.search(text):
                        a = x
                        break
            if not a:
                for x in soup.find_all('a', href=True):
                    attrs = ' '.join([x.get('id') or ''] + (x.get('class') or []))
                    if re.search(r'next', attrs, re.I) and not re.search(r'prev', attrs, re.I):
                        a = x
                        break
            if a and not a['href'].startswith(('javascript:', '#')):
                cand = urljoin(page_url, a['href'])
        if not cand and self.a.next in ('auto', 'number'):
            p = urlparse(page_url)
            nums = list(re.finditer(r'\d+', p.path + '?' + p.query))
            if nums:
                old = nums[-1].group(0)
                idx = page_url.rfind(old)
                cand = page_url[:idx] + str(int(old) + 1).zfill(len(old)) + page_url[idx + len(old):]
        if cand and cand.split('#')[0] != page_url.split('#')[0]:
            return cand
        return None

    # --- 한 화 다운로드
    def download_images(self, ep, urls, page_url, part_dir):
        """part_dir 에 001.jpg ... 저장. 이미 있는 번호는 건너뜀. 실패한 번호 목록 반환."""
        os.makedirs(part_dir, exist_ok=True)
        pad = max(3, len(str(len(urls))))
        existing = {f.split('.')[0] for f in os.listdir(part_dir) if not f.endswith('.tmp')}

        def one(i_url):
            i, url = i_url
            stem = str(i).zfill(pad)
            if stem in existing:
                return None
            if self.stop_event.is_set():
                return (i, '중지됨')
            try:
                r = self.get(url, page_url)
                if r.status_code != 200 or not r.content:
                    raise RuntimeError(f'HTTP {r.status_code}')
                ctype = r.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if ctype.startswith('text/'):
                    raise RuntimeError('이미지가 아닌 응답')
                ext = EXT_BY_TYPE.get(ctype)
                if not ext:
                    m = re.search(r'\.(jpe?g|png|webp|gif|avif|bmp)$', urlparse(url).path, re.I)
                    ext = m.group(1).lower().replace('jpeg', 'jpg') if m else 'jpg'
                tmp = os.path.join(part_dir, stem + '.tmp')
                with open(tmp, 'wb') as f:
                    f.write(r.content)
                os.replace(tmp, os.path.join(part_dir, f'{stem}.{ext}'))
                return None
            except Exception as e:
                return (i, e)

        done = sum(1 for i in range(1, len(urls) + 1) if str(i).zfill(pad) in existing)
        if done:
            self.log(f'이미 받은 이미지 {done}장은 건너뜀', 'skip')
        fails = []
        with ThreadPoolExecutor(self.a.workers) as ex:
            for k, res in enumerate(ex.map(one, enumerate(urls, 1)), 1):
                if res:
                    fails.append(res[0])
                    if not self.stop_event.is_set():
                        self.log(f'{res[0]}번 이미지 실패: {res[1]}', 'warn')
                self.on('image', {'ep': ep, 'done': k, 'total': len(urls)})
        return fails

    @staticmethod
    def make_zip(part_dir, zip_path):
        tmp = zip_path + '.tmp'
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_STORED) as zf:  # 이미지는 이미 압축돼 있어 STORED가 빠름
            for f in sorted(os.listdir(part_dir)):
                if not f.endswith('.tmp'):
                    zf.write(os.path.join(part_dir, f), f)
        os.replace(tmp, zip_path)

    # --- 전체 실행
    def run(self, start_url):
        if not re.match(r'^https?://', start_url, re.I):
            start_url = 'https://' + start_url
        os.makedirs(self.a.out, exist_ok=True)
        log_path = os.path.join(self.a.out, LOG_NAME)
        record = load_log(log_path)
        max_ep = self.a.n or 10 ** 9

        url, ep, series, seen = start_url, 0, self.a.name, set()
        stats = {'new': 0, 'skipped': 0, 'partial': 0}
        reason = ''
        while url and ep < max_ep:
            if self.stop_event.is_set():
                reason = '중지했습니다. 다시 시작하면 이어서 받습니다.'
                break
            ep += 1
            key = url.split('#')[0]
            if key in seen:
                reason = '같은 페이지가 반복되어 종료했습니다.'
                break
            seen.add(key)

            self.on('episode', {'ep': ep, 'url': url, 'title': '', 'images': 0})
            try:
                soup = self.fetch_page(url)
            except Exception as e:
                if 'reset' in str(e).lower() or '10054' in str(e) or 'aborted' in str(e).lower():
                    reason = ('사이트가 접속을 끊었습니다. 크롬에서 이 링크가 열리는지 확인해 보세요. '
                              '크롬에서도 안 열리면 차단된 사이트이고, 크롬에서는 열리면 사이트가 프로그램 접속을 막는 것입니다.')
                else:
                    reason = f'페이지 접속 실패({e}). 다시 시작하면 이어서 받습니다.'
                break
            if soup is None:
                reason = '다음 페이지가 없습니다(404). 마지막 화까지 받았습니다.'
                break

            title = clean_name(soup.title.get_text() if soup.title else '') or f'{ep}화'
            if not series:
                series = series_name_from(title)
            series_dir = os.path.join(self.a.out, clean_name(series))
            os.makedirs(series_dir, exist_ok=True)
            nxt = self.next_url(soup, url)

            rec = record.get(key)
            if rec and os.path.exists(os.path.join(self.a.out, rec['zip'])):
                stats['skipped'] += 1
                self.on('episode_done', {'ep': ep, 'status': 'skip', 'zip': rec['zip'], 'title': title, 'fails': []})
                url = nxt
                if not url:
                    reason = '다음 화 링크가 없습니다. 마지막 화까지 받았습니다.'
                time.sleep(0.2)
                continue

            urls = self.image_urls(soup, url)
            if urls is None:
                reason = (f'id="{self.a.id}" 영역이 없는 페이지에서 멈췄습니다. '
                          '마지막 화를 지났거나 로그인이 필요한 페이지입니다.')
                break
            if not urls:
                reason = '이미지가 없는 페이지에서 멈췄습니다.'
                break
            self.on('episode', {'ep': ep, 'url': url, 'title': title, 'images': len(urls)})

            part_dir = os.path.join(series_dir, '.받는중', hashlib.md5(key.encode()).hexdigest()[:10])
            fails = self.download_images(ep, urls, url, part_dir)
            if self.stop_event.is_set():
                reason = '중지했습니다. 다시 시작하면 받다 만 이미지부터 이어서 받습니다.'
                self.on('episode_done', {'ep': ep, 'status': 'partial', 'zip': '', 'title': title, 'fails': fails})
                break
            if fails:
                stats['partial'] += 1
                self.on('episode_done', {'ep': ep, 'status': 'partial', 'zip': '', 'title': title, 'fails': fails})
            else:
                zip_rel = os.path.join(clean_name(series), f'{ep:03d} {title}.zip')
                self.make_zip(part_dir, os.path.join(self.a.out, zip_rel))
                shutil.rmtree(part_dir, ignore_errors=True)
                record[key] = {'zip': zip_rel, 'title': title, 'images': len(urls),
                               'time': time.strftime('%Y-%m-%d %H:%M')}
                save_log(log_path, record)
                stats['new'] += 1
                self.on('episode_done', {'ep': ep, 'status': 'ok', 'zip': zip_rel, 'title': title, 'fails': []})

            url = nxt
            if not url:
                reason = '다음 화 링크가 없습니다. 마지막 화까지 받았습니다.'
                if self.a.next == 'link' and ep == 1:
                    reason += ' (1화에서 바로 멈췄다면 "다음 화 찾기"를 "주소 숫자 +1"로 바꿔보세요)'
            elif ep < max_ep:
                self.stop_event.wait(self.a.delay)
        else:
            if not reason:
                reason = f'{ep}화까지 받았습니다.'

        try:
            os.rmdir(os.path.join(self.a.out, clean_name(series or ''), '.받는중'))
        except OSError:
            pass
        result = dict(stats, out=os.path.abspath(self.a.out), reason=reason)
        self.on('finished', result)
        return result


# ---------------------------------------------------------------- 명령 창 버전
def console_printer(ev, d):
    if ev == 'log':
        mark = {'warn': '⚠', 'error': '❌', 'skip': '⏭', 'ok': '✅'}.get(d['level'], '·')
        print(f'\n   {mark} {d["msg"]}')
    elif ev == 'episode':
        if d['title']:
            print(f'   {d["title"]} · 이미지 {d["images"]}장')
        else:
            print(f'\n[{d["ep"]}화] {d["url"]}')
    elif ev == 'image':
        print(f'\r   이미지 {d["done"]}/{d["total"]}', end='' if d['done'] < d['total'] else '\n', flush=True)
    elif ev == 'episode_done':
        if d['status'] == 'ok':
            print(f'   ✅ {d["zip"]}')
        elif d['status'] == 'skip':
            print(f'   ⏭ 이미 받음: {d["zip"]}')
        else:
            print(f'   ⚠ {len(d["fails"])}장 실패 → ZIP 보류. 다시 실행하면 실패한 것만 받습니다.')
    elif ev == 'finished':
        print(f'\n{d["reason"]}')
        print(f'🎉 끝! 새로 받음 {d["new"]}화 · 건너뜀 {d["skipped"]}화 · 미완성 {d["partial"]}화')
        print(f'   저장 위치: {d["out"]}')


def main():
    ap = argparse.ArgumentParser(description='웹툰 1화~마지막 화 자동 다운로더 (id="toon_img")')
    ap.add_argument('url', nargs='?', help='시작할 화(보통 1화) 링크')
    ap.add_argument('-n', type=int, default=0, help='몇 화까지 받을지 (기본: 마지막 화까지)')
    ap.add_argument('--out', default=DEFAULTS['out'], help='저장 폴더 (기본: 웹툰다운로드)')
    ap.add_argument('--name', default='', help='작품 폴더 이름 (기본: 페이지 제목에서 자동)')
    ap.add_argument('--id', default='toon_img', help='이미지가 들어있는 요소 id (기본: toon_img)')
    ap.add_argument('--next', choices=['link', 'number', 'auto'], default='link',
                    help='다음 화 찾는 방법: link=다음화 링크 따라가기(기본) / number=주소의 마지막 숫자+1 / auto=링크, 없으면 숫자+1')
    ap.add_argument('--delay', type=float, default=1.5, help='화 사이 쉬는 시간(초) (기본 1.5)')
    ap.add_argument('--workers', type=int, default=4, help='동시에 받을 이미지 수 (기본 4)')
    ap.add_argument('--retries', type=int, default=3, help='실패 시 재시도 횟수 (기본 3)')
    args = ap.parse_args()

    if not args.url:
        print('=' * 50)
        print('  웹툰 1화 ~ 마지막 화 자동 다운로더')
        print('=' * 50)
        args.url = input('시작할 화(보통 1화) 링크: ').strip()
        n = input('몇 화까지 받을까요? (그냥 엔터 = 마지막 화까지): ').strip()
        args.n = int(n) if n.isdigit() else 0
    if not args.url:
        return
    opts = make_options(**{k: v for k, v in vars(args).items() if k != 'url'})
    try:
        Downloader(opts, console_printer).run(args.url)
    except KeyboardInterrupt:
        print('\n\n중지했습니다. 다시 실행하면 받은 것은 건너뛰고 이어서 받습니다.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        print('\n[!] 오류가 났습니다. 위 내용을 캡처해서 보여주세요.')
    finally:
        # .py 파일을 직접 더블클릭한 경우에도 창이 바로 닫히지 않게
        if not os.environ.get('TOON_BAT') and len(sys.argv) == 1:
            try:
                input('\n엔터를 누르면 창이 닫힙니다.')
            except EOFError:
                pass
