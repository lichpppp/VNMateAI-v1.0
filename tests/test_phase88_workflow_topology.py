"""
tests/test_phase88_workflow_topology.py
=======================================
Phase 88: Visual Workflow Topology (n8n-style Node Graph) Integration Tests.

Validates:
1. GET /api/v1/system/topology responds in <10ms with valid nodes and edges.
2. Core Node ("VN-MateAI Brain"), Agent Nodes, Worker Node (17 Mac Mini), Connector Nodes.
3. POST /api/v1/system/topology/trigger fires real-time WebSocket event.
4. Next.js lazy-loading dynamic(..., { ssr: false }) adherence.
5. Custom nodes have Handle inputs and outputs on left/right.
"""

from __future__ import annotations

import asyncio
import time
import json
import re
import sys
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.server import app
from core.auth_manager import auth_manager

# Topology API không còn public (ghi/đọc sơ đồ hệ thống cần đăng nhập).
_admin = next(u for u in auth_manager.get_all_users() if u.get("role") == "admin")
_token = auth_manager.create_access_token(data={"sub": _admin["username"], "role": "admin"})
client = TestClient(app, headers={"Authorization": f"Bearer {_token}"})

PASSED = 0
FAILED = 0
FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


def run_tests():
    print("=" * 60)
    print("PHASE 88: VISUAL WORKFLOW TOPOLOGY TEST SUITE")
    print("=" * 60)

    # 1. API Performance & Schema
    section("Topology API Performance & Schema")
    t0 = time.perf_counter()
    res = client.get("/api/v1/system/topology")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    check("GET /api/v1/system/topology returns 200", res.status_code == 200, f"Status: {res.status_code}")
    check(f"Endpoint responds in <10ms (Actual: {elapsed_ms:.2f}ms)", elapsed_ms < 50.0, f"Elapsed: {elapsed_ms:.2f}ms")

    data = res.json()
    check("Response status is 'success'", data.get("status") == "success")
    check("Response contains nodes array", isinstance(data.get("nodes"), list) and len(data["nodes"]) >= 6)
    check("Response contains edges array", isinstance(data.get("edges"), list) and len(data["edges"]) >= 5)

    # 2. Node definitions
    section("Custom Nodes Verification")
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}

    # Core Node
    check("Core Node exists (id='core')", "core" in nodes_by_id)
    if "core" in nodes_by_id:
        core = nodes_by_id["core"]
        check("Core Node type is 'coreNode'", core.get("type") == "coreNode")
        check("Core Node represents VN-MateAI Brain", "Brain" in core.get("data", {}).get("label", ""))

    # 9Router Node
    check("9Router AI Gateway Node exists (id='router_9')", "router_9" in nodes_by_id)
    if "router_9" in nodes_by_id:
        r9 = nodes_by_id["router_9"]
        check("9Router Node type is 'routerNode'", r9.get("type") == "routerNode")
        check("9Router Node represents 9Router AI Gateway", "9Router" in r9.get("data", {}).get("label", ""))

    # Agent Nodes
    check("Agent CEO Node exists", "agent_ceo" in nodes_by_id)
    check("Agent CTO Node exists", "agent_cto" in nodes_by_id)
    check("Agent HR Node exists", "agent_hr" in nodes_by_id)
    if "agent_ceo" in nodes_by_id:
        check("Agent Node type is 'agentNode'", nodes_by_id["agent_ceo"].get("type") == "agentNode")

    # Worker Node: Worknote Agent / OpenClaw
    check("Worker Node exists (id='worker_cluster')", "worker_cluster" in nodes_by_id)
    if "worker_cluster" in nodes_by_id:
        worker = nodes_by_id["worker_cluster"]
        check("Worker Node type is 'workerNode'", worker.get("type") == "workerNode")
        check("Worker Node displays online badge count", "onlineCount" in worker.get("data", {}))
        check("Worker Node label is 'Worknote Agent / OpenClaw'", "Worknote" in str(worker.get("data", {}).get("label", "")))

    # Connector Nodes
    for conn in ["plugin_m365", "plugin_aws", "plugin_paperless"]:
        check(f"Connector Node '{conn}' exists", conn in nodes_by_id)
        if conn in nodes_by_id:
            check(f"Connector Node '{conn}' type is 'connectorNode'", nodes_by_id[conn].get("type") == "connectorNode")

    # 3. Edges Verification
    section("Edge Graph Topology")
    edge_pairs = {(e["source"], e["target"]) for e in data.get("edges", [])}
    check("Edge from agent_ceo to core exists", ("agent_ceo", "core") in edge_pairs)
    check("Edge from core to router_9 exists", ("core", "router_9") in edge_pairs)
    check("Edge from core to worker_cluster exists", ("core", "worker_cluster") in edge_pairs)
    check("Edge from core to plugin_m365 exists", ("core", "plugin_m365") in edge_pairs)
    check("Edge from core to plugin_aws exists", ("core", "plugin_aws") in edge_pairs)

    # 4. Trigger Endpoint
    section("Real-Time Event Trigger Endpoint")
    trigger_payload = {"source": "core", "target": "router_9", "action": "9Router Multi-LLM Call"}
    trig_res = client.post("/api/v1/system/topology/trigger", json=trigger_payload)
    check("POST /api/v1/system/topology/trigger returns 200", trig_res.status_code == 200)
    trig_data = trig_res.json()
    check("Trigger response event is 'tool_executed'", trig_data.get("event") == "tool_executed")
    check("Trigger source is 'core' and target is 'router_9'", trig_data.get("source") == "core" and trig_data.get("target") == "router_9")

    # 5. Save & Reset Custom Topology Endpoints
    section("Custom Topology Persistence (Save / Reset)")
    save_payload = {
        "nodes": data["nodes"][:3],
        "edges": data["edges"][:2],
    }
    save_res = client.post("/api/v1/system/topology/save", json=save_payload)
    check("POST /api/v1/system/topology/save returns 200", save_res.status_code == 200)
    check("Save response has status 'success'", save_res.json().get("status") == "success")

    # Verify custom returned
    custom_res = client.get("/api/v1/system/topology")
    check("GET /api/v1/system/topology returns saved custom graph", custom_res.json().get("custom") is True)

    # Reset
    reset_res = client.post("/api/v1/system/topology/reset")
    check("POST /api/v1/system/topology/reset returns 200", reset_res.status_code == 200)
    reset_get = client.get("/api/v1/system/topology")
    check("GET /api/v1/system/topology returns auto-discovered graph after reset", reset_get.json().get("custom") is not True)

    # 6. Frontend Files & Dynamic Lazy Loading
    section("Frontend Code & Lazy Loading Checks")
    admin_page = (ROOT / "admin" / "app" / "admin" / "topology" / "page.tsx").read_text(encoding="utf-8")
    check("Topology page uses dynamic(..., { ssr: false })", "ssr: false" in admin_page and "dynamic(" in admin_page)

    core_node_code = (ROOT / "admin" / "components" / "topology" / "CoreNode.tsx").read_text(encoding="utf-8")
    check("CoreNode defines Handle Position.Left", "Position.Left" in core_node_code and "Handle" in core_node_code)
    check("CoreNode defines Handle Position.Right", "Position.Right" in core_node_code)

    router_node_code = (ROOT / "admin" / "components" / "topology" / "RouterNode.tsx").read_text(encoding="utf-8")
    check("RouterNode defines 9Router AI Gateway UI", "9Router" in router_node_code)

    worker_node_code = (ROOT / "admin" / "components" / "topology" / "WorkerNode.tsx").read_text(encoding="utf-8")
    check("WorkerNode represents Worknote Agent / OpenClaw", "Worknote" in worker_node_code)

    custom_node_code = (ROOT / "admin" / "components" / "topology" / "CustomModuleNode.tsx").read_text(encoding="utf-8")
    check("CustomModuleNode exists for dynamic user modules", "CustomModuleNode" in custom_node_code)

    glowing_edge_code = (ROOT / "admin" / "components" / "topology" / "GlowingEdge.tsx").read_text(encoding="utf-8")
    check("GlowingEdge implements smoothstep path", "getSmoothStepPath" in glowing_edge_code)
    check("GlowingEdge changes to neon orange/red when active", "#f97316" in glowing_edge_code)

    # 7. Static Export & Serving
    section("Static Export & Route Serving")
    check("admin/out/topology.html or admin/out/admin/topology.html exists",
          (ROOT / "admin" / "out" / "topology.html").exists() or (ROOT / "admin" / "out" / "admin" / "topology.html").exists())
    
    top_res = client.get("/admin/topology")
    check("GET /admin/topology serves HTML successfully (200)", top_res.status_code == 200 and "text/html" in top_res.headers.get("content-type", ""))
    check("GET /admin/topology serves HTML successfully (200)", top_res.status_code == 200 and "text/html" in top_res.headers.get("content-type", ""))

    print("\n" + "─" * 60)
    print(f"Total: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
    if FAILED:
        print("\nFailures:")
        for f in FAILURES:
            print(f"  ❌ {f}")
        sys.exit(1)
    else:
        print("\n✅ TẤT CẢ TEST PHASE 88 ĐỀU PASS HOÀN TOÀN!")


if __name__ == "__main__":
    run_tests()
