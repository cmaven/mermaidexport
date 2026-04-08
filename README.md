<!-- README.md: Mermaid Web Converter 프로젝트 소개 및 사용 가이드 | 생성일: 2026-04-07 -->

# Mermaid Web Converter

Markdown 파일의 Mermaid 다이어그램을 PNG, draw.io, Excalidraw, PPTX로 변환하는 웹 애플리케이션.

## 주요 기능

- 드래그 앤 드롭 .md 파일 업로드
- 4개 포맷 자동 변환: PNG, draw.io, Excalidraw, PPTX
- PPTX는 편집 가능한 네이티브 도형 (임베드 이미지 아님)
- 합본 PPTX 다운로드 (모든 다이어그램을 하나의 파일로)
- 전체 파일 ZIP 다운로드
- flowchart, graph, sequenceDiagram 지원
- NanumSquare 한글 폰트 적용

## 빠른 시작

### Docker로 실행 (권장)

```bash
git clone <repo-url>
cd webapp
docker compose up -d
```

브라우저에서 `http://localhost:8205` 접속.

### 로컬 직접 실행

**사전 요구사항:**
- Python 3.10+
- Node.js 20+ (`mmdc` 폴백용)
- LibreOffice (`PPTX→PNG` 변환용, 선택)

```bash
# 1. mmdc 설치
npm install -g @mermaid-js/mermaid-cli

# 2. Python 의존성 설치
cd webapp/backend
pip install -r requirements.txt

# 3. 서버 실행
uvicorn main:app --host 0.0.0.0 --port 8205 --reload
```

브라우저에서 `http://localhost:8205` 접속.

## 사용 방법

1. 브라우저에서 애플리케이션 접속
2. Mermaid 코드블록이 포함된 `.md` 파일을 드래그 앤 드롭 또는 클릭하여 업로드
3. 변환 완료 후 다이어그램별 미리보기 확인
4. 포맷별 개별 다운로드 또는 전체 ZIP 다운로드

**지원 Mermaid 블록 형식:**

````markdown
## 시스템 흐름도

```mermaid
flowchart LR
    A[클라이언트] --> B[API 서버]
    B --> C[(데이터베이스)]
```
````

블록 직전의 마크다운 제목(`#`~`######`)이 다이어그램 제목으로 사용된다.

## 배포

### Docker Compose

```bash
# 실행
docker compose up -d

# 중지
docker compose down

# 삭제 (볼륨 포함)
docker compose down -v
docker rmi $(docker images -q webapp-mermaid-converter)
```

### 포트 변경

```bash
cp .env_example .env
# .env 파일에서 PORT 값 수정
# PORT=9000
docker compose up -d
```

기본 포트는 `8205`이다.

### Nginx 리버스 프록시

```nginx
server {
    listen 80;
    server_name mermaid.example.com;

    location / {
        proxy_pass http://127.0.0.1:8205;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10M;
    }
}
```

HTTPS 적용 시 `proxy_set_header X-Forwarded-Proto $scheme;` 헤더가 올바른 리다이렉트를 보장한다.

## 디렉토리 구조

```
webapp/
├── backend/
│   ├── main.py            # FastAPI 서버
│   ├── parser.py          # Mermaid 블록 파서
│   ├── requirements.txt
│   └── converters/
│       ├── palette.py     # 공통 색상 팔레트
│       ├── png.py
│       ├── drawio.py
│       ├── excalidraw.py
│       ├── pptx_shapes.py
│       └── pptx_combined.py
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── docs/
│   ├── implementation.md  # 구현 상세
│   └── tutorial.md        # 따라하기 가이드
├── Dockerfile
├── docker-compose.yml
├── .env_example
└── README.md
```

## 기술 스택

| 구분 | 기술 |
|------|------|
| 백엔드 | Python 3.10+, FastAPI, uvicorn |
| PPTX 생성 | python-pptx, lxml |
| 이미지 처리 | Pillow |
| PNG 변환 | LibreOffice headless (1차), mmdc 폴백 (2차) |
| 프론트엔드 | 바닐라 HTML/CSS/JS |
| 배포 | Docker, Docker Compose |
| 폰트 | NanumSquare (fonts-nanum) |

## API 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 헬스 체크 |
| `POST` | `/api/convert` | MD 파일 변환 |
| `GET` | `/api/download/{job_id}/{index}/{format}` | 개별 파일 다운로드 |
| `GET` | `/api/download/{job_id}/combined-pptx` | 합본 PPTX |
| `GET` | `/api/download/{job_id}/all` | 전체 ZIP |

지원 `format`: `png`, `drawio`, `excalidraw`, `pptx`

## 라이선스

MIT
