# ADR-002：证据只追加

状态：接受。

解析和人工修改均新增 representation；evidence 记录对应 representation、artifact、content version、解析配置与规范摘录哈希。不得就地替换已引用文本，保证 citation 可复现（`REQ-020..022`）。
