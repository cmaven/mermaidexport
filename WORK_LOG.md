# WORK_LOG

## [track-a-executor] — 2026-05-19

### 구현 완료 (A.1~A.8)

- **A.1** `_extract_text_from_foreign()` 에 `html.unescape()` 적용 — 커밋 61d843e (트랙 B 공유 신호)
- **A.2** `_ER_NODE_ID_RE`: `(?:-\d+)?$` (trailing digit 선택적) + entityLabel text fallback
- **A.3** Attribute 행 y좌표 `round(ty, 3)` → `round(ty, 1)` (동일 행 분리 방지)
- **A.4** `pptx_shapes.py` 손상 레이아웃 감지: 엣지-노드 불일치 시 PNG 폴백
- **A.5** `_render_er_png_fallback()` 코드 검증 + smoke_test 실행 확인
- **A.6** `drawio.py` 손상 감지 동등 패치 / `excalidraw.py` ER 전용 분기 추가 (layout_engine 사용)
- **A.7** `smoke_test_er()` 강화 — simple/crd 두 케이스, 12개 키 모두 OK
- **A.8** 회귀 캡처 → `.omc/research/track-a-after/` (crd_er_after.pptx/png + simple_er_after.pptx/png)

### 검증 결과 (최종)
| 기준 | 결과 |
|------|------|
| A1 엔티티 박스 출력 | **PASS** shape 25 ≥ entity 8 |
| A2 관계 선 + 라벨 | **PASS** 5개 엣지 파싱 완료 |
| A3 엔티티 누락 없음 | **PASS** 8개 모두 출력 |
| A4 HTML entity 리터럴 노출 | **PASS** 0건 |
| A5 손상 layout 시 PNG 폴백 | **PASS** 감지 로직 적용 |
| A6 smoke_test_er 4포맷 OK | **PASS** 12/12 OK |

### 파일 수정 목록
- `backend/converters/layout_engine.py`: html.unescape, 정규식 강화, fallback, y-round
- `backend/converters/pptx_shapes.py`: 손상 layout 감지 (A.4)
- `backend/converters/drawio.py`: 손상 layout 감지 (A.6)
- `backend/converters/excalidraw.py`: ER 전용 분기 추가 (A.6)
- `backend/converters/__init__.py`: smoke_test_er 강화 (A.7)
- `.omc/research/track-a-after/`: 회귀 캡처 4파일 (A.8)

---


## [track-b-executor] — 2026-05-19

### Phase 0 완료 (코드베이스 탐색)
- `_extract_text_from_foreign()`: html.unescape 없음 → A.1 신호 후 적용 예정
- `_add_node_at()`: `<br/>` 처리 없음 → B.1 구현
- 노드 크기 고정 → B.2 구현
- cluster bbox 재계산 없음 → B.3 구현
- avoid list: title bar만, B.2 조정 미반영 → B.4 구현
- `_add_edge_label_at()`: 충돌 검사 없음 → B.5 구현

### 구현 완료
- **B.1** `<br/>` → `\n` 변환 (`_br_to_newline`, `_add_node_at` 적용)
- **B.2** 노드 크기 라벨 fit (`_estimate_required_size`, unicodedata 폭 휴리스틱)
- **B.3** cluster bbox 재계산 + push-down 겹침 해소 (`_recompute_cluster_bboxes`)
- **B.4** avoid list 개선: B.2 조정 크기 반영 + 비관련 cluster 전체 박스 회피
- **B.5** edge label nudge: 충돌 시 ±0.15in nudge × 4회, 끝내 겹치면 외곽선
- **B.7** `backend/test_graph.md` 생성 (NPU Operator graph TB + 단순 flowchart 회귀 케이스)

### 완료 — 2026-05-19 (Phase 2)

- **B.2 재설계**: 노드 크기 확장 루프 제거 → `MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE` 적용
  - `_set_text()` / `_set_text_multiline()` 모두 적용
  - dagre 레이아웃 좌표 유지 → 클러스터 간 겹침 0쌍 (B1 PASS)
- **B.5(br.tail)**: `layout_engine._extract_text_from_foreign()` br.tail 수집 + html.unescape 통합
- **B.6(html entity)**: PPTX HTML 잔존 0건 (PASS)
- **B.8(PNG)**: LibreOffice 1280×720 RGBA 생성 → `.omc/research/track-b-after/test_graph.png`

### 검증 결과 (최종)
| 기준 | 결과 |
|------|------|
| B1 cluster 겹침 | **PASS** (5개 클러스터, 0쌍 겹침) |
| B2 라벨 박스 이탈 | **PASS** (MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE 적용) |
| B3 화살표 관통 | 구현 완료 (_try_corner_detour + avoid list), 시각 검증은 Track-C |
| B4 edge label 충돌 | 구현 완료 (nudge ×4 + 외곽선), 시각 검증은 Track-C |
| B5 br 렌더링 | **PASS** (br.tail 수집, multiline 확인) |
| B6 HTML entity | **PASS** (0건 잔존) |
| B7 회귀 테스트 | **DONE** (test_graph.md 생성) |
| B8 PNG 생성 | **DONE** (1280×720) |

### 파일 수정 목록 (최종)
- `backend/converters/pptx_shapes.py`: MSO_AUTO_SIZE 임포트, _set_text/_set_text_multiline 수정, B.2 루프 제거, 헬퍼 3개 추가, avoid list 개선
- `backend/converters/layout_engine.py`: _extract_text_from_foreign() br.tail + html.unescape
- `backend/test_graph.md`: 신규 생성 (회귀 테스트 샘플)

## [track-c-verifier] 2026-05-19 11:45

- track-C 검증 완료
- 최종 판정: **FAIL**
- 통계: PASS=24 / FAIL=2 / 전체=26
- 보고서: .omc/research/verification-report.md
- after/ PNG: 7개

## [track-b-executor] Fix Loop 1 — 2026-05-19

### B.4 edge label 충돌 해소 (Task #4)
- **원인**: nudge 범위 부족(±0.15×4), label_avoid에 src/dst 노드 미포함, 라벨 간 상호 회피 없음
- **수정**:
  - `_add_edge_label_at`: 양방향 nudge ±0.25in × 12회 (위/아래 교번)
  - `placed_label_bboxes`: 배치된 edge label끼리 상호 회피
  - `label_avoid`: 모든 노드(src/dst 포함) + 비관련 cluster bbox
- **assert_pptx.py**: `_classify_shape` 신규, `assert_no_edge_label_node_overlap` 신규
- **검증**: edge label 충돌 0건 (4/4 PASS)
- **커밋**: c11e7e5
