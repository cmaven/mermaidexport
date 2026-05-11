# ============================================================
# palette.py: 모든 변환기가 공유하는 통합 색상 팔레트 (SaaS 마케팅 스타일)
# 상세: PNG, PPTX, draw.io, Excalidraw 모두 동일한 색상 사용.
#       Scaleway / Vercel / Render 제품 페이지 톤 — 보라 primary,
#       핑크(managed) / 파랑(workload) fill, 1px stroke, 그림자/그라데이션 없음.
# 생성일: 2026-04-07 | 수정일: 2026-05-11
# ============================================================

# 노드 색상 팔레트: (fill_hex, stroke_hex)
# SaaS 스타일: 파랑/핑크/보라 3톤을 메인으로, 부드러운 보조 톤으로 다양성 확보
NODE_COLORS = [
    ("#DBEAFE", "#6B46FF"),   # blue fill   + primary purple stroke (workload)
    ("#FCE7F3", "#6B46FF"),   # pink fill   + primary purple stroke (managed)
    ("#EDE9FE", "#6B46FF"),   # violet fill + primary purple stroke
    ("#E0F2FE", "#0369A1"),   # sky fill    + sky stroke
    ("#FCE7F3", "#BE185D"),   # pink fill   + magenta stroke
    ("#DBEAFE", "#1E40AF"),   # blue fill   + navy stroke
    ("#F5F3FF", "#6B46FF"),   # light violet
    ("#FFE4E6", "#BE185D"),   # rose fill   + magenta stroke
]

# 서브그래프 색상: (fill_hex, stroke_hex) — 클러스터 컨테이너 (대시 보라 테두리 톤)
SUBGRAPH_COLORS = [
    ("#F5F3FF", "#6B46FF"),   # violet tint — primary cluster (k8s 등)
    ("#FCE7F3", "#6B46FF"),   # pink tint   — managed plane (capsule, operator)
    ("#EFF6FF", "#6B46FF"),   # blue tint   — workload group
    ("#FAF5FF", "#7C3AED"),   # very-light violet
    ("#FDF2F8", "#BE185D"),   # very-light pink
    ("#F0F9FF", "#0369A1"),   # very-light sky
]

# 텍스트/라인 공통 색상 (노트 §부록 기준)
TEXT_COLOR = "#1E293B"          # 본문 텍스트 (slate-900)
LINE_COLOR = "#475569"          # 엣지/legend (slate-600)
EDGE_LABEL_BG = "#FFFFFF"
SUBGRAPH_BORDER_FALLBACK = "#6B46FF"

# 브랜드 액센트 (PPTX 상단 구분선 등에 활용 가능)
PRIMARY_ACCENT = "#6B46FF"      # SaaS primary purple


def get_node_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 노드 색상 (fill, stroke) 반환."""
    return NODE_COLORS[index % len(NODE_COLORS)]


def get_subgraph_color(index: int) -> tuple[str, str]:
    """인덱스에 맞는 서브그래프 색상 (fill, stroke) 반환."""
    return SUBGRAPH_COLORS[index % len(SUBGRAPH_COLORS)]
