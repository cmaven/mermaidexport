# ============================================================
# Dockerfile: Mermaid Web Converter 컨테이너 이미지
# 상세: Python + Node.js + mmdc + Chromium 환경 구성
# 생성일: 2026-04-07
# ============================================================

FROM python:3.10-slim

# 시스템 패키지 설치 (Node.js, Chromium, 한글 폰트 포함)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    chromium \
    fonts-nanum \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Puppeteer가 시스템 Chromium을 사용하도록 설정
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# mmdc(Mermaid CLI) 전역 설치
RUN npm install -g @mermaid-js/mermaid-cli

# 작업 디렉토리 설정
WORKDIR /app

# 백엔드 및 프론트엔드 파일 복사
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY requirements.txt /app/requirements.txt

# Python 의존성 설치
RUN pip install --no-cache-dir -r /app/requirements.txt

# 작업 디렉토리를 백엔드로 변경
WORKDIR /app/backend

# jobs 디렉토리 생성
RUN mkdir -p /app/backend/jobs

# 서버 실행
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8205}
