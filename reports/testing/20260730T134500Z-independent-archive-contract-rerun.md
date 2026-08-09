# 20260730 独立归档契约重运行记录

## 范围

本记录固化对冻结工作树的独立隔离重运行。测试角色只执行预先定义的编译和单元测试命令；未读取、枚举或修改既有 `archives/` 内容，也未访问日常 `data/`。未编辑项目文件、未安装依赖、未修改网络或系统配置。

## 结果

- 编译检查通过。
- 归档契约回归：`14 passed, 0 failed, 0 skipped, 1 warning`，耗时 `12.50s`。
- 完整单元套件：`110 passed, 0 failed, 2 skipped, 2 warnings`，耗时 `474.51s`。
- 两项跳过是未配置的 PostgreSQL 集成环境门禁；未被表述为通过。
- warning 来自负向安全测试刻意构造的重复 ZIP 成员，涉及 `records.json` 和 `manifest.json`；不代表构建候选档案包含重复成员。

## 覆盖结论

本次归档契约回归确认：Unicode 转义和带引号的 JSON 凭据式键会被拒绝；封存后的同类结构化键篡改会被独立验证器拒绝；前序登记会选择最新未接受快照并受封存验证；同名目录或 ZIP 冲突不会被覆盖；活动 run ID 锁会拒绝并发构建。

本记录只证明独立测试范围内的实际结果。`DEF-ARCH-004` 至 `DEF-ARCH-009` 仍需对新生成候选档案进行独立验收；真实 PostgreSQL、物理 Docker Compose、Edge 和 Chrome 黑盒 GUI 门禁继续保持 `blocked`。
