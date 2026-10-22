# A 股分析系统优化执行计划

> 执行方式：在当前会话逐项实施；开始实现时使用 superpowers:executing-plans。未经用户同意不启动子代理。
> 状态：用户已批准实施；开发候选与54项离线测试已交付，真实联通及视觉验收待通过，尚未上线。见 [验收记录](../../evidence/acceptance.md)。下方原计划清单作为设计基线保留，执行进度以任务列表为准。
> 日期：2026-10-22

**Goal:** 在保留现有微信入口与管理后台的基础上，使 A 股分析有可信数据、可核对证据、可重复筛选和可追踪结果。
**Architecture:** 沿用 application/domain/infrastructure/repository 分层；数据快照、结构化决策与展示报告分离。规则计算候选与风险，LLM 解释证据；历史规则回测和 AI 前向评估分别实施。
**Tech Stack:** Python >=3.11、现有 FastAPI/SQLite/aiosqlite 和数据源适配器；离线测试采用 unittest。第一批不强制引入 Qlib、CZSC 或新的数据库。
**Spec:** 本文“已确认范围与设计约束”承接用户已确认的上一轮优化方案，作为本计划的内嵌设计依据。
**任务清单:** [任务列表](2026-10-22-a-share-upgrade-tasks.md)

## 已确认范围与设计约束

- 第一批：现有缺陷修复、两类规则策略、可追溯报告、评估基础。
- 工作基线：A 股日线，5—20 个交易日研究周期；评估窗口为 5/10/20 个交易日。
- 保留微信与后台入口；期货行为不纳入此次改造。
- 快速分析=规则结果与一次解释；标准分析=分项证据分析与综合；深度分析=标准流程加反方质疑和独立风险核查。规则风险检查适用于全部档位。
- 不承诺收益或胜率；模型自报置信度不等同于统计胜率。
- 模型不能增加候选池外股票、改写原始数值或绕过风险约束；允许零候选。
- 历史数据和旧报告保留；迁移采用新增表/字段，旧记录不能可靠还原时标记不可评估。
- 所有时间使用带时区时间；交易日归属按 Asia/Shanghai 和交易日历。
- 数据源、模型、提示词、策略版本以及数据截止时间随每次运行固化。
- 数据和费用规则缺失时明确标记限制，不生成看似完整的绩效。
- 仅本地测试和报告预览；不自动推送消息、不下单、不购买数据。上线切换在验收后处理。

## 实施顺序与里程碑

| 里程碑 | 任务 | 可审阅交付物 | 放行条件 |
|---|---|---|---|
| M0 基线 | T01 | 缺陷复现、隔离环境与基线记录 | 能区分原有问题与新增回归 |
| M1 可信输入与输出 | T02—T04 | 数据快照、决策存储、真实分档分析 | 缺失/过期数据与缓存用例通过 |
| M2 可解释选股 | T05—T06 | 两类策略、带证据报告 | 评分可重现，事实可核对 |
| M3 验证基础 | T07—T08 | 回测、三组对照、模拟观察入口 | 离线端到端与迁移/回退检查通过 |
| M4 观察结论 | T09 | 至少20个交易日初评 | 披露成熟样本量与统计局限 |

按依赖顺序实施，不把模拟观察等待时间计为代码开发时间。T09 需要后续真实交易日数据，不能在本次开发结束时提前标记完成。

## 共用数据契约

新增 domain/models/research.py，定义：
- EvidenceItem: evidence_id、kind、source、source_url(可空)、observed_at(可空)、published_at(可空)、fetched_at、available_at(可空)、payload、quality_flags。
- AnalysisSnapshot: snapshot_id、stock_code、as_of、market_session、quote、bars、fundamentals、news、fund_flows、evidence、quality_status、missing_fields、content_hash。
- ResearchDecision: decision_id、run_id、stock_code、status、horizon_days、rule_score、trigger_conditions、invalidation_conditions、supporting_evidence_ids、opposing_evidence_ids、risk_flags。
- AnalysisRun: run_id、snapshot_id、created_at、depth、strategy_version、prompt_version、model_id、status、decision、report。
- EvaluationResult: run_id、horizon_days、status(pending/evaluated/unavailable)、metrics、assumptions、exclusion_reason。

输入数据缺失用 None/缺失状态，不用零冒充真实数值。数据可用时间未知，不得纳入声称无前视偏差的历史验证。

## Review Focus

