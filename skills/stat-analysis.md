# Stat Analysis

对外部 `stat-analysis` 技能包做统计分析适配，按需读取参考资料，并在其项目根目录用 `uv run python scripts/stat_engine.py ...` 执行分析。

## 适用场景

- 用户要做描述统计、频数表、列联表、t 检验、ANOVA、相关分析、线性/逻辑/计数回归、混合模型
- 用户提到 SAS / SPSS / Stata 的常见统计命令，希望给出 Python 等价流程
- 用户提供 CSV / Excel / Parquet / JSON / feather 数据文件，希望完成分析或先做方法选择

## 技能根目录

- 外部项目根目录：`Z:\agentworkspace\demo_ragtag_workspace\stat-analysis`
- 主入口脚本：`Z:\agentworkspace\demo_ragtag_workspace\stat-analysis\scripts\stat_engine.py`
- 方法选择参考：`Z:\agentworkspace\demo_ragtag_workspace\stat-analysis\references\method_selection_guide.md`

## 使用约定

- 运行命令时，把 `workdir` 设为 `Z:\agentworkspace\demo_ragtag_workspace\stat-analysis`
- 使用 `uv run python scripts/stat_engine.py ...`，不要假设全局 Python 环境已经装好依赖
- 需要完整规则时，用 `read` 读取 `references/*.md`，不要凭记忆猜测统计方法或报告格式
- 如果研究问题、因变量、自变量、数据结构、样本量不清楚，先问用户，不要直接跑分析

## 推荐工作流

1. 先澄清研究问题、DV、IV、数据结构和样本量。
2. 用 `inspect` 检查数据列、类型、缺失、前几行和基础统计。
3. 如方法不明确，先读取 `references/method_selection_guide.md` 再选方法。
4. 对参数方法先跑 `check-assumptions`，必要时提示稳健或非参数替代。
5. 再用 `analyze` 执行正式分析，并输出统计量、p 值、区间、效应量和通俗解释。

## 常用命令

```text
uv run python scripts/stat_engine.py inspect --file <data_path>
uv run python scripts/stat_engine.py check-assumptions --file <data_path> --method <method> --dv <dv> --iv <iv1,iv2,...>
uv run python scripts/stat_engine.py analyze --file <data_path> --method <method> --dv <dv> --iv <iv1,iv2,...> --options '{"key":"value"}' --save-figures <dir>
```

## 关键参考文件

- `references/method_selection_guide.md`：方法选择决策树
- `references/assumption_checks.md`：假设检验与违背时的替代方案
- `references/effect_size_guide.md`：效应量计算与解释
- `references/output_templates.md`：APA 风格输出模板
- `REVIEW.md`：已实现范围、已知缺口和设计差距

## 注意事项

- 当前外部技能包有一部分方法尚未实现；如果用户请求未实现方法，先读取 `REVIEW.md` 或 `SKILL.md` 确认，再说明限制并给最接近替代方案
- 不要跳过数据检查和假设检查
- 不要只报 p 值；应同时报告统计量、自由度、区间和效应量
- 不要修改用户原始数据文件，必要时在输出目录写派生结果
