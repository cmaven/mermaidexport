# ============================================================
# text_metrics.py: 텍스트 시각적 너비·높이를 CSS px 단위로 추정하는 유틸
# 상세: Mermaid 라벨의 font-size 기반 em 가중치 + 단어 줄바꿈으로 (w, h) 산출
# 생성일: 2026-05-14
# ============================================================

import re
import unicodedata


def _visual_width(s: str) -> float:
    """
    문자열의 시각적 너비를 em 단위(float)로 반환한다.

    가중치:
      - 한글·CJK 전각 문자 (east_asian_width W/F): 1.0 em
      - 영문 대문자: 0.62 em
      - 영문 소문자: 0.55 em
      - 숫자: 0.55 em
      - 공백·기호 등 기타: 0.45 em
    """
    width = 0.0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ('W', 'F'):
            width += 1.0
        elif ch.isupper():
            width += 0.62
        elif ch.islower():
            width += 0.55
        elif ch.isdigit():
            width += 0.55
        else:
            width += 0.45
    return width


def _wrap_by_max_chars(text: str, max_chars: int = 28) -> list:
    """
    텍스트를 단어 경계에서 max_chars 문자 수 기준으로 줄바꿈한다.

    Args:
        text: 단일 라인 텍스트 (\\n 없음)
        max_chars: 한 줄 최대 문자 수 (기본값 28)

    Returns:
        줄바꿈된 라인 목록 (최소 1개 요소)
    """
    if not text:
        return ['']

    words = text.split(' ')
    lines = []
    current_line = ''
    current_len = 0

    for word in words:
        word_len = len(word)
        if not current_line:
            current_line = word
            current_len = word_len
        elif current_len + 1 + word_len <= max_chars:
            current_line += ' ' + word
            current_len += 1 + word_len
        else:
            lines.append(current_line)
            current_line = word
            current_len = word_len

    lines.append(current_line)
    return lines


def estimate_text_size_px(
    text: str,
    font_size_px: int = 13,
    padding_px: int = 24,
    min_w: int = 100,
    max_w: int = 360,
) -> tuple:
    """
    Mermaid 라벨 텍스트의 추정 크기를 (width_px, height_px)로 반환한다.

    처리 순서:
      1. <br/> / <br> → \\n 정규화 (mermaid 개행 태그 처리)
      2. \\n 분리 → 세그먼트별 시각적 너비(em) 계산
      3. 너비: max(min_w, min(max_w, max_segment_visual * font_size + 2*padding))
      4. 높이: 각 세그먼트를 max_chars=28로 단어 줄바꿈 → 총 라인 수 * line_height + padding

    Args:
        text: 원본 라벨 텍스트 (Mermaid <br/> 포함 가능)
        font_size_px: 폰트 크기 (기본 13px)
        padding_px: 좌우/상하 여백 각각 (기본 24px, 양쪽 합산 시 2배)
        min_w: 최소 너비 (기본 100px)
        max_w: 최대 너비 캡 (기본 360px)

    Returns:
        (width_px, height_px) — CSS px 단위 (96 dpi 기준)
    """
    # 1. <br/>, <br> 정규화 → \n
    normalized = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    segments = normalized.split('\n') if normalized else ['']

    max_visual = 0.0
    total_lines = 0

    for segment in segments:
        # 너비: 세그먼트 전체(줄바꿈 전) 시각적 너비를 기준으로 노드 폭 결정
        seg_visual = _visual_width(segment)
        if seg_visual > max_visual:
            max_visual = seg_visual

        # 높이: 단어 단위 줄바꿈 후 라인 수 집계
        wrapped_lines = _wrap_by_max_chars(segment)
        total_lines += len(wrapped_lines)

    # 너비 계산: 양쪽 padding 포함
    raw_width = int(max_visual * font_size_px + 2 * padding_px)
    width_px = max(min_w, min(max_w, raw_width))

    # 높이 계산: 1.5× line-height + 상하 padding
    line_height_px = int(font_size_px * 1.5)
    height_px = total_lines * line_height_px + padding_px

    return (width_px, height_px)
