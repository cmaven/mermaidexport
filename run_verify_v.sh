#!/usr/bin/env bash
# ============================================================
# run_verify_v.sh: track-V 종합 검증 — 색/fit/progress (Task #6)
# 상세: track-P(color) + track-F(fit) + track-U(progress) 통합 검증
#       의존성: track_p_done + track_f_done + track_u_done
# 생성일: 2026-05-21
# ============================================================

set -uo pipefail

PROJECT_ROOT="/home/kcloud/claude-code/projects/mermaidexport"
STATE_DIR="$PROJECT_ROOT/.omc/state"
SCRIPTS_DIR="$PROJECT_ROOT/.omc/research/scripts"
OUT_DIR="$PROJECT_ROOT/.omc/research/color-fit-progress"
BASE_URL="http://localhost:8205"
REF_MD="$PROJECT_ROOT/13_architecture_reference.md"

mkdir -p "$OUT_DIR"

TOTAL_PASS=0
TOTAL_FAIL=0

log()      { echo "[$(date '+%H:%M:%S')] $*"; }
pass_()    { echo "  ✅ PASS: $1"; TOTAL_PASS=$((TOTAL_PASS+1)); }
fail_()    { echo "  ❌ FAIL: $1"; TOTAL_FAIL=$((TOTAL_FAIL+1)); }

# ── 0. 의존성 신호 확인 ──────────────────────────────────────
log "=== [0] 의존성 신호 확인 ==="
BLOCKED=0
for sig in track_p_done track_f_done track_u_done; do
    if [ -f "$STATE_DIR/$sig" ]; then
        pass_ "$sig 존재"
    else
        fail_ "$sig 없음 — 의존 작업 미완료"
        BLOCKED=1
    fi
done
if [ "$BLOCKED" -eq 1 ]; then
    log "의존성 미충족 — 검증 중단"
    exit 2
fi

# ── 1. Docker 재빌드 ─────────────────────────────────────────
log "=== [1] Docker 재빌드 ==="
sudo docker compose -f "$PROJECT_ROOT/docker-compose.yml" up -d --build > /tmp/verify_v_docker.log 2>&1
if [ $? -eq 0 ]; then
    pass_ "docker compose 재빌드 성공"
else
    fail_ "docker compose 재빌드 실패"
    cat /tmp/verify_v_docker.log | tail -20
    exit 1
fi

# /health 폴링 (최대 30초)
log "/health 폴링..."
for i in $(seq 1 30); do
    HEALTH=$(curl -sf "$BASE_URL/health" 2>/dev/null || echo '{}')
    if echo "$HEALTH" | grep -q '"ok"'; then
        pass_ "/health → ok (${i}초)"
        break
    fi
    sleep 1
done
echo "$HEALTH" | grep -q '"ok"' || { fail_ "/health 30초 내 응답 없음"; exit 1; }

# ── 2. 13_architecture_reference.md 전체 변환 ─────────────────
log "=== [2] 13_architecture_reference.md 변환 (10블록) ==="
CONVERT_RESP=$(curl -sf -X POST "$BASE_URL/api/convert" \
    -F "file=@${REF_MD}" 2>/dev/null) || { fail_ "변환 API 호출 실패"; exit 1; }
JOB_ID=$(echo "$CONVERT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null)
if [ -z "$JOB_ID" ] || [ "$JOB_ID" = "None" ]; then
    fail_ "job_id 추출 실패"
    exit 1
fi
BLOCK_COUNT=$(echo "$CONVERT_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['diagrams']))" 2>/dev/null)
pass_ "변환 완료 — job_id=$JOB_ID, 블록 수=$BLOCK_COUNT"

# PNG + PPTX 다운로드
log "PNG/PPTX 다운로드 중..."
PPTX_PATHS=()
for i in $(seq 0 $((BLOCK_COUNT-1))); do
    PNG_OUT="$OUT_DIR/diagram_${i}.png"
    PPTX_OUT="$OUT_DIR/diagram_${i}.pptx"
    curl -sf "$BASE_URL/api/download/${JOB_ID}/${i}/png"  -o "$PNG_OUT"  2>/dev/null || true
    curl -sf "$BASE_URL/api/download/${JOB_ID}/${i}/pptx" -o "$PPTX_OUT" 2>/dev/null || true
    if [ -s "$PNG_OUT" ];  then pass_ "diagram_${i}.png 다운로드 OK"; else fail_ "diagram_${i}.png 누락"; fi
    if [ -s "$PPTX_OUT" ]; then PPTX_PATHS+=("$PPTX_OUT"); fi
done

# ── 3. 트랙 P: 색상 검증 (WCAG 대비비 ≥ 4.5:1, 검은 박스 0건) ──
log "=== [3] 트랙 P 색상 검증 ==="
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "/home/kcloud/claude-code/projects/mermaidexport/backend")
import glob

try:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE_TYPE
except ImportError:
    print("  ❌ FAIL: python-pptx 미설치")
    sys.exit(1)

OUT_DIR = "/home/kcloud/claude-code/projects/mermaidexport/.omc/research/color-fit-progress"
TEXT_RGB = (0x1E, 0x29, 0x3B)   # 텍스트 색 (#1e293b)

def linearize(c):
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

def luminance(r, g, b):
    return 0.2126*linearize(r) + 0.7152*linearize(g) + 0.0722*linearize(b)

def contrast_ratio(rgb1, rgb2):
    L1 = luminance(*rgb1)
    L2 = luminance(*rgb2)
    if L1 < L2: L1, L2 = L2, L1
    return (L1 + 0.05) / (L2 + 0.05)

PASS = 0
FAIL = 0
BLACK_BOXES = 0
LOW_CONTRAST = []

pptx_files = sorted(glob.glob(f"{OUT_DIR}/diagram_*.pptx"))
for pptx_path in pptx_files:
    label = os.path.basename(pptx_path)
    try:
        prs = Presentation(pptx_path)
        for slide_idx, slide in enumerate(prs.slides):
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                # fill 색 추출
                try:
                    fill = shape.fill
                    if fill.type is None:
                        continue
                    fill_rgb = fill.fore_color.rgb
                    r, g, b = fill_rgb[0], fill_rgb[1], fill_rgb[2]
                    lum = luminance(r, g, b)
                    # 검은 박스: 휘도 < 0.05 (어두운 fill)
                    if lum < 0.05:
                        BLACK_BOXES += 1
                        text = shape.text_frame.text[:30] if shape.has_text_frame else ""
                        LOW_CONTRAST.append(f"{label}/슬라이드{slide_idx+1}: fill=#{r:02x}{g:02x}{b:02x} lum={lum:.3f} text='{text}'")
                    else:
                        # WCAG 대비비 (fill vs 텍스트)
                        cr = contrast_ratio((r,g,b), TEXT_RGB)
                        if cr < 4.5:
                            LOW_CONTRAST.append(f"{label}/슬라이드{slide_idx+1}: fill=#{r:02x}{g:02x}{b:02x} 대비비={cr:.2f}:1 < 4.5:1")
                            FAIL += 1
                        else:
                            PASS += 1
                except Exception:
                    continue
    except Exception as e:
        print(f"  ❌ FAIL: {label} 파싱 오류 — {e}")
        FAIL += 1

if BLACK_BOXES == 0:
    print(f"  ✅ PASS: 검은 박스(lum<0.05) 0건")
else:
    print(f"  ❌ FAIL: 검은 박스 {BLACK_BOXES}건")
    for v in LOW_CONTRAST[:5]: print(f"    → {v}")

low_cr_count = sum(1 for v in LOW_CONTRAST if "대비비" in v)
if low_cr_count == 0:
    print(f"  ✅ PASS: WCAG 4.5:1 미달 0건 (검사 {PASS}개 shape)")
else:
    print(f"  ❌ FAIL: 대비비 미달 {low_cr_count}건")
    for v in [v for v in LOW_CONTRAST if "대비비" in v][:5]: print(f"    → {v}")

print(f"  색상 검증: shape {PASS}개 OK, {FAIL}개 FAIL, 검은박스 {BLACK_BOXES}건")
PYEOF

