"""
웹툰 이미지 자동 다운로더 (Python / GitHub Actions 호환)

링크만 주면 페이지의 <div id="toon_img"> 안 이미지를 1번부터 끝까지
순서대로 내려받아 [제목.zip] 으로 압축합니다.

사용법:
  python webtoon_batch_downloader.py <링크>                 # 그 페이지(한 화)만
  python webtoon_batch_downloader.py <링크> <최대회차수>     # 1화부터 '다음화'를 따라가며 여러 화
  python webtoon_batch_downloader.py <링크> 1 --id 다른아이디  # 컨테이너 id 변경

필요 라이브러리: pip install requests beautifulsoup4
"""

import argparse
import os
import re
import time
import zipfile
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

LAZY_ATTRS = ('data-src', 'data-original', 'data-url', 'data-lazy-src', 'src')
CONTENT_TYPE_EXT = {
    'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
    'image/webp': 'webp', 'image/gif': 'gif', 'image/bmp': 'bmp', 'image/avif': 'avif',
}
NEXT_KEYWORDS = ('다음화', '다음 화', '다음회', '다음 회', 'Next', '▶')


def make_session(page_url):
    s = requests.Session()
    origin = '{0.scheme}://{0.netloc}/'.format(urlparse(page_url))
    s.headers.update({'User-Agent': USER_AGENT, 'Referer': origin})
    return s


def clean_filename(name):
    name = re.sub(r'[\\/*?:"<>|\r\n\t]', '', name).strip()
    return name[:100] or f'웹툰_{int(time.time())}'


def pick_src(img):
    for attr in LAZY_ATTRS:
        v = (img.get(attr) or '').strip()
        if not v or v.startswith('data:'):
            continue
        if attr == 'src' and any(k in v for k in ('spacer', 'loading', 'blank')):
            continue
        return v
    return None


def guess_ext(url, content_type):
    ct = (content_type or '').split(';')[0].strip().lower()
    if ct in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ct]
    m = re.search(r'\.([a-zA-Z0-9]{3,4})$', urlparse(url).path)
    if m and m.group(1).lower() in ('jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'avif'):
        return 'jpg' if m.group(1).lower() == 'jpeg' else m.group(1).lower()
    return 'jpg'


def find_title(soup, imgs_alt):
    for text in (imgs_alt, soup.title.string if soup.title and soup.title.string else ''):
        m = re.search(r'^(.*?)\s*(\d+)\s*(화|회)?', text or '')
        if m and m.group(1).strip():
            return f'{m.group(1).strip()} {m.group(2)}화'
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ''


def find_next_link(soup, page_url):
    for a in soup.find_all('a'):
        text = a.get_text(strip=True)
        href = a.get('href')
        if href and not href.startswith(('javascript:', '#')) and any(k in text for k in NEXT_KEYWORDS):
            return urljoin(page_url, href)
    return None


def download_episode(url, container_id='toon_img', save_dir='output_zips', session=None):
    """한 페이지를 받아 ZIP을 만들고, (zip경로, 다음화링크) 를 반환."""
    os.makedirs(save_dir, exist_ok=True)
    session = session or make_session(url)

    print(f'\n🌐 [페이지 접속] {url}')
    try:
        res = session.get(url, timeout=20)
        res.raise_for_status()
    except Exception as e:
        print(f'❌ 페이지 접속 실패: {e}')
        return None, None
    res.encoding = res.apparent_encoding or res.encoding

    soup = BeautifulSoup(res.text, 'html.parser')
    container = soup.find(id=container_id)
    if not container:
        print(f"❌ id='{container_id}' 요소를 찾을 수 없습니다. "
              "(자바스크립트로 이미지를 그리는 사이트이거나 로그인이 필요한 페이지일 수 있습니다)")
        return None, find_next_link(soup, url)

    imgs = []
    for img in container.find_all('img'):
        src = pick_src(img)
        if src:
            imgs.append((urljoin(url, src), img.get('alt', '')))
    if not imgs:
        print('❌ 유효한 이미지 주소를 찾지 못했습니다.')
        return None, find_next_link(soup, url)

    title = clean_filename(find_title(soup, imgs[0][1]))
    zip_path = os.path.join(save_dir, f'{title}.zip')
    n = 1
    while os.path.exists(zip_path):
        n += 1
        zip_path = os.path.join(save_dir, f'{title} ({n}).zip')

    pad = max(2, len(str(len(imgs))))
    print(f'📦 [{title}] 총 {len(imgs)}장 다운로드 시작...')

    ok = 0
    session.headers['Referer'] = url
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:  # 이미지는 이미 압축돼 있어 STORED가 빠름
        for idx, (src, _alt) in enumerate(imgs, 1):
            for attempt in range(3):
                try:
                    r = session.get(src, timeout=30)
                    if r.status_code == 200 and r.content:
                        name = f'{idx:0{pad}d}.{guess_ext(src, r.headers.get("Content-Type"))}'
                        zf.writestr(name, r.content)
                        ok += 1
                        print(f'  └ [{idx}/{len(imgs)}] {name}')
                        break
                    print(f'  └ [{idx}] HTTP {r.status_code} (재시도 {attempt + 1}/3)')
                except Exception as err:
                    print(f'  └ [{idx}] 에러: {err} (재시도 {attempt + 1}/3)')
                time.sleep(1)
            else:
                print(f'  └ [{idx}] ❌ 최종 실패: {src}')

    if ok == 0:
        os.remove(zip_path)
        print('❌ 이미지를 하나도 받지 못해 ZIP을 만들지 않았습니다.')
        return None, find_next_link(soup, url)

    print(f'✅ {zip_path} 완성! ({ok}/{len(imgs)}장)')
    return zip_path, find_next_link(soup, url)


def run_auto_crawler(start_url, max_episodes=1, container_id='toon_img', save_dir='output_zips', delay=2.0):
    session = make_session(start_url)
    current, seen = start_url, set()
    for count in range(1, max_episodes + 1):
        if not current or current in seen:
            break
        seen.add(current)
        print(f'\n>>> [회차 {count}/{max_episodes}] <<<')
        _, next_url = download_episode(current, container_id, save_dir, session)
        if count < max_episodes:
            if not next_url:
                print('다음 회차 링크가 없어 종료합니다.')
                break
            time.sleep(delay)
        current = next_url
    print('\n🎉 모든 작업이 완료되었습니다!')


def main():
    ap = argparse.ArgumentParser(description='id="toon_img" 이미지 순서대로 ZIP 다운로더')
    ap.add_argument('url', nargs='?', help='웹툰 페이지 링크')
    ap.add_argument('max_episodes', nargs='?', type=int, default=1,
                    help='받을 회차 수 (기본 1 = 입력한 페이지만, 2 이상이면 다음화를 따라감)')
    ap.add_argument('--id', default='toon_img', help='이미지가 들어있는 요소 id (기본 toon_img)')
    ap.add_argument('--out', default='output_zips', help='ZIP 저장 폴더 (기본 output_zips)')
    ap.add_argument('--delay', type=float, default=2.0, help='회차 사이 대기 초 (기본 2)')
    args = ap.parse_args()

    url = (args.url or input('웹툰 페이지 링크를 입력하세요: ')).strip()
    if not url:
        return
    if not re.match(r'^https?://', url, re.I):
        url = 'https://' + url
    run_auto_crawler(url, max(1, args.max_episodes), args.id, args.out, args.delay)


if __name__ == '__main__':
    main()
