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

### 대기 중
- A.1 신호 (`.omc/state/track_a_html_unescape_done`) → `_extract_text_from_foreign()` html.unescape 적용 (B.6 일부)
- B.8 PNG 캡처 (docker/LibreOffice 의존)

### 파일 수정 목록
- `backend/converters/pptx_shapes.py`: import 추가, 헬퍼 함수 3개, `_add_node_at`·`_add_edge_label_at`·`_render_pptx_from_layout` 수정
- `backend/test_graph.md`: 신규 생성 (회귀 테스트 샘플)
