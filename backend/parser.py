# ============================================================
# parser.py: MD 파일에서 Mermaid 코드블록 추출
# 상세: ```mermaid 코드블록을 파싱하여 리스트로 반환
# 생성일: 2026-04-07 | 수정일: 2026-04-07
# ============================================================

import re
from typing import TypedDict


class MermaidBlock(TypedDict):
    index: int
    title: str
    mermaid_code: str


def parse_mermaid_blocks(md_text: str) -> list[MermaidBlock]:
    """MD 텍스트에서 ```mermaid 코드블록을 추출한다.

    블록 바로 위의 제목(# 또는 ## 등)이 있으면 title로 사용한다.
    """
    if not md_text or not md_text.strip():
        return []

    pattern = re.compile(
        r"```mermaid\s*\n(.*?)```",
        re.DOTALL,
    )

    lines = md_text.split("\n")
    blocks: list[MermaidBlock] = []
    idx = 0

    for match in pattern.finditer(md_text):
        code = match.group(1).strip()
        if not code:
            continue

        # 블록 시작 위치에서 바로 위의 제목 라인 탐색
        start_pos = match.start()
        text_before = md_text[:start_pos].rstrip()
        title = ""
        if text_before:
            last_line = text_before.split("\n")[-1].strip()
            heading_match = re.match(r"^#{1,6}\s+(.+)$", last_line)
            if heading_match:
                title = heading_match.group(1).strip()

        if not title:
            title = f"Diagram {idx + 1}"

        blocks.append(MermaidBlock(index=idx, title=title, mermaid_code=code))
        idx += 1

    return blocks
