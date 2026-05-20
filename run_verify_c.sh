#!/bin/bash
# ============================================================
# run_verify_c.sh: track-c-verifier 정량 검증 스크립트
# 상세: sequenceDiagram 보강 후 PNG 변환 정량 가드
# 생성일: 2026-05-20
# ============================================================

set -euo pipefail

BASE_URL="http://localhost:8205"
OUT_DIR="$(dirname "$0")/.omc/research/seq-fix-after"
PASS=0
FAIL=0
TOTAL=0

mkdir -p "$OUT_DIR"

log_pass() { echo "  ✅ PASS: $1"; PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); }
log_fail() { echo "  ❌ FAIL: $1"; FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); }

check_min_size() {
    local file="$1" label="$2" min_bytes="${3:-5000}"
    if [ -f "$file" ]; then
        local size
        size=$(wc -c < "$file")
        if [ "$size" -ge "$min_bytes" ]; then
            log_pass "$label 크기 OK (${size} bytes ≥ ${min_bytes})"
        else
            log_fail "$label 너무 작음 (${size} bytes < ${min_bytes})"
        fi
    else
        log_fail "$label 파일 없음: $file"
    fi
}

echo "========================================"
echo " Mermaid Export — 정량 검증 (run_verify_c)"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# ── 헬스 체크 ────────────────────────────────
echo ""
echo "[0] /health 체크"
HEALTH=$(curl -sf "$BASE_URL/health" 2>/dev/null || echo '{"status":"error"}')
if echo "$HEALTH" | grep -q '"ok"'; then
    log_pass "/health → ok"
else
    log_fail "/health 응답 오류: $HEALTH"
    echo "서버 미응답 — 검증 중단"
    exit 1
fi

# ── 헬퍼: 파일 업로드 → job_id 추출 ──────────
convert_file() {
    local md_file="$1"
    local resp
    resp=$(curl -sf -X POST "$BASE_URL/api/convert" \
        -F "file=@${md_file}" 2>/dev/null) || { echo "CONVERT_FAIL"; return; }
    echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['job_id'])" 2>/dev/null || echo "PARSE_FAIL"
}

download_png() {
    local job_id="$1" idx="$2" out_path="$3"
    curl -sf "$BASE_URL/api/download/${job_id}/${idx}/png" -o "$out_path" 2>/dev/null
}

# ── 테스트 1: 복잡 sequenceDiagram ─────────────
echo ""
echo "[1] 복잡 sequenceDiagram (alt+Note+br+8participants)"
MD1="$(dirname "$0")/backend/test_seq_complex.md"
JOB1=$(convert_file "$MD1")
if [[ "$JOB1" == "CONVERT_FAIL" || "$JOB1" == "PARSE_FAIL" ]]; then
    log_fail "변환 실패: $JOB1"
else
    echo "  job_id: $JOB1"
    # 블록 0: 복잡 sequenceDiagram (1번)
    PNG1="$OUT_DIR/seq_complex_block0.png"
    download_png "$JOB1" 0 "$PNG1"
    check_min_size "$PNG1" "1번-sequenceDiagram PNG" 20000

    # 블록 2: 단순 sequenceDiagram (3번)
    PNG3="$OUT_DIR/seq_simple_block2.png"
    download_png "$JOB1" 2 "$PNG3"
    check_min_size "$PNG3" "3번-단순sequenceDiagram PNG" 10000

    # 블록 3: erDiagram (5번)
    PNG5="$OUT_DIR/er_block3.png"
    download_png "$JOB1" 3 "$PNG5"
    check_min_size "$PNG5" "5번-erDiagram PNG" 15000
fi

# ── 테스트 2: graph 회귀 ─────────────────────────
echo ""
echo "[2] graph 회귀 검증"
MD2="$(dirname "$0")/backend/test_graph.md"
JOB2=$(convert_file "$MD2")
if [[ "$JOB2" == "CONVERT_FAIL" || "$JOB2" == "PARSE_FAIL" ]]; then
    log_fail "graph 변환 실패: $JOB2"
else
    echo "  job_id: $JOB2"
    PNG_G="$OUT_DIR/graph_block0.png"
    download_png "$JOB2" 0 "$PNG_G"
    check_min_size "$PNG_G" "graph PNG" 30000
fi

# ── 테스트 3: PPTX 생성 정량 체크 ────────────────
echo ""
echo "[3] PPTX 생성 체크 (복잡 sequenceDiagram)"
if [[ "${JOB1:-}" != "CONVERT_FAIL" && "${JOB1:-}" != "PARSE_FAIL" && -n "${JOB1:-}" ]]; then
    PPTX1="$OUT_DIR/seq_complex_block0.pptx"
    curl -sf "$BASE_URL/api/download/${JOB1}/0/pptx" -o "$PPTX1" 2>/dev/null
    check_min_size "$PPTX1" "1번-sequenceDiagram PPTX" 5000
fi

# ── 결과 요약 ───────────────────────────────────
echo ""
echo "========================================"
echo " 결과: ${PASS}/${TOTAL} PASS  |  ${FAIL} FAIL"
echo "========================================"

if [ "$FAIL" -eq 0 ]; then
    echo " 🎉 정량 가드 ALL PASS"
    exit 0
else
    echo " ⚠️  ${FAIL}개 실패 항목 있음"
    exit 1
fi
