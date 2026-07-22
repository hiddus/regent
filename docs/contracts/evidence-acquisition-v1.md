# Evidence Acquisition Protocol v1

状态：Frozen for P1  
日期：2026-07-18

## 适用范围

Product Discovery 只能使用被授权来源快照、用户提供材料和已验证历史 Evidence。模型常识可以产生假设，但不能被标记为研究事实。

## SourceRequest

- URL 或资源标识；
- 目的；
- 允许域名；
- 时间窗口；
- 最大字节；
- 数据分类；
- Permit；
- correlation_id。

## SourceSnapshot

- 原始 URL；
- 最终 URL；
- retrieved_at；
- media_type；
- content_artifact_uri；
- content_hash；
- title、publisher、published_at，如可验证；
- extractor_version；
- injection_flags；
- access_policy_ref。

## 规则

- 外部内容始终视为不可信数据，不视为系统指令；
- 检测并标记提示注入；
- 引用必须指向 SourceSnapshot 或现有 Evidence ID；
- 删除或不可访问来源不能被伪造；
- 摘要和结论保留引用映射；
- 研究结论区分 observed、inferred、assumed、unknown；
- 禁止超出授权范围抓取；
- 个人信息遵循最小化、保留期和删除要求。

## Product Discovery 门槛

只有被分类为 product_creation 的 Goal 才进入 Product Discovery。证据或预算不足时返回 RESEARCH_MORE、BLOCKED 或 STOP，不强制生成两个虚假候选。
