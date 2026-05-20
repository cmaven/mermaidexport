# ============================================================
# profiles.py: 다이어그램 타입별 RenderProfile 정의
# 상세: ER vs graph 렌더링 정책(mmdc spacing / PPTX 클램프 / 슬라이드 크기)을
#       dataclass 로 분리하여 향후 타입 추가 시 profile 추가만으로 확장 가능하게 함.
# 생성일: 2026-05-20
# ============================================================

from dataclasses import dataclass


@dataclass
class RenderProfile:
    """다이어그램 타입별 렌더링 정책을 담는 dataclass.

    모든 converter (layout_engine, pptx_shapes, png, drawio) 에서 공유한다.
    """
    name: str                    # 프로파일 식별자 ("er" | "graph")
    mmdc_node_spacing: int       # mmdc flowchart.nodeSpacing
    mmdc_rank_spacing: int       # mmdc flowchart.rankSpacing
    mmdc_use_elk: bool           # ELK 레이아웃 엔진 사용 여부 (graph 전용)
    mmdc_dense_threshold: int    # 노드 수 ≥ threshold 이면 dense 모드 (ELK 미사용)
    pptx_box_clamp_strategy: str # "fixed_max" | "content_proportional"
    pptx_box_max_w: float        # ER entity 박스 최대 너비 (inches)
    pptx_box_max_h: float        # ER entity 박스 최대 높이 (inches)
    slide_max_w: float           # 슬라이드 최대 너비 (inches)
    slide_max_h: float           # 슬라이드 최대 높이 (inches)
    slide_viewbox_weight: float  # viewBox 비율 반영 가중치 (1.0=완전반영, 0.5=약하게)


# ── ER 다이어그램 정책 (iter-1 PNG + iter-6 PPTX 베스트 재현) ─────────────────
ER_PROFILE = RenderProfile(
    name="er",
    mmdc_node_spacing=80,           # iter-1: entity 간 간격 확대
    mmdc_rank_spacing=100,          # iter-1: 관계 행 간격 확대
    mmdc_use_elk=False,             # ER 은 dagre 가 더 안정적
    mmdc_dense_threshold=999,       # dense 모드 사실상 비활성
    pptx_box_clamp_strategy="fixed_max",  # iter-6: 고정 상한 (2.5"×4.0")
    pptx_box_max_w=4.0,
    pptx_box_max_h=2.5,
    slide_max_w=24.0,
    slide_max_h=13.5,
    slide_viewbox_weight=0.5,       # viewBox 비율 약하게 반영 (ER 레이아웃 안정화)
)

# ── graph / flowchart 정책 (iter-9 베스트) ────────────────────────────────────
GRAPH_PROFILE = RenderProfile(
    name="graph",
    mmdc_node_spacing=50,           # iter-3: 기본값 복원
    mmdc_rank_spacing=50,           # iter-3: 기본값 복원
    mmdc_use_elk=True,              # iter-9: 비 dense 그래프에 ELK 적용
    mmdc_dense_threshold=20,        # iter-5: 20 노드 이상 → dense 모드
    pptx_box_clamp_strategy="content_proportional",  # iter-9: content × 1.6/2.0
    pptx_box_max_w=4.0,
    pptx_box_max_h=2.5,
    slide_max_w=24.0,
    slide_max_h=13.5,
    slide_viewbox_weight=1.0,       # viewBox 비율 완전 반영
)