1. 除权、复权口径切换及成交量单位不同：T02 拒绝混算并测试。
2. 午休、周末、休市和盘中未完成日线：T02/T04 依据交易日历和数据时间判断。
3. 模型输出未知股票、无来源事实或错误证据 ID：T04/T06 拦截，不进入正式决策。
4. 涨跌停无法成交、停牌、跳空穿越止损、同日双触价：T07 按明确撮合规则处理。
5. 不完整评估窗口、历史退市股票缺失、公告时点缺失：T07/T08 标为 pending 或披露样本偏差，不补造数据。

## T01 基线、隔离与缺陷复现

**文件：**新增 tests/test_analysis_regressions.py、tests/fixtures/research/；执行时建立 docs/evidence/ 下的基线记录。
**依赖：**无。
**接口：**产出可注入的行情、模型响应、交易日历样本，禁止测试调用真实推送。
- [ ] 检查工作区未提交内容及适用 AGENTS.md；创建隔离工作区，使用 SQLite backup API 备份数据库，不能仅复制活跃数据库主文件。
- [ ] 固定旧版本、依赖版本和脱敏样本；记录旧版可运行范围、现有失败及耗时。
- [ ] 写回归测试：现价90/支撑100不能得满分；同日更新行情不能命中旧快照；标准/深度调用步骤应不同；报告决策可回读。
- [ ] 运行 python -m unittest tests.test_analysis_regressions -v，记录预期失败的具体断言，不能把导入失败当作缺陷复现。
- [ ] 提交基线与测试；后续各任务分别修复对应失败。

## T02 数据契约与质量闸门

**文件：**新增 domain/models/research.py、domain/data_quality.py、application/snapshot_service.py、tests/test_data_quality.py；修改 domain/models/data_source.py、infrastructure/data_source/manager.py、相关适配器。
**接口：**SnapshotService.collect(stock_code: str, as_of: datetime) -> AnalysisSnapshot；validate_snapshot(snapshot: AnalysisSnapshot) -> list[str]。
**依赖：**T01。
- [ ] 先写测试：来源缺时间、陈旧行情、复权混用、单位混用、财务缺字段、节假日最近收盘、未完成日线。
- [ ] 按测试最小实现统一元数据；保留原 FetchResult 消费方兼容性。财务解析使用固定接口样本验证字段和报表方向。
- [ ] 第一版盘中行情最大时效5分钟；收盘模式要求最近已完成交易日；规则信号只使用完成的日线，至少120根。历史不足停止策略评级。
- [ ] 新闻催化窗口默认72小时，保留发布时间和来源；旧公告可作为背景，必须标注日期；未知时间新闻不作为新催化。
- [ ] 行情时间未知/过期或关键日线不可用时只输出数据状态；可选数据缺失允许部分报告，但不能判定相关条件通过。
- [ ] 运行 python -m unittest tests.test_data_quality -v；全部通过后提交。

## T03 结构化持久化与迁移

**文件：**新增 repository/research_repository.py、tests/test_research_repository.py；修改 infrastructure/database.py、repository/report_repository.py。
**接口：**ResearchRepository.save_run(run: AnalysisRun, snapshot: AnalysisSnapshot) -> None；get_run(run_id: str) -> AnalysisRun | None。
**依赖：**T02。
- [ ] 写测试：重复迁移无损、运行原子写入、重复run_id幂等、报告不含DECISION标记仍可回读决策。
- [ ] 新增 research_snapshots、research_runs、research_decisions、research_evaluations 表，以 run_id 连接；旧表和旧报告可读。
- [ ] 同事务保存快照与决策，快照内容不可原地覆盖；版本升级生成新运行。
- [ ] 不从旧报告猜入场价、证据或模型版本；不能还原的旧记录标记 legacy_unverifiable。
- [ ] 运行 python -m unittest tests.test_research_repository -v；临时数据库迁移与旧版读取均通过后提交。

## T04 统一分析、缓存与真实分档