# ── 4. 트랙 F: fit 검증 (폰트 ≥ 9pt, 텍스트 박스 내 수용) ──────
log "=== [4] 트랙 F fit 검증 ==="
python3 - <<'PYEOF'
import sys, glob
sys.path.insert(0, "/home/kcloud/claude-code/projects/mermaidexport/backend")

try:
    from pptx import Presentation
    from pptx.util import Pt
except ImportError:
    print("  ❌ python-pptx 미설치"); sys.exit(1)

OUT_DIR = "/home/kcloud/claude-code/projects/mermaidexport/.omc/research/color-fit-progress"
PASS = 0
FAIL = 0
SMALL_FONT = []

MIN_PT = 9.0

for pptx_path in sorted(glob.glob(f"{OUT_DIR}/diagram_*.pptx")):
    import os
    label = os.path.basename(pptx_path)
    try:
        prs = Presentation(pptx_path)
        for slide_idx, slide in enumerate(prs.slides):
            for shape in slide.shapes:
                if not shape.has_text_frame: continue
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.size is not None:
                            pt = run.font.size / 12700
                            if pt < MIN_PT:
                                SMALL_FONT.append(f"{label}/슬라이드{slide_idx+1}: {pt:.1f}pt < {MIN_PT}pt ('{run.text[:20]}')")
                                FAIL += 1
                            else:
                                PASS += 1
    except Exception as e:
        print(f"  ❌ {label} 파싱 오류: {e}"); FAIL += 1

if FAIL == 0:
    print(f"  ✅ PASS: 폰트 ≥ 9pt — {PASS}개 run 검사 완료")
else:
    print(f"  ❌ FAIL: {FAIL}개 run이 9pt 미만")
    for v in SMALL_FONT[:5]: print(f"    → {v}")
PYEOF

# ── 5. 트랙 U: progressbar 검증 ──────────────────────────────
log "=== [5] 트랙 U progressbar 검증 ==="

