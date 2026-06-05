# ============================================================
# palette.py: 모든 변환기가 공유하는 통합 색상 팔레트 (draw.io 예제 톤)
# 상세: PNG, PPTX, draw.io, Excalidraw 모두 동일한 색상 사용.
#       draw.io(svg)example.png 에서 추출한 의미별 다색(semantic) 팔레트 —
#       파랑/빨강/금색/초록/보라/주황/분홍 + 크림색 컨테이너, 슬레이트 엣지.
# 생성일: 2026-04-07 | 수정일: 2026-06-05
# ============================================================

# 노드 색상 팔레트: (fill_hex, stroke_hex)
# draw.io 예제 톤: 밝은 파스텔 fill + 진한 동일 계열 stroke (인덱스 순환 적용)
NODE_COLORS = [
    ("#D8E8F8", "#0080C0"),   # blue   — 사용자/프론트엔드 톤
    ("#F8E0E0", "#D82020"),   # red    — GPU/LLM 톤
    ("#F8F0C0", "#C88800"),   # gold   — 저장소 톤
    ("#D8F8E0", "#10A048"),   # green  — 챗봇/서비스 톤
    ("#E8E8F8", "#7838E8"),   # violet — 모니터링 톤
    ("#F8E8D0", "#E06000"),   # orange — LiteLLM/공유 톤
    ("#F8E0F0", "#D82070"),   # pink   — 임베딩 톤
    ("#EDEFF2", "#586878"),   # slate  — 여유/중립 톤
]

# 서브그래프 색상: (fill_hex, stroke_hex) — 클러스터 컨테이너
# draw.io 예제: 크림색 배경 + 금색 점선 테두리를 메인으로, 옅은 톤들로 다양성
SUBGRAPH_COLORS = [
    ("#FAF8E6", "#C88800"),   # cream — 저장소/메인 컨테이너 (금색 점선)
    ("#FBEAEA", "#D82020"),   # pale red
    ("#E6F1FB", "#0080C0"),   # pale blue
    ("#E7F8EC", "#10A048"),   # pale green
    ("#EEEBFB", "#7838E8"),   # pale violet
    ("#FBF0E2", "#E06000"),   # pale orange
]

# 텍스트/라인 공통 색상 (draw.io 예제 추출)
TEXT_COLOR = "#1E293B"          # 본문 텍스트 (진한 슬레이트)
LINE_COLOR = "#405068"          # 엣지/legend (예제 엣지 슬레이트)
EDGE_LABEL_BG = "#FFFFFF"
SUBGRAPH_BORDER_FALLBACK = "#C88800"   # 금색 (예제 컨테이너 테두리)

# 브랜드 액센트 (PPTX 상단 구분선 등에 활용 가능)
PRIMARY_ACCENT = "#7838E8"      # 예제 모니터링 보라


def get_node_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 노드 색상 (fill, stroke) 반환."""
    return NODE_COLORS[index % len(NODE_COLORS)]


def get_subgraph_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 서브그래프 색상 (fill, stroke) 반환."""
    return SUBGRAPH_COLORS[index % len(SUBGRAPH_COLORS)]
