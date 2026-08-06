# Design: Graphviz waypoints + orthogonal freeform edges (Approach A)

**Date:** 2026-08-05  
**Status:** Approved for implementation  
**Problem:** PPTX/PNG for complex flowcharts (e.g. `13_architecture_reference.md` §1.1) show severe shape/arrow/text overlap; PowerPoint editing is impractical.

## Root cause

1. `layout_graph.compute_graphviz_layout` already returns edge waypoints + label positions.
2. `apply_graphviz_layout` copies only node/cluster boxes and **discards** edge paths.
3. PPTX draws `MSO_CONNECTOR.ELBOW` shape-to-shape → lines cut through nodes.
4. `_scale_to_fit` crushes content into fixed 16:9 → text overflow inside nodes.
5. draw.io graphviz path likewise uses exit/entry anchors only, not waypoints.

## Goals

- Orthogonal (or polyline) edges that follow Graphviz routes and avoid boxes.
- Edge labels at Graphviz `label_pos` (offset), not centered on a chord through shapes.
- Prefer **expand slide** over aggressive downscale; keep readable min font/node size.
- Same layout IR for PPTX and draw.io.

## Non-goals

- Custom Sugiyama/A* router (Approach B).
- Mermaid SVG harvest (Approach C).
- Pixel-perfect hand-tuned draw.io aesthetics.

## Design

### IR

Extend `Edge` with:

- `points: list[tuple[float,float]]` — inch coords, slide space  
- `label_pos: Optional[tuple[float,float]]` — inch coords  

`apply_graphviz_layout` matches layout edges to diagram edges by `(source, target)` order and fills these fields (px→inch + content origin).

### PPTX

- If `edge.points` has ≥2 points: draw stroke-only freeform polyline + triangle arrowhead; dashed when arrow style is dotted.
- Else: keep ELBOW fallback.
- Label textbox near `label_pos` (or path midpoint) with small offset.
- After layout: compute content bounds → set `slide_width`/`slide_height` to `max(16:9, bounds+margin)`. Soft-scale only if exceeding a hard cap (e.g. 40"×22.5"), never below min node height ~0.4".

### draw.io

- In `_build_flowchart_xml_from_layout`, inject `<Array as="points"><mxPoint …/></Array>` from matched `layout.edges` waypoints (absolute page px).
- Expand `pageWidth`/`pageHeight` from layout bbox (already partial).

### Graphviz tuning

- Increase `nodesep` / `ranksep` slightly for clearer gutters.
- Prefer `splines=ortho`, keep polyline/spline fallback.

## Success criteria

- Converting first mermaid block of `13_architecture_reference.md` yields PPTX/PNG where edges largely avoid node interiors and Worker Node labels remain readable.
- Existing unit tests still pass; add tests for waypoint retention and polyline vs elbow branch.

## Files

- `backend/converters/pptx_shapes.py` — Edge IR, apply layout, freeform edges, slide size
- `backend/converters/drawio.py` — waypoint Array on edges
- `backend/converters/layout_graph.py` — spacing constants
- `backend/tests/test_orthogonal_edges.py` — new
