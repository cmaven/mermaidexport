# WORK_LOG

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
