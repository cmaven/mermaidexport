# Orthogonal Edge Layout (Approach A) — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Keep Graphviz edge waypoints through PPTX/draw.io so lines avoid boxes; expand slide instead of crushing.

**Architecture:** layout_graph → Edge.points/label_pos → freeform PPTX + draw.io Array points.

**Tech:** python-pptx freeform, mxGraph points Array, existing Graphviz JSON.

---

### Task 1: Edge IR + apply_graphviz_layout keeps waypoints

**Files:**
- Modify: `backend/converters/pptx_shapes.py`
- Test: `backend/tests/test_orthogonal_edges.py`

**Steps:** Extend Edge; map layout edges; replace `_scale_to_fit` with expand-slide helper; increase nodesep/ranksep in layout_graph.

### Task 2: PPTX freeform polylines + labels

**Files:**
- Modify: `backend/converters/pptx_shapes.py` (`_add_connector_polyline`, render loop, slide size)

### Task 3: draw.io waypoint Array

**Files:**
- Modify: `backend/converters/drawio.py` (`_add_edge_cell`, `_build_flowchart_xml_from_layout`)

### Task 4: Verify on §1.1 architecture diagram

Run converter on first block of `13_architecture_reference.md`; inspect PNG/PPTX for overlaps.