**文件：**修改 application/analysis_service.py、application/analyst_agents.py、application/debate_service.py、domain/decision_parser.py、cli.py；新增 tests/test_analysis_pipeline.py。
**接口：**新增 AnalysisService.analyze_run(stock_code: str, depth: ResearchDepth, force: bool=False) -> AnalysisRun；原文本接口保留为兼容包装。
**依赖：**T02、T03。
- [ ] 写测试：数据闸门阻止模型调用；三档步骤不同；风险规则不可被模型覆盖；强制刷新跳过报告缓存；异常不返回成功。
- [ ] 缓存键包含股票、深度、快照hash、策略/提示词/模型版本；有用户上下文时包含脱敏上下文hash；先验证数据有效性。
- [ ] 收敛 CLI、微信和定时分析至同一入口；删除旧入口无行情时推测近期走势的路径。
- [ ] 快速/标准/深度按设计真实执行；模型分项失败只输出已有证据并标记部分完成。风险核查失败时不输出合格候选状态。
- [ ] 结构化模型输出校验必填项、范围、候选与证据ID；数值冲突采用计算值，不能解析时返回明确状态。
- [ ] 进度由真实阶段完成触发；每次调用使用超时和已有用量记录，保留任务失败原因。
- [ ] 运行 python -m unittest tests.test_analysis_pipeline tests.test_analysis_regressions -v；除T05拥有的评分回归外相关测试通过后提交。

## T05 两类可配置规则策略

**文件：**新增 domain/strategy/swing_rules.py、config/swing_strategies.yaml、tests/test_swing_rules.py；修改 domain/technical_scorer.py、application/recommend_service.py。
**接口：**evaluate_strategy(snapshot: AnalysisSnapshot, strategy_id: str, config: dict) -> ResearchDecision。
**依赖：**T02、T03；接入分析依赖T04。
**初始参数是待验证基线，不代表有效策略：**
- 趋势回踩：MA20>MA60、MA20高于5交易日前；收盘价不低于MA20且距离不超过3%；最近5日平均成交量不高于此前20日平均量的80%。
- 放量突破：收盘价超过此前20日最高价1%—5%；当日量不低于此前20日均量1.5倍；MA20>MA60。所有“此前”窗口排除当日。
- 通用过滤：非ST、未停牌、非上市初期（至少120根完成日线）、近20日平均成交额至少1亿元；状态未知则不进入合格候选。
- 支撑/止损候选与ATR(14)等技术值由代码计算；价位非有效顺序时不给触发价方案。第一版不要求模型预测目标收益。
- [ ] 写边界测试：破位、阈值相等、零量、无穷/NaN、短历史、未来数据、候选为空。
- [ ] 实现策略条件检查与每项证据；初版用固定可解释评分，配置和版本随run保存。
- [ ] 修正破位得高分；资金流展示相对成交额，缺失标为不可用，第一版不强制计入选股总分。
- [ ] 确定性排序平分时按股票代码；LLM只能在合格池内辅助排序，分别保存两份排名。
- [ ] 运行 python -m unittest tests.test_swing_rules tests.test_analysis_regressions -v；全部通过后提交。

## T06 可核对报告与现有界面接入

**文件：**修改 domain/report_renderer.py、domain/decision_parser.py、application/report_export_service.py、admin/api.py、admin/static/index.html；新增 tests/test_evidence_report.py。
**接口：**render_research_report(run: AnalysisRun, snapshot: AnalysisSnapshot) -> str。
**依赖：**T04、T05。
- [ ] 写测试：无效证据ID拒绝、缺失数据明显展示、指标与快照一致、特殊字符安全渲染、报告不展示伪胜率。
- [ ] 固定结构：结论/周期、支持依据、反对依据、触发与失效条件、数据时间及完整度、版本。
- [ ] 事实字段由模板从快照渲染；LLM解释必须引用有效证据，不能核实的内容标为待核实或不展示。
- [ ] 微信短摘要与网页全文共享同一run；后台展示来源、状态和版本，不再把角色数量当作质量指标。
- [ ] 本地预览完整、缺新闻、过期行情、零候选、模型失败五类报告，不发送外部消息。
- [ ] 运行 python -m unittest tests.test_evidence_report -v，并检查网页实际显示后提交。

## T07 修复回测并区分预测与交易收益

