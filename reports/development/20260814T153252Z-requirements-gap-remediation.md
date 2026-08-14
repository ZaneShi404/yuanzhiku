# 需求缺口修复报告（REQ 审计后续）

- 日期：2026-08-14（UTC `20260814T153252Z`）
- 角色：development（开发自测；本报告不构成独立测试/验收结论）
- 范围：2026-08-14 全项目 REQ 审计发现的缺口与偏差修复；决策由用户人工拍板（见每项"决策"）
- 关联：`docs/requirements.md`（冻结基线）、`docs/v1-2-requirements.md`、`docs/acceptance-matrix.md`、`docs/test-plan.md`

## 0. 先行修复（审计实测阶段）

**yt-dlp 平台标题 GBK 乱码（真实 bug，REQ-047.6/6.2 标题回填意图）**

- 现象：全量单测 1 失败——`test_synthetic_download_captures_platform_title`：中文区域 Windows 上 yt-dlp 子进程按 GBK 输出 `--print` 标题，`_extract_title` 按 UTF-8 解码，真实下载会把乱码标题落库。
- 修复：`backend/app/adapters/downloader.py` `_subprocess_environment` 增加 `env["PYTHONIOENCODING"] = "utf-8"`（约 :512），强制子进程 stdout/stderr UTF-8。
- 验证：该用例由失败转通过；`test_video_download.py` 全模块 67 通过（4 分 58 秒）。

## 1. 明确缺口修复（3 项）

### 1.1 REQ-021：Docling 路径 PDF evidence 无页码 locator

- 缺口：`adapters/parsers.py` Docling 分支仅 `export_to_markdown()` 返回整文，evidence 落 `documents.py:24` 的 `page:"unknown"` 兜底，不满足"PDF locator 为页码"。
- 决策（用户）：安装真实 docling 实测（非桩测试）。
- 修复：
  - `parsers.py` 新增 `_docling_segments(document)`：遍历 `document.texts` 条目 provenance（`prov[0].page_no`），按页聚合文本（页内 `\n`、页间 `\n\n`，offset 跟踪与 pypdf 路径同算法），产出 `pdf_page_char_range` segments；**任何非空条目缺页码即返回 None**，调用方回退整文路径，绝不产出页码未知的页级证据。
  - Docling 分支改用该函数构造 `ParsedDocument(segments=...)`。
- 实测：经清华镜像安装 `docling==2.120.1`（PyPI 直连两次 10054 中断后改镜像）；用真实 docling-core 类构建两页合成 `DoclingDocument`，monkeypatch `convert` 走完整 `parse()` 路径。新测试 `tests/unit/test_docling_segments.py` 3 用例通过（页码/offset 自洽、无 prov 返回 None、无 prov 整文回退）。
- 依赖纪律：docling 安装曾把 `python-docx` 从锁定 1.1.2 冲到 1.2.0，已降回 1.1.2（`pip check` 无冲突、docling 导入与测试正常），既有 12 行零漂移；`backend/requirements.lock` 按"直接依赖单列"惯例追加 `docling==2.120.1`；`docs/dependency-installation.md` 记录安装/锁定/许可证（MIT）。

### 1.2 REQ-023：前端引用详情缺三要素

- 缺口：`App.tsx` citation-detail 只渲染标题/context/locator 标签；缺来源状态、定位动作、可展开上下文。
- 修复（`frontend/src/App.tsx`）：
  - `CitationDetail` 类型补 `location_action`（后端 `documents.py:125` 本已返回）；
  - 引用详情渲染 `<Status value={citation.processing_state}/>`（来源状态）；
  - 新增「定位」按钮 `locateCitation`：按 `location_action.evidence_id` 在当前表示 evidence 列表查找并复用 `locateEvidence`；找不到时中文提示切换到对应表示；
  - context 默认折叠（80 字截断）+「展开上下文/收起上下文」切换；后端 300 字截断不变。
- 验证：`npm run build` 通过；T-INT-001 断言后端引用详情含 `processing_state` 与 `location_action` 字段。

### 1.3 REQ-045：集成测试缺失 + 数据根不强制

- 缺口：`tests/integration/` 为空；compose 的 `YUANZHIKU_COMPOSE_DATA_ROOT` 仅强制非空。
- 决策（用户）：本地 TestClient 全链路集成测试（本机无 Docker，不写 compose 实测）。
- 修复：
  - 新建 `tests/integration/test_local_full_chain.py`（T-INT-001）：paste 导入→run-once→representations/evidence→citation（含 REQ-023 字段断言）→knowledge 发布→search 命中→backup→export（confirmed）→reimport（幂等无冲突）→软删/恢复/purge/404；数据根落 `tests/runtime/`。
  - `backend/app/core/config.py` 新增 `compose_data_root()` 守卫：解析路径必须是仓库 `tests/runtime/compose-<run-id>`，日常数据根与其他位置直接拒绝（中文报错）。
