# 验收映射

| 需求组 | 实现证据 | 自测标识 | 独立复核重点 |
|---|---|---|---|
| REQ-001..004 | `backend/app/main.py`, `frontend/src/App.tsx`, `scripts/start-windows.ps1` | T-API-001, T-UI-001 | IPv4、端口持久化、无敏感日志 |
| REQ-010..014 | `services/imports.py`, `adapters/storage.py`, `adapters/parsers.py` | T-ING-001, T-ING-002 | 大文件空间、Docling 可用时路径、损坏文件 |
| REQ-020..025 | `services/documents.py`, `services/search.py` | T-ING-002, T-KNOW-001 | locator 完整性、检索默认范围 |
| REQ-030..031 | `services/external_cards.py` | T-EXT-001 | 代码审计无网络访问抖音 |
| REQ-032..034 | `services/jobs.py`, `services/lifecycle.py` | T-JOB-001, T-LIFE-001 | 断路器、重试、取消、清理 |
| REQ-040..042 | `services/transfers.py` | T-BACK-001 | 快照一致性、restore 新根、保留策略 |
| REQ-043..046 | `main.py`, `docker-compose.yml`, docs | T-API-001, T-COMP-001 | endpoint 覆盖、容器镜像和数据库迁移 |
