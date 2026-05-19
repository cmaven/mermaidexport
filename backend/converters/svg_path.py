# ============================================================
# svg_path.py: SVG path d 속성 파서 — OOXML custGeom 변환용
# 상세: M/L/C/Q/Z(절대) + m/l/c/q/z(상대) 지원, 상대 명령은 절대로 변환.
#       단위 테스트는 파일 하단 __main__ 블록에 포함.
# 생성일: 2026-05-19
# ============================================================

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# SVG 숫자 토크나이저 (부호 포함, 지수 표기 지원)
_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
# SVG path 명령 문자
_CMD_CHARS = set("MLCQZmlcqz")


@dataclass
class PathCmd:
    """SVG path 하나의 명령 (절대 좌표로 정규화됨)."""
    op: str                               # 'M' | 'L' | 'C' | 'Q' | 'Z'
    pts: list[tuple[float, float]] = field(default_factory=list)


def parse_svg_path(d: str) -> list[PathCmd]:
    """SVG path `d` 속성 문자열을 절대 좌표 PathCmd 리스트로 파싱.

    지원 명령:
        M/m  moveTo (절대/상대)
        L/l  lineTo
        C/c  cubicBezTo (6개 숫자: x1 y1 x2 y2 x y)
        Q/q  quadBezTo  (4개 숫자: x1 y1 x y)
        Z/z  closePath

    미지원 명령(H/V/S/T/A 등)은 무시 — 폴백에서 polyline 사용.

    Returns:
        PathCmd 리스트. 빈 리스트 = 파싱 불가.
    """
    # re.split으로 명령 문자와 숫자 시퀀스를 분리
    tokens = re.split(r"([MLCQZmlcqz])", d.strip())
    cmds: list[PathCmd] = []
    cur_x, cur_y = 0.0, 0.0
    i = 0

    while i < len(tokens):
        tok = tokens[i].strip()
        i += 1
        if not tok or tok not in _CMD_CHARS:
            continue

        op = tok
        # 바로 다음 토큰이 숫자 시퀀스
        nums: list[float] = []
        if i < len(tokens) and tokens[i].strip() not in _CMD_CHARS:
            nums = [float(x) for x in _NUM_RE.findall(tokens[i])]
            i += 1

        if op in "Zz":
            cmds.append(PathCmd("Z", []))

        elif op == "M":
            # M x y [x2 y2 ...] — 첫 쌍은 moveTo, 추가 쌍은 lineTo
            for j in range(0, len(nums) - 1, 2):
                x, y = nums[j], nums[j + 1]
                cur_x, cur_y = x, y
                cmds.append(PathCmd("M" if j == 0 else "L", [(x, y)]))

        elif op == "m":
            for j in range(0, len(nums) - 1, 2):
                cur_x += nums[j]
                cur_y += nums[j + 1]
                cmds.append(PathCmd("M" if j == 0 else "L", [(cur_x, cur_y)]))

        elif op == "L":
            for j in range(0, len(nums) - 1, 2):
                x, y = nums[j], nums[j + 1]
                cur_x, cur_y = x, y
                cmds.append(PathCmd("L", [(x, y)]))

        elif op == "l":
            for j in range(0, len(nums) - 1, 2):
                cur_x += nums[j]
                cur_y += nums[j + 1]
                cmds.append(PathCmd("L", [(cur_x, cur_y)]))

        elif op == "C":
            for j in range(0, len(nums) - 5, 6):
                x1, y1, x2, y2, x, y = nums[j:j + 6]
                cur_x, cur_y = x, y
                cmds.append(PathCmd("C", [(x1, y1), (x2, y2), (x, y)]))

        elif op == "c":
            for j in range(0, len(nums) - 5, 6):
                x1 = cur_x + nums[j];     y1 = cur_y + nums[j + 1]
                x2 = cur_x + nums[j + 2]; y2 = cur_y + nums[j + 3]
                cur_x += nums[j + 4];     cur_y += nums[j + 5]
                cmds.append(PathCmd("C", [(x1, y1), (x2, y2), (cur_x, cur_y)]))

        elif op == "Q":
            for j in range(0, len(nums) - 3, 4):
                x1, y1, x, y = nums[j:j + 4]
                cur_x, cur_y = x, y
                cmds.append(PathCmd("Q", [(x1, y1), (x, y)]))

        elif op == "q":
            for j in range(0, len(nums) - 3, 4):
                x1 = cur_x + nums[j];     y1 = cur_y + nums[j + 1]
                cur_x += nums[j + 2];     cur_y += nums[j + 3]
                cmds.append(PathCmd("Q", [(x1, y1), (cur_x, cur_y)]))
        # else: 미지원 명령(H, V, S, T, A) → 무시

    return cmds