**文件：**修改 application/backtest_service.py、domain/backtest_engine.py；新增 domain/execution_rules.py、config/execution_rules.yaml、tests/test_backtest_execution.py。
**接口：**evaluate_run(run: AnalysisRun, bars: list, horizon_days: int, execution_config: dict) -> EvaluationResult。
**依赖：**T03、T05。
- [ ] 写测试：缺报告日价格、未满窗口、停牌、不能成交的涨跌停、跳空、T+1、同日止损止盈、成本、卖出观点不等于做空收益。
- [ ] 入场采用信号后下一可交易日开盘；当日不能成交则该信号标记未成交，不无限追单。样本不足为pending，缺价格为unavailable。
- [ ] 日线触价顺序不可知时采用保守止损优先并标注假设；跳空穿越止损按可成交开盘价，不能按理想止损价。
- [ ] 原始价格撮合、复权因子与公司行动用于持仓收益处理；未支持的公司行动样本标记不可评估。
- [ ] 费用、板块涨跌规则按有效日期配置，实施时核对官方来源；不能直接照搬Qlib示例参数。
- [ ] 方向准确率与模拟持仓收益分开；输出净收益、相对基准、最大回撤、换手、成交/拒单及排除样本数。
- [ ] 运行 python -m unittest tests.test_backtest_execution -v；手算样本与计算结果一致后提交。

## T08 三组对照、端到端验收与回退

**文件：**新增 application/evaluation_service.py、tests/test_research_e2e.py；修改 cli.py、admin/api.py、admin/static/index.html。
**接口：**evaluate_mature_runs(as_of: datetime, horizons: tuple[int,...]=(5,10,20)) -> list[EvaluationResult]。
**依赖：**T06、T07。
- [ ] 写测试：只处理成熟样本、重复执行幂等、交易日计数、各对照组共享时点与成交规则。
- [ ] 对照旧版、纯规则新版、规则+AI新版；旧版不能运行时明确排除，不能拿修正版冒充旧版。
- [ ] 历史规则评估按时间切分；冻结参数后再评估样本外。报告历史股票池/退市样本覆盖，缺少时披露幸存者偏差。
- [ ] AI前向评估仅使用生成当日冻结记录，禁止今天模型回看历史充当无泄漏成绩。
- [ ] 用版本开关选择旧/新分析；新增表兼容旧版本，回退只切入口、不删除新记录。
- [ ] 运行 python -m unittest discover -s tests -v；在隔离数据库完成取数→分析→保存→展示→评估，并复查微信入口和期货兼容性。
- [ ] 输出开发验收记录、已知限制、外部数据/模型成本记录和回退步骤；提交后交用户审核，尚不宣布策略有效。

## T09 至少20个交易日前向观察

**依赖：**T08开发验收完成；真实逐日输入。
- [ ] 固定观察股票池、策略版本、参数、模型和基准，登记观察开始日期。
- [ ] 每交易日收盘后记录三组结果；首次观察默认手动/已有明确授权机制，不擅自新建定时任务或消息推送。
- [ ] 按5/10/20交易日更新成熟样本；第20个交易日并不意味着所有20日窗口均成熟。
- [ ] 记录缺失率、延迟、模型失败率、报告证据错误、费用及策略表现。
- [ ] 完成初评，说明样本相关性和样本量；无证据证明AI排序改善时保留解释功能。
- [ ] 决定继续观察、修策略或评估Qlib；不得把20日初评当作长期收益证明。

## 执行规范与验证

每项功能任务按“写失败测试→运行确认断言失败→最小实现→相关测试通过→审阅diff→独立提交”推进。
仅在新增修改/发现失败时重复相关检查；里程碑做完整集成验证。
离线测试禁用真实网络、真实数据库和推送；线上数据可用性另做只读抽查，结果与离线正确性分开记录。
测试文件名称和接口是本计划拟新增内容，当前尚不存在。
实现前确认环境依赖可用，不因导入失败误报策略测试结果。

## GitHub参考与复用边界

- DSA：https://github.com/ZhuLinsen/daily_stock_analysis —— 报告与证据组织，MIT。
- AlphaSift：https://github.com/ZhuLinsen/alphasift —— 策略配置、候选记录与数据质量，Apache-2.0；其T+N评估并非完整交易回测。
- Qlib：https://github.com/microsoft/qlib —— 离线研究与样本外评估，第一批不强制接入。
- TradingAgents-CN：https://github.com/hsliuping/TradingAgents-CN —— 研究流程；混合许可，按复用目录核对。
- CZSC：https://github.com/waditu/czsc —— 多周期技术信号，后续候选。
实施时固定所参考的commit和许可，记录具体借鉴文件；外部代码不得覆盖当前项目配置或密钥。

## 完成定义

开发完成：T01—T08交付并验证；不表示策略有效。
观察完成：T09达到交易日和成熟样本要求，发布初评及局限。
第一批不包含机器学习选股、全市场历史数据库建设、自动下单或多市场重构。
