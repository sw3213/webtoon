# 웹툰 이미지 순차 다운로더 (#toon_img → ZIP)

링크만 주면 페이지의 `<div id="toon_img">` 안 이미지를 **1번부터 끝까지 순서대로** 내려받아
`01.jpg, 02.jpg, …` 이름으로 **ZIP 하나**로 묶어줍니다.

## 1. 웹사이트 (`index.html`)
1. `index.html`을 브라우저로 열거나 GitHub Pages로 배포
2. 맨 위 **🔗 링크** 칸에 웹툰 페이지 주소 입력 → **분석 + ZIP 다운로드**
3. 사이트가 막아서 실패하면: 페이지 소스(Ctrl+U)를 아래 칸에 붙여넣고 **이미지 태그 분석** → **ZIP 다운로드**

> 브라우저는 보안(CORS) 때문에 다른 사이트 페이지/이미지를 직접 못 받는 경우가 많아 공개 프록시(allorigins, corsproxy.io)를 경유합니다.
> 프록시가 막히거나 Referer 검사를 하는 사이트는 아래 Python 프로그램이 가장 확실합니다.

## 2. Python 프로그램 (`webtoon_batch_downloader.py`) — 가장 확실한 방법
```bash
pip install requests beautifulsoup4
```
```bash
python webtoon_batch_downloader.py "https://사이트/웹툰/1화"
```
- 결과: `output_zips/제목 1화.zip`
- 여러 화 연속: `python webtoon_batch_downloader.py "<1화 링크>" 30` (다음화 링크를 따라감)
- 컨테이너 id 변경: `--id 다른아이디`, 저장 폴더: `--out 폴더`

## 3. GitHub Actions (내 컴퓨터 파이썬 불필요)
`download.yml`을 `.github/workflows/`에 두고 **Actions → 웹툰 자동 순차 다운로더 → Run workflow** 에서 링크 입력 → 완료 후 Artifacts에서 ZIP 다운로드.

## 한계
- 이미지를 자바스크립트로 나중에 그리는 사이트, 로그인/성인인증이 필요한 페이지는 링크만으로는 받을 수 없습니다(소스 붙여넣기 사용).
- 저작권이 있는 작품은 개인 소장 범위에서만 사용하고, 사이트 이용약관을 지켜주세요.
