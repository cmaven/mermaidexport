# ============================================================
# test_text_metrics.py: text_metrics.py 단위 테스트 모음
# 상세: estimate_text_size_px / _visual_width / _wrap_by_max_chars 검증
# 생성일: 2026-05-14
# ============================================================

import pytest
import sys
import os

# backend/ 루트를 sys.path에 추가 (pytest를 backend/에서 실행)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from converters.text_metrics import estimate_text_size_px, _visual_width, _wrap_by_max_chars


# ──────────────────────────────────────────────
# 보조 상수 (구현과 동기화)
# ──────────────────────────────────────────────
FONT_SIZE = 13
PADDING = 24
LINE_HEIGHT = int(FONT_SIZE * 1.5)   # 19px
SINGLE_LINE_H = LINE_HEIGHT + PADDING  # 43px


# ──────────────────────────────────────────────
# 1. 한글 짧은 라벨
# ──────────────────────────────────────────────
def test_korean_short_label():
    """'API 서버' → 너비 80-150px 범위, 단일 라인"""
    w, h = estimate_text_size_px("API 서버")
    assert 80 <= w <= 150, f"너비 범위 초과: {w}"
    # 짧은 라벨 → 줄바꿈 없음 → 최소 높이
    assert h == SINGLE_LINE_H, f"단일 라인 높이 불일치: {h}"


# ──────────────────────────────────────────────
# 2. 긴 한글·영문 혼합 + <br/> 개행
# ──────────────────────────────────────────────
def test_long_korean_label():
    """'DriverInstallReconciler<br/>(mode=job)' → 너비 200-300px, 2 라인"""
    w, h = estimate_text_size_px("DriverInstallReconciler<br/>(mode=job)")
    assert 200 <= w <= 300, f"너비 범위 초과: {w}"
    # <br/>로 인해 2개 세그먼트 → 2 라인
    expected_h = 2 * LINE_HEIGHT + PADDING
    assert h == expected_h, f"2 라인 높이 불일치: {h} (기대 {expected_h})"


# ──────────────────────────────────────────────
# 3. 가장 긴 라벨 — NPU 워커 노드
# ──────────────────────────────────────────────
def test_npu_longest_label():
    """'Worker Node (NPU — Furiosa Warboy/RNGD)' → max_w 근접 너비 + 줄바꿈 2라인"""
    label = "Worker Node (NPU — Furiosa Warboy/RNGD)"
    w, h = estimate_text_size_px(label)

    # 긴 라벨 → 너비가 max_w(360)에 가까운 값이어야 함
    assert 280 <= w <= 360, f"너비가 예상 범위(280~360)를 벗어남: {w}"

    # 39자 라벨은 max_chars=28 기준 단어 줄바꿈 → 2라인
    expected_h = 2 * LINE_HEIGHT + PADDING
    assert h == expected_h, f"2 라인 높이 불일치: {h} (기대 {expected_h})"


# ──────────────────────────────────────────────
# 4. <br/> 정규화 — 높이가 라인 수에 비례
# ──────────────────────────────────────────────
def test_br_normalization():
    """<br/> 포함 텍스트의 높이가 단일 라인 대비 2배여야 함"""
    _, h_with_br = estimate_text_size_px("hello<br/>world")
    _, h_no_br = estimate_text_size_px("helloworld")

    # <br/> 있을 때 → 2라인, 없을 때 → 1라인
    assert h_with_br == 2 * LINE_HEIGHT + PADDING, f"2라인 높이: {h_with_br}"
    assert h_no_br == SINGLE_LINE_H, f"1라인 높이: {h_no_br}"
    assert h_with_br > h_no_br, "<br/> 포함 높이가 더 커야 함"


def test_br_variant_normalization():
    """<br> (슬래시 없는 형태)도 정상 정규화되어야 함"""
    _, h_br = estimate_text_size_px("A<br>B")
    _, h_br_slash = estimate_text_size_px("A<br/>B")
    assert h_br == h_br_slash, f"<br>와 <br/> 높이 불일치: {h_br} vs {h_br_slash}"


# ──────────────────────────────────────────────
# 5. max_w 캡: 매우 긴 라벨도 width <= 520
# ──────────────────────────────────────────────
def test_max_w_cap():
    """매우 긴 라벨 → 너비 == max_w(520)"""
    # "W" 70개: visual 충분히 커서 raw > 520
    very_long = "W" * 70
    w, _ = estimate_text_size_px(very_long)
    assert w == 520, f"max_w 캡 미적용: {w}"


def test_max_w_cap_realistic():
    """실제 긴 서비스명도 max_w(520)를 초과하지 않아야 함"""
    long_label = "ThisIsAVeryLongServiceNameThatExceedsMaximumWidthCapForDrawioNode"
    w, _ = estimate_text_size_px(long_label)
    assert w <= 520, f"max_w 초과: {w}"


# ──────────────────────────────────────────────
# 6. min_w 플로어: 빈/짧은 라벨도 width >= 100
# ──────────────────────────────────────────────
def test_min_w_floor_empty():
    """빈 문자열 → min_w(100) 플로어 적용"""
    w, _ = estimate_text_size_px("")
    assert w >= 100, f"min_w 플로어 미적용 (빈 문자열): {w}"
    assert w == 100, f"min_w 정확히 100이어야 함: {w}"


def test_min_w_floor_short():
    """짧은 단일 문자 라벨 → min_w(100) 플로어 적용"""
    w, _ = estimate_text_size_px("x")
    assert w >= 100, f"min_w 플로어 미적용 (단일 문자): {w}"
    assert w == 100, f"min_w 정확히 100이어야 함: {w}"


# ──────────────────────────────────────────────
# 내부 함수 단위 테스트
# ──────────────────────────────────────────────
def test_visual_width_korean():
    """한글 문자는 1.0em 가중치"""
    w = _visual_width("가나다")
    assert abs(w - 3.0) < 1e-9, f"한글 가중치 오류: {w}"


def test_visual_width_english():
    """영문 대소문자 가중치"""
    w_upper = _visual_width("ABC")
    w_lower = _visual_width("abc")
    assert abs(w_upper - 3 * 0.62) < 1e-9, f"대문자 가중치 오류: {w_upper}"
    assert abs(w_lower - 3 * 0.55) < 1e-9, f"소문자 가중치 오류: {w_lower}"


def test_visual_width_digit():
    """숫자 가중치 0.55"""
    w = _visual_width("123")
    assert abs(w - 3 * 0.55) < 1e-9, f"숫자 가중치 오류: {w}"


def test_wrap_no_wrap_needed():
    """짧은 텍스트는 줄바꿈 없이 1라인"""
    lines = _wrap_by_max_chars("short text", max_chars=28)
    assert lines == ["short text"], f"줄바꿈 불필요한데 분리됨: {lines}"


def test_wrap_splits_at_boundary():
    """max_chars 초과 시 단어 경계에서 줄바꿈"""
    # "Worker Node (NPU — Furiosa"(26chars) + " Warboy/RNGD)"(13more=39) → split
    label = "Worker Node (NPU — Furiosa Warboy/RNGD)"
    lines = _wrap_by_max_chars(label, max_chars=28)
    assert len(lines) == 2, f"2라인으로 분리되어야 함: {lines}"
    assert "Warboy/RNGD)" in lines[1], f"두 번째 라인 내용 오류: {lines}"


def test_wrap_empty_string():
    """빈 문자열 → 1개 빈 라인"""
    lines = _wrap_by_max_chars("")
    assert lines == [''], f"빈 문자열 처리 오류: {lines}"