- 验证：2 用例通过。`docs/test-plan.md` 增 T-INT-001 行；`docs/acceptance-matrix.md` REQ-043..046 行补集成测试证据。

## 2. 结构性偏差处理（3 项，按用户拍板）

### 2.1 REQ-004 模块边界 → 文档映射（不重构代码）

- `docs/architecture.md` 新增"冻结模块边界 → 代码载体映射"表：11 个具名模块逐一对应实际载体（sources/taxonomy/settings 为端点+repository 无独立服务模块；evidence/knowledge 合于 `services/documents.py`），并登记命名粒度偏差；分层约束（fastapi 仅 `main.py`、sqlite3 仅 `adapters/`、domain 零框架依赖）经 import 扫描确认。

### 2.2 REQ-013 模型下载 → 强化锁校验 + 文档声明（不实现下载通道）

- `parsers.py` `_model_status`：锁条目强制 `name/version/source_url/license/cache_path/sha256` 六个非空字符串字段，缺一即"未批准"。
- `docs/dependency-installation.md`：显式声明"按锁下载通道刻意未实现，空名单=零下载"及条目 schema；日后批准模型须先实现并独立审核合规下载通道。
- 新测试 `tests/unit/test_model_lock.py`：六字段逐一缺失拒绝、完整条目+哈希核验通过、哈希不匹配拒绝（2 用例通过）。

### 2.3 REQ-016 video_time_range locator → 后端定义补齐

- `backend/app/domain/media.py`：新增 `VIDEO_METADATA_LOCATOR_TYPE`/`VIDEO_TIME_RANGE_LOCATOR_TYPE` 常量与 `video_time_range_locator(start_ms, end_ms)` 工厂（非负整数、start<end、拒绝 bool/浮点/字符串），未来转写证据只能经此构造。
- `ports/media.py` `MediaAiPort` docstring 明确该约束（REQ-016/017）。
- 测试：`tests/unit/test_video_media.py::test_video_time_range_locator_validation` 通过。

## 3. 小修（按用户拍板范围）

- REQ-044：PDF 面板 header 增加「外部打开」显式链接（`target="_blank" rel="noreferrer"`，指向已有 `/sources/{id}/original`，该端点带 CSP sandbox 头）。
- REQ-030「人工定位」文案按用户决定**不动**。

## 4. 治理形式（按用户拍板）

- `docs/v1-2-requirements.md` 文首状态由 DRAFT 同步为"已并入 requirements.md 冻结基线并已完成实现"，注明 `release_readiness` 仍 blocked。
- 补 `reports/testing/20260813T072039Z-T-VID-005-real-platform-acceptance.json` sidecar（`report-schema-v1.json` 双件约定）：`report_kind/author_role=acceptance`、`independence=non_independent`（如实，operator-assisted）、`decision_scope=archive_local`、`verdict=accepted`；Markdown 原文未动。

## 5. 验证汇总

- 全量回归：`tests/unit + tests/integration` **214 passed, 2 skipped, 0 failed**（20 分 12 秒；2 个 skipped 为历史既有 PostgreSQL 外部服务用例）。
- 前端 `npm run build` 通过（TypeScript 编译含在新类型/渲染路径）。
- `pip check` 无依赖冲突；docling 物理导入验证通过（`docling 2.120.1`、`DocumentConverter` 可导入）。

## 6. 残留事项（如实登记）

1. Docling 运行时仍默认不可达（`models.lock.json` 空白名单、无合规下载通道）——REQ-021 修复保证"启用即有页码"，但真实模型转换的端到端行为未经真实模型验证（受 REQ-013 纪律所限，属预期）。
2. 无 prov 页码的 Docling 文档回退整文路径，其 evidence 仍为 `page:"unknown"` 兜底（仅在文档模型异常时触发）。
3. `requirements.lock` 为"直接依赖钉死"惯例，docling 的传递依赖未逐包锁定（与既有 12 包同惯例；如需全量传递锁定须另行决策）。
4. 真实 compose 集成测试仍未编写（本机无 Docker）；`compose_data_root` 守卫已落地，供未来 compose 测试强制使用。
5. `release_readiness` 保持 **blocked**，本次修复不改变发布门禁状态。
6. 前端引用详情三项为构建级验证，未经真实浏览器黑盒确认（既有 GATE-BROWSER-BLACKBOX 门禁仍 blocked）。
