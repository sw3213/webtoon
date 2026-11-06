"""
웹툰 전 회차 자동 다운로더

  - 각 화: <div id="toon_img"> 안 이미지를 1번부터 마지막까지 순서대로 받아 ZIP 하나로 저장
  - 1화부터 '다음화'를 따라가며 마지막 화(또는 N화)까지 자동으로 진행
  - 이미 받은 화는 건너뜀, 중간에 끊긴 화는 받은 이미지는 두고 나머지만 받음

사용법:
  python webtoon_downloader.py                       (실행 후 링크 입력)
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
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # 처음 실행이면 필요한 라이브러리를 자동 설치
    print('필요한 라이브러리(requests, beautifulsoup4)를 설치합니다...')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', 'requests', 'beautifulsoup4'])
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


# ---------------------------------------------------------------- 다운로더
class Downloader:
    def __init__(self, args):
        self.a = args
        self.s = requests.Session()
        self.s.headers['User-Agent'] = USER_AGENT
        self.s.mount('https://', requests.adapters.HTTPAdapter(pool_maxsize=args.workers + 2))
        self.s.mount('http://', requests.adapters.HTTPAdapter(pool_maxsize=args.workers + 2))

    # --- 네트워크
    def get(self, url, referer, tries=None):
        tries = tries or self.a.retries
        last = None
        for i in range(tries):
            try:
                r = self.s.get(url, headers={'Referer': referer}, timeout=30)
                if r.status_code == 404:
                    return r  # 없는 페이지: 재시도 의미 없음
                if r.status_code == 200:
                    return r
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
            nums = list(re.finditer(r'\d+', urlparse(page_url).path + '?' + urlparse(page_url).query))
            if nums:
                m = nums[-1]
                full = page_url
                idx = full.rfind(m.group(0))
                new = str(int(m.group(0)) + 1).zfill(len(m.group(0)))
                cand = full[:idx] + new + full[idx + len(m.group(0)):]
        if cand and cand.split('#')[0] != page_url.split('#')[0]:
            return cand
        return None

    # --- 한 화 다운로드
    def download_images(self, urls, page_url, part_dir):
        """part_dir 에 001.jpg ... 저장. 이미 있는 번호는 건너뜀. 실패한 번호 목록 반환."""
        os.makedirs(part_dir, exist_ok=True)
        pad = max(3, len(str(len(urls))))
        existing = {f.split('.')[0]: f for f in os.listdir(part_dir) if not f.endswith('.tmp')}

        def one(i_url):
            i, url = i_url
            stem = str(i).zfill(pad)
            if stem in existing:
                return None
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
            print(f'   이미 받은 이미지 {done}장은 건너뜀')
        fails = []
        with ThreadPoolExecutor(self.a.workers) as ex:
            for k, res in enumerate(ex.map(one, enumerate(urls, 1)), 1):
                if res:
                    fails.append(res[0])
                    print(f'\n   ✗ {res[0]}번 실패: {res[1]}')
                print(f'\r   이미지 {k}/{len(urls)}', end='', flush=True)
        print()
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
        os.makedirs(self.a.out, exist_ok=True)
        log_path = os.path.join(self.a.out, LOG_NAME)
        log = load_log(log_path)
        max_ep = self.a.n or 10 ** 9

        url, ep, series, seen = start_url, 0, self.a.name, set()
        new_count = skip_count = 0
        while url and ep < max_ep:
            ep += 1
            key = url.split('#')[0]
            if key in seen:
                print('같은 페이지가 반복되어 종료합니다.')
                break
            seen.add(key)

            print(f'\n[{ep}화] {url}')
            try:
                soup = self.fetch_page(url)
            except Exception as e:
                print(f'❌ 페이지 접속 실패: {e}  → 여기서 멈춥니다. 다시 실행하면 이어서 받습니다.')
                break
            if soup is None:
                print('페이지가 없습니다(404). 마지막 화로 보고 종료합니다.')
                break

            title = clean_name(soup.title.get_text() if soup.title else '') or f'{ep}화'
            if not series:
                series = series_name_from(title)
            series_dir = os.path.join(self.a.out, clean_name(series))
            os.makedirs(series_dir, exist_ok=True)
            nxt = self.next_url(soup, url)

            rec = log.get(key)
            if rec and os.path.exists(os.path.join(self.a.out, rec['zip'])):
                print(f'   ⏭ 이미 받음: {rec["zip"]}')
                skip_count += 1
                url = nxt
                time.sleep(0.3)
                continue

            urls = self.image_urls(soup, url)
            if urls is None:
                print(f'id="{self.a.id}" 영역이 없습니다. 마지막 화를 지났거나 로그인이 필요한 페이지입니다. 종료합니다.')
                break
            if not urls:
                print('이미지가 없습니다. 종료합니다.')
                break
            print(f'   {title} · 이미지 {len(urls)}장')

            part_dir = os.path.join(series_dir, '.받는중', hashlib.md5(key.encode()).hexdigest()[:10])
            fails = self.download_images(urls, url, part_dir)
            if fails:
                print(f'   ⚠ {len(fails)}장 실패 → 이 화는 ZIP을 만들지 않았습니다. 다시 실행하면 실패한 것만 받습니다.')
            else:
                zip_name = f'{ep:03d} {title}.zip'
                zip_rel = os.path.join(clean_name(series), zip_name)
                self.make_zip(part_dir, os.path.join(self.a.out, zip_rel))
                shutil.rmtree(part_dir, ignore_errors=True)
                log[key] = {'zip': zip_rel, 'title': title, 'images': len(urls), 'time': time.strftime('%Y-%m-%d %H:%M')}
                save_log(log_path, log)
                new_count += 1
                print(f'   ✅ {zip_rel}')

            url = nxt
            if not url:
                print('\n다음 화 링크가 없습니다. 마지막 화까지 받았습니다.')
                if self.a.next == 'link' and ep == 1:
                    print('(1화에서 바로 멈췄다면 다음화 버튼이 자바스크립트일 수 있습니다 → --next number 옵션을 써보세요)')
            elif ep < max_ep:
                time.sleep(self.a.delay)

        try:
            os.rmdir(os.path.join(self.a.out, clean_name(series or ''), '.받는중'))
        except OSError:
            pass
        print(f'\n🎉 끝! 새로 받음 {new_count}화 · 건너뜀 {skip_count}화 · 저장 위치: {os.path.abspath(self.a.out)}')


def main():
    ap = argparse.ArgumentParser(description='웹툰 1화~마지막 화 자동 다운로더 (id="toon_img")')
    ap.add_argument('url', nargs='?', help='시작할 화(보통 1화) 링크')
    ap.add_argument('-n', type=int, default=0, help='몇 화까지 받을지 (기본: 마지막 화까지)')
    ap.add_argument('--out', default='웹툰다운로드', help='저장 폴더 (기본: 웹툰다운로드)')
    ap.add_argument('--name', default='', help='작품 폴더 이름 (기본: 페이지 제목에서 자동)')
    ap.add_argument('--id', default='toon_img', help='이미지가 들어있는 요소 id (기본: toon_img)')
    ap.add_argument('--next', choices=['link', 'number', 'auto'], default='link',
                    help='다음 화 찾는 방법: link=다음화 링크 따라가기(기본) / number=주소의 마지막 숫자+1 / auto=링크, 없으면 숫자+1')
    ap.add_argument('--delay', type=float, default=1.5, help='화 사이 쉬는 시간(초) (기본 1.5)')
    ap.add_argument('--workers', type=int, default=4, help='동시에 받을 이미지 수 (기본 4)')
    ap.add_argument('--retries', type=int, default=3, help='실패 시 재시도 횟수 (기본 3)')
    args = ap.parse_args()

    interactive = not args.url
    if interactive:
        print('=' * 50)
        print('  웹툰 1화 ~ 마지막 화 자동 다운로더')
        print('=' * 50)
        args.url = input('시작할 화(보통 1화) 링크: ').strip()
        n = input('몇 화까지 받을까요? (그냥 엔터 = 마지막 화까지): ').strip()
        args.n = int(n) if n.isdigit() else 0
    if args.url:
        if not re.match(r'^https?://', args.url, re.I):
            args.url = 'https://' + args.url
        try:
            Downloader(args).run(args.url)
        except KeyboardInterrupt:
            print('\n\n중지했습니다. 다시 실행하면 받은 것은 건너뛰고 이어서 받습니다.')
    if interactive:
        input('\n엔터를 누르면 창이 닫힙니다.')


if __name__ == '__main__':
    main()
