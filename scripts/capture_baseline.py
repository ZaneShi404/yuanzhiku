"""Task 0 基线快照（可靠性/安全加固计划配套工具）。

在隔离数据根上创建应用并输出三份可机器比较的基线文件：
- openapi.json   完整 OpenAPI 文档（路径/方法/响应结构对照用）
- schema.json    SQLite 表/列/索引清单
- chain.json     合成数据证据链记录（粘贴导入 → 解析 → 证据 → 引用 → 知识发布 → 检索的成功状态码与响应字段名）

用法：python scripts/capture_baseline.py <runtime-dir>
不触碰日常数据根；仅写 <runtime-dir>/baseline-data 与三份 JSON。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("YUANZHIKU_EMBEDDED_WORKER", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


def dump_schema(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schema: dict[str, dict] = {}
        for table in tables:
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()
            schema[table] = {
                "columns": [
                    {"name": column[1], "type": column[2], "notnull": bool(column[3]), "pk": bool(column[5])}
                    for column in columns
                ],
                "indexes": sorted(row[1] for row in indexes if not str(row[1]).startswith("sqlite_")),
            }
        return schema
    finally:
        connection.close()


def field_names(payload: object) -> list[str]:
    return sorted(payload) if isinstance(payload, dict) else []


def build_chain(client: TestClient) -> dict:
    """合成证据链：粘贴导入 → 解析作业 → 证据 → 引用 → 知识发布 → 检索。"""
    record: dict = {"steps": []}

    def step(name: str, response, extra: dict | None = None) -> dict:
        entry = {
            "name": name,
            "status": response.status_code,
            "fields": field_names(response.json() if response.content else None),
        }
        if extra:
            entry.update(extra)
        record["steps"].append(entry)
        return response.json() if response.content else {}

    imported = step(
        "import_paste",
        client.post(
            "/api/v1/imports/paste",
            json={
                "title": "基线合成来源",
                "text": "# 基线合成\n\n用于可机器比较证据链快照的合成中文文本。",
                "rights": "owned",
            },
        ),
    )
    version_id = imported.get("content_version", {}).get("id")
    source_id = imported.get("source", {}).get("id")

    # run-once 会先处理随 lifespan 入队的 backup/integrity_sample 低优先级作业；
    # 只在 parse 作业到达终态时才结束循环。
    parse_state = "queued"
    for _ in range(50):
        payload = client.post("/api/v1/jobs/run-once").json()
        job = payload.get("job") if isinstance(payload, dict) else None
        if isinstance(job, dict) and job.get("kind") == "parse" and job.get("state") in {"succeeded", "failed", "blocked", "cancelled"}:
            parse_state = job["state"]
            break
    record["parse_state"] = parse_state

    representations = step("representations", client.get(f"/api/v1/documents/{version_id}/representations"))
    extraction = next((item for item in representations if item["kind"] == "extraction"), None)
    evidence_items = []
    if extraction is not None:
        evidence_items = step(
            "evidence", client.get(f"/api/v1/representations/{extraction['id']}/evidence")
        )
    if evidence_items:
        citation = step(
            "citation",
            client.post(f"/api/v1/citations?evidence_id={evidence_items[0]['id']}"),
        )
        knowledge = step(
            "knowledge",
            client.post(
                "/api/v1/knowledge",
                json={"kind": "fact", "statement": "基线合成事实。", "evidence_ids": [evidence_items[0]["id"]]},
            ),
        )
        if citation and knowledge.get("id"):
            step("knowledge_publish", client.post(f"/api/v1/knowledge/{knowledge['id']}/publish"))

    step("search", client.get("/api/v1/search?q=基线合成"))
    record["source_id"] = source_id
    record["version_id"] = version_id
    return record


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    runtime = Path(sys.argv[1]).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    data_root = runtime / "baseline-data"

    app = create_app(data_root, acquire_lock=False)
    with TestClient(app) as client:
        openapi = client.get("/openapi.json").json()
        chain = build_chain(client)

    (runtime / "openapi.json").write_text(json.dumps(openapi, ensure_ascii=False, indent=1), encoding="utf-8")
    (runtime / "schema.json").write_text(
        json.dumps(dump_schema(data_root / "state" / "knowledge.db"), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (runtime / "chain.json").write_text(json.dumps(chain, ensure_ascii=False, indent=1), encoding="utf-8")
    parse_ok = chain.get("parse_state") == "succeeded"
    print(f"openapi paths={len(openapi.get('paths', {}))} chain={chain.get('parse_state')} written={runtime}")
    return 0 if parse_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