# 5-A. /api/convert 응답 속도 < 1초
log "5-A: /api/convert 속도 테스트"
START_TS=$(date +%s%3N)
PROG_JOB=$(curl -sf -X POST "$BASE_URL/api/convert" \
    -F "file=@${REF_MD}" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")
END_TS=$(date +%s%3N)
ELAPSED=$((END_TS - START_TS))
if [ -n "$PROG_JOB" ] && [ "$PROG_JOB" != "None" ]; then
    if [ "$ELAPSED" -lt 1000 ]; then
        pass_ "/api/convert 응답 ${ELAPSED}ms < 1000ms (즉시 반환)"
    else
        fail_ "/api/convert 응답 ${ELAPSED}ms ≥ 1000ms (느림 — progressbar 패턴 아님)"
    fi
else
    fail_ "/api/convert 응답 파싱 실패"
    PROG_JOB=""
fi

# 5-B. /api/progress/{job_id} 엔드포인트 존재 확인
log "5-B: /api/progress 엔드포인트"
if [ -n "${PROG_JOB:-}" ]; then
    PROGRESS_RESP=$(curl -sf "$BASE_URL/api/progress/$PROG_JOB" 2>/dev/null || echo "ENDPOINT_MISSING")
    if [ "$PROGRESS_RESP" = "ENDPOINT_MISSING" ]; then
        fail_ "/api/progress/{job_id} 엔드포인트 없음 (track-U 미완료)"
    else
        PROG_STATUS=$(echo "$PROGRESS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))" 2>/dev/null || echo "parse_err")
        if [ "$PROG_STATUS" != "parse_err" ]; then
            pass_ "/api/progress 응답 OK (status=$PROG_STATUS)"
        else
            fail_ "/api/progress 응답 파싱 실패: $PROGRESS_RESP"
        fi
    fi
else
    fail_ "job_id 없어 /api/progress 테스트 스킵"
fi

# 5-C. 프론트엔드 접속 확인
log "5-C: 프론트엔드 progressbar UI"
FRONTEND_RESP=$(curl -sf "$BASE_URL/" 2>/dev/null | head -c 2000 || echo "")
if echo "$FRONTEND_RESP" | grep -qi "progress\|progressbar\|진행"; then
    pass_ "프론트엔드에 progressbar 관련 마크업 확인"
elif [ -n "$FRONTEND_RESP" ]; then
    fail_ "프론트엔드 응답 있으나 progressbar 마크업 미확인 (track-U 미완료 가능)"
else
    fail_ "프론트엔드 응답 없음"
fi

# ── 6. 회귀 가드 ────────────────────────────────────────────
log "=== [6] 회귀 가드 ==="

# 6-A. smoke_test_er
log "6-A: smoke_test_er"
SMOKE_OUT=$(cd "$PROJECT_ROOT/backend" && python3 -c "
import sys
sys.path.insert(0, '.')
from converters import smoke_test_er
results = smoke_test_er()
ok = sum(1 for v in results.values() if v.startswith('OK'))
fail = sum(1 for v in results.values() if 'FAIL' in v)
print(f'OK={ok} FAIL={fail}')
for k, v in results.items():
    if 'FAIL' in v:
        print(f'  FAIL: {k}: {v}')
" 2>&1)
echo "$SMOKE_OUT"
if echo "$SMOKE_OUT" | grep -q "FAIL=0"; then
    SMOKE_OK=$(echo "$SMOKE_OUT" | grep -oP 'OK=\K[0-9]+')
    pass_ "smoke_test_er ${SMOKE_OK}/12 PASS"
else
    fail_ "smoke_test_er 실패 항목 있음"
fi

# 6-B. assert_pptx (graph PPTX들)
log "6-B: assert_pptx (graph 다이어그램)"
PPTX_GRAPH_FILES=$(ls "$OUT_DIR"/diagram_*.pptx 2>/dev/null | head -8 | tr '\n' ' ')
if [ -n "$PPTX_GRAPH_FILES" ]; then
    ASSERT_OUT=$(cd "$PROJECT_ROOT/backend" && python3 "$SCRIPTS_DIR/assert_pptx.py" \
        --graph $PPTX_GRAPH_FILES 2>&1)
    echo "$ASSERT_OUT" | tail -5
    ASSERT_PASS=$(echo "$ASSERT_OUT" | grep -c "\[PASS\]" || true)
    ASSERT_FAIL=$(echo "$ASSERT_OUT" | grep -c "\[FAIL\]" || true)
    if [ "${ASSERT_FAIL:-0}" -eq 0 ]; then
        pass_ "assert_pptx graph: ${ASSERT_PASS} PASS, 0 FAIL"
    else
        fail_ "assert_pptx graph: ${ASSERT_PASS} PASS, ${ASSERT_FAIL} FAIL"
    fi
fi

# 6-C. custGeom 검증
log "6-C: custGeom 검증"
if [ -n "$PPTX_GRAPH_FILES" ]; then
    CUSTGEOM_OUT=$(cd "$PROJECT_ROOT/backend" && python3 "$SCRIPTS_DIR/assert_pptx.py" \
        --custgeom $PPTX_GRAPH_FILES 2>&1)
    echo "$CUSTGEOM_OUT" | tail -5
    CG_PASS=$(echo "$CUSTGEOM_OUT" | grep -c "\[PASS\]" || true)
    CG_FAIL=$(echo "$CUSTGEOM_OUT" | grep -c "\[FAIL\]" || true)
    if [ "${CG_FAIL:-0}" -eq 0 ]; then
        pass_ "custGeom: ${CG_PASS} PASS, 0 FAIL"
    else
        fail_ "custGeom: ${CG_PASS} PASS, ${CG_FAIL} FAIL"
    fi
fi

# ── 최종 결과 ────────────────────────────────────────────────
echo ""
echo "========================================================"
echo " track-V 종합 검증 결과: ${TOTAL_PASS} PASS  |  ${TOTAL_FAIL} FAIL"
echo " 시각: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================================"
if [ "$TOTAL_FAIL" -eq 0 ]; then
    echo " 🎉 ALL PASS — track-V 검증 완료"
    exit 0
else
    echo " ⚠️  ${TOTAL_FAIL}개 실패 — 검토 필요"
    exit 1
fi