def path_bounding_box(
    cmds: list[PathCmd],
) -> Optional[tuple[float, float, float, float]]:
    """모든 PathCmd 의 점 집합에서 (min_x, min_y, max_x, max_y) 반환.

    제어점(bezier control points)도 포함하여 보수적(conservative) bbox 계산.
    점이 없으면 None 반환.
    """
    xs: list[float] = []
    ys: list[float] = []
    for cmd in cmds:
        for x, y in cmd.pts:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# ──────────────────────────────────────────────────────────────────────
# 단위 테스트 (python backend/converters/svg_path.py 로 직접 실행 가능)
# ──────────────────────────────────────────────────────────────────────

def _run_tests() -> None:
    tests_pass = 0
    tests_fail = 0

    def check(name: str, got, expected):
        nonlocal tests_pass, tests_fail
        if got == expected:
            print(f"  [PASS] {name}")
            tests_pass += 1
        else:
            print(f"  [FAIL] {name}")
            print(f"         got:      {got}")
            print(f"         expected: {expected}")
            tests_fail += 1

    # Case 1: M only
    cmds = parse_svg_path("M 100 200")
    check("M-only op", cmds[0].op, "M")
    check("M-only pts", cmds[0].pts, [(100.0, 200.0)])

    # Case 2: M + L
    cmds = parse_svg_path("M 0 0 L 100 50")
    check("M+L len", len(cmds), 2)
    check("L pts", cmds[1].pts, [(100.0, 50.0)])

    # Case 3: M + C (cubic bezier)
    cmds = parse_svg_path("M 0 0 C 50 -30 150 -30 200 0")
    check("M+C len", len(cmds), 2)
    check("C op", cmds[1].op, "C")
    check("C pts count", len(cmds[1].pts), 3)
    check("C endpoint", cmds[1].pts[2], (200.0, 0.0))

    # Case 4: M + L + C + Z
    cmds = parse_svg_path("M 10 10 L 50 10 C 80 10 90 50 50 80 Z")
    check("M+L+C+Z len", len(cmds), 4)
    check("Z op", cmds[3].op, "Z")
    check("Z pts empty", cmds[3].pts, [])

    # Case 5: 상대 명령 m + c
    cmds = parse_svg_path("m 100 200 c 10 -20 30 -20 40 0")
    check("relative m op", cmds[0].op, "M")
    check("relative m abs", cmds[0].pts, [(100.0, 200.0)])
    check("relative c op", cmds[1].op, "C")
    # control1 = (100+10, 200-20) = (110, 180)
    check("relative c ctrl1", cmds[1].pts[0], (110.0, 180.0))
    # endpoint = (100+40, 200+0) = (140, 200)
    check("relative c end", cmds[1].pts[2], (140.0, 200.0))

    # Case 6: bounding box
    cmds = parse_svg_path("M 10 20 L 100 80 C 30 10 50 90 60 50")
    bb = path_bounding_box(cmds)
    check("bbox not None", bb is not None, True)
    if bb:
        check("bbox min_x", bb[0], 10.0)
        check("bbox max_y", bb[3], 90.0)

    print(f"\n결과: PASS={tests_pass}, FAIL={tests_fail}")
    if tests_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    print("=== svg_path.py 단위 테스트 ===")
    _run_tests()
