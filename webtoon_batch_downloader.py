"""
웹툰 전회차 자동 순차 다운로더 (Python / GitHub Actions 호환)
- div id="toon_img" 안의 이미지를 순서대로 긁어와 [제목 @화.zip]으로 자동 압축
"""

import sys
import os
import re
import time
import zipfile
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://aws-cloud-no7.site'
}

def download_episode(url, save_dir="output_zips"):
    os.makedirs(save_dir, exist_ok=True)
    
    print(f"\n==================================================")
    print(f"🌐 [페이지 접속 중] {url}")
    print(f"==================================================")
    try:
        res = requests.get(url, headers=HEADERS, timeout=20)
        res.raise_for_status()
    except Exception as e:
        print(f"❌ 페이지 접속 실패: {e}")
        return None

    soup = BeautifulSoup(res.text, 'html.parser')
    toon_div = soup.find('div', id='toon_img')
    if not toon_div:
        print("❌ id='toon_img' 요소를 찾을 수 없습니다. 페이지 소스 구조를 확인하세요.")
        return None

    # 이미지 태그 수집
    imgs = []
    for img in toon_div.find_all('img'):
        src = img.get('data-src') or img.get('data-original') or img.get('data-url') or img.get('src')
        alt = img.get('alt', '')
        if src and not src.startswith('data:image/svg') and 'spacer' not in src:
            imgs.append({'src': src, 'alt': alt})

    if not imgs:
        print("❌ 유효한 이미지 URL을 찾지 못했습니다.")
        return None

    # 회차명 및 제목 추출
    first_alt = imgs[0]['alt']
    match = re.search(r'^(.*?)\s*(\d+화|\d+회|\d+)', first_alt)
    if match:
        title = match.group(1).strip()
        ep = match.group(2)
        if not ep.endswith('화'):
            ep += '화'
        full_title = f"{title} {ep}"
    else:
        # fallback to page title
        match_title = re.search(r'^(.*?)\s*(\d+화|\d+회|\d+)', soup.title.string if soup.title else '')
        if match_title:
            full_title = f"{match_title.group(1).strip()} {match_title.group(2)}"
        else:
            full_title = f"웹툰_{int(time.time())}"

    # 특수문자 제거
    clean_title = re.sub(r'[\\/*?:"<>|]', "", full_title)
    zip_filename = os.path.join(save_dir, f"{clean_title}.zip")
    print(f"📦 [{clean_title}] 총 {len(imgs)}장 다운로드 및 압축 시작...")

    temp_folder = os.path.join(save_dir, f"temp_{int(time.time())}")
    os.makedirs(temp_folder, exist_ok=True)

    success_count = 0
    with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zf:
        for idx, item in enumerate(imgs, 1):
            src = item['src']
            num_str = f"{idx:02d}"
            filename = f"이미지_{num_str}.jpg"
            filepath = os.path.join(temp_folder, filename)

            try:
                img_res = requests.get(src, headers=HEADERS, timeout=15)
                if img_res.status_code == 200:
                    with open(filepath, 'wb') as f:
                        f.write(img_res.content)
                    zf.write(filepath, filename)
                    success_count += 1
                    print(f"  └ [{idx:02d}/{len(imgs):02d}] {filename} 저장 완료")
                else:
                    print(f"  └ [{idx:02d}] HTTP {img_res.status_code} 실패: {src}")
            except Exception as err:
                print(f"  └ [{idx:02d}] 에러 발생: {err}")

    # 임시 파일 삭제
    for f in os.listdir(temp_folder):
        try:
            os.remove(os.path.join(temp_folder, f))
        except:
            pass
    try:
        os.rmdir(temp_folder)
    except:
        pass

    print(f"✅ [{clean_title}.zip] 완성! (총 {success_count}/{len(imgs)}장 압축됨)")

    # 다음화 링크 탐색
    next_link = None
    for a in soup.find_all('a'):
        text = a.get_text(strip=True)
        if any(keyword in text for keyword in ['다음화', '다음 회', '다음', 'Next', '▶']):
            href = a.get('href')
            if href and not href.startswith('javascript:'):
                next_link = href
                break

    return next_link

def run_auto_crawler(start_url, max_episodes=50):
    current_url = start_url
    count = 0

    while current_url and count < max_episodes:
        count += 1
        print(f"\n>>> [회차 진행 {count}/{max_episodes}] <<<")
        next_url = download_episode(current_url)
        if not next_url:
            print("더 이상 다음 회차가 없거나 링크를 찾을 수 없습니다. 작업을 종료합니다.")
            break
        
        if not next_url.startswith('http'):
            current_url = urljoin(current_url, next_url)
        else:
            current_url = next_url

        print("다음 회차 이동 전 2초 대기 중...")
        time.sleep(2)

    print("\n🎉 모든 다운로드 작업이 완료되었습니다!")

if __name__ == '__main__':
    if len(sys.argv) > 1:
        target_url = sys.argv[1].strip()
        max_eps = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    else:
        print("==================================================")
        print("  웹툰 1화부터 끝까지 자동 다운로더 프로그램")
        print("==================================================")
        target_url = input("1화 웹툰 페이지 주소를 입력하세요: ").strip()
        max_eps = 50

    if target_url:
        run_auto_crawler(target_url, max_eps)
