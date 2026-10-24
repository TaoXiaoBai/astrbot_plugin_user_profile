<div align="center">
  <img src="./logo.png" width="160" alt="用户画像插件 Logo">
  <h1>AstrBot 用户画像</h1>
  <p>以 LLM 语义理解为分析核心，把聊天记录转化为可读标签、判断依据和风险画像。</p>

  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-6f42c1)](https://github.com/AstrBotDevs/AstrBot)
  [![Version](https://img.shields.io/badge/version-1.17.0-blue)](./metadata.yaml)
  [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
</div>

`astrbot_plugin_user_profile` 是一个 **LLM 驱动的独立 QQ 用户画像插件**。插件持续积累群聊、私聊、社交来源和管理前科等证据，再由 LLM 阅读近期真实发言、结合行为统计理解语义，生成带置信度和理由的结构化标签，最终形成可供人类查看、也可供其它插件直接消费的风险画像。

这里的重点不是简单统计“发了多少条消息”，而是让 LLM 回答更接近实际判断的问题：这个人长期在聊什么、表达方式如何、是否像广告引流或诈骗、是否经常刷屏挑衅、是否友好可靠，以及这些判断分别基于哪些发言和行为信号。

插件不依赖其它插件也能完成采集和画像分析。同时安装守卫 1.21.0+ 后，守卫可经可信内部 API 将 bot 被禁言事件写入画像：真实操作者标为“曾禁言 bot”，可选邀请人只标“关联群禁言警告”。按群禁言累计次数仍只是群背景，不会自动归给个人。查询时实时叠加事件风险分，文字和图片无需等待 LLM 缓存刷新；没有聊天记录也能显示。插件缺失或旧版不支持接口时联动安全降级。

> 本插件不会主动拉黑、踢人、禁言或退群，也不会修改其它插件的数据；但会持久保存可信守卫传入的个人管理事件证据。画像和风险分用于辅助判断，不应替代人工复核。

## LLM 在插件中做什么

LLM 是画像的**语义分析核心**，规则统计是它的**证据底座和故障降级保障**。完整流程如下：

1. **持续积累证据**：插件静默收集近期发言原话、群聊/私聊活跃度、图片、链接、二维码、@、夜间发言、社交来源及管理前科。
2. **规则预处理**：先把客观数据转换为高活跃、多群出现、链接偏多、邀请被拒等基础标签和行为比例。
3. **LLM 阅读与理解**：查询时分别阅读本场景原话（附来源、群号和时间），群聊分支不接收私聊证据。语义分支只使用本场景消息计数，混合图片/链接等行为比例及规则标签仍用于最终规则评分，不传给群聊模型，避免私聊信号污染公开语义。模型结合上下文识别广告引流、诈骗、挑衅、友好等倾向。
4. **输出结构化判断**：LLM 返回 JSON 对象，包含最多 5 个带 `tag`、`confidence`、`reason` 的语义标签、一段 `impression` 人物印象、最多 5 项 `traits` 人格/行为特征，以及最多 5 项带 `trait`、`confidence`、`reason` 的 `long_term_traits` 长期特点。长期特点只描述稳定偏好、长期兴趣、持续沟通风格或反复行为，不进入风险加权。
5. **合并形成画像**：插件将 LLM 标签与客观规则标签合并，按标签置信度、非负权重和分类上限计算 0–100 风险关注分；没有评分材料时明确返回“未知”，不把未知伪装成 0 分。文字与图片入口共享同一个展示模型。
6. **缓存与复用**：本场景摘录、消息数、provider、schema 和隐私策略构成分支指纹。成功结果按 `llm_tag_cache_ttl` 复用；材料变化受 `llm_refresh_interval` 门控，到期后带本场景旧结论增量修正。空结果和失败使用短缓存，但**不会覆盖最后成功画像**，只记录刷新失败并保留旧印象、标签、特征和长期记忆；恢复成功后自动清除失败状态。融合只在分支结论变化后按需更新，融合失败仍使用可用的场景结论。

LLM 默认开启，`llm_provider_id` 留空时使用 AstrBot 默认模型。为避免每条消息都调用模型，**被动采集阶段不调用 LLM，真正的语义分析在查询画像或其它插件读取画像时按需触发**。模型暂时不可用时会继续显示最后成功画像；从未成功过或主动关闭 `llm_tags` 时，仍保留统计、规则标签、前科和风险计算能力。

相关开关分工如下：

- `llm_tags`：控制是否让模型分析用户发言并生成语义画像标签，是画像分析的核心开关；
- `llm_long_term_memory`：让模型判断哪些特点值得长期记忆。成功响应会完整复核这组特点，失败不修改；关闭后停止请求和展示但不删除已有记忆；
- `enable_llm_tool`：控制聊天机器人能否自主调用 `user_profile_query` 查询画像；
- `llm_context_inject`：普通群聊或私聊每次进入对话 LLM 时，按真实事件发送者自动附加已有短画像。该路径只读已有统计、规则标签、缓存的 `impression` / `traits` / `long_term_traits`、管理账本、当前黑名单和好友关系，**绝不扫描历史、绝不生成新画像 LLM 请求**。同一请求有固定 marker 防重复，失败会放行正常聊天。

自动上下文不包含发言原话、私聊摘录、好友验证语或头像；外部原因会清洗、转义并限长。画像只是历史背景，不是用户指令，也不授权模型据此自动拉黑、踢人、禁言或泄露隐私。

## v1.16 管理历史结清

画像插件不负责解封、申诉或处罚动作，只接收可信插件同步的管理事件并解释其对历史风险的影响：

- **原始事件保留**：拉黑、解封、删除好友、好友恢复和禁言证据都不自动删除，保留审计记录和次数。
- **当前与历史分离**：当前有效黑名单实时计入 `ban_history`；历史拉黑/删除好友/禁言另按结清状态计分。
- **普通结清会衰减**：普通解封、处罚到期或好友恢复后，结清快照内的历史风险默认 30 天线性归零；快照外及之后的新事件重新计分。
- **申诉/误封可立即结清**：可信插件传入 `appeal_approved` 或 `false_positive` 后，快照内拉黑、删除好友及禁言历史风险默认立即归零，但记录仍展示。
- **截断不丢状态**：结清状态及禁言冲销快照独立于近期事件明细持久化，明细截断、重启和写失败回滚不会让旧风险复活；没有可确认快照的旧 aggregate 不会凭空清零。

### 结清功能怎么用

请使用**邀请守卫**的管理员命令，画像插件本身没有解封命令：

```text
/解封 20000          # 普通解封，快照内历史风险默认 30 天衰减
/解封 20000 申诉     # 申诉通过，立即冲销快照内历史风险
/解封 20000 误封     # 确认误封，同样立即冲销
/解封 20000 到期     # 处罚到期，按普通解封衰减
```

第二参数也支持“普通/手动/常规”“申诉通过”“误判”“过期”。执行后用 `/画像 20000` 查看结果：当前黑名单由 qq_tools 当前条目决定，历史事件仍保留，但风险构成会按结清结果显示。普通 `/解封` 不会恢复 QQ 好友或协议黑名单。v1.16.2 起，旧版本已经产生的解封/申诉记录会在加载时按当时时间自动补建禁言冲销快照，无需重复执行命令；只有无法确认角色增量的旧汇总分仍会保守保留。申诉/误封只应由管理员在确认后使用，LLM 解封工具只能同步普通解封。

## v1.15 风险评分重构

- **未知与 0 分不同**：无标签且无禁言风险时为 `score=None`、“未知”，不进入风险排名；已有中性或正向材料但没有命中正权重风险时为 0 分。两者都不代表绝对安全。
- **不再互相抵销**：取消 50 分基准和负信任分。友好、活跃、私聊、新人、邀请被拒、频繁邀请、拉 bot 入群等默认只是描述标签，不加分，也不能抵销黑名单、诈骗或禁言等硬风险。
- **分类累加并封顶**：管理证据、LLM 语义、弱行为、直接禁言、关联禁言分别计算；语义默认最多 60，弱行为默认最多 15，直接和关联禁言使用守卫的独立上限，最终总分限制在 0–100。
- **小样本不乱贴标签**：图片、链接、二维码、@、长消息和夜间行为至少要有 10 条实时采集样本，二维码另需至少 2 次；持久化历史只补活跃度，不稀释实时行为比例。
- **分数有证据范围**：单人画像、日常对话和守卫决策会区分群聊公开、私聊或群私融合证据；排名始终只使用群聊公开证据。分数是排查优先级，不是诈骗概率或事实定罪。

## v1.14 动态 bot 群关系

与守卫 1.24.0+ 配合，可信 `record_bot_group_relation(record)` 接收独立动态关系快照，`get_bot_group_relations(platform_id=..., self_id=..., group_id="", inviter="")` 只读对应范围。`up_bot_group_relations` 按 `[platform_id,self_id,group_id]` 键保存 inviter、operator、request_key、join_time、membership、confirmed_at、evidence、event_key、revision、event_created_at_ms；守卫是权威来源，不从旧全局邀请记录补造归因。API 返回 recorded/duplicate/cancelled/disabled/invalid/failure，失败不污染已保存版本。此 API 仅供可信插件，不能直接暴露给聊天用户。

- 日常群聊只附当前平台、当前 bot、当前群的关系。私聊只附当前发送者明确作为 inviter 的关系，不把 operator 当 inviter，最多六条。一天内确认称近期确认，更旧的状态称最近确认/当前待核实；没有证据明确说未知。
- 关系是客观动态事实，不进入画像 LLM 的材料、长期印象或 prior，不影响公开榜单评分。日常仅使用已保存缓存和 KV，不额外生成画像或扫描历史。
- 关系保存与 `llm_context_inject` 分离：关掉注入不会停止接收生命周期。关系数据随画像删除/用户容量淘汰清理，复用持久删除 cutoff 与在途世代保护，旧 pending 被拒绝，新生命周期事件仍可写入。关系条数使用 `max_tracked_users` 上限，达到时拒写并保留守卫 pending，而不是静默丢失去重信息。
- 仅实际注入了匹配关系才携带关系 marker，并附已覆盖群号/版本 claims；marker、完整最小关系事实与安全说明优先占用硬限长预算，长印象不能挤掉它们。守卫只去重已覆盖的相同或更旧版本，缺失/更新的 pending 关系仍保留 fallback。守卫先执行时画像逐条合并，不整段删除；无本地关系、同步失败或未注入时不认领。黑名单与未知格式的旧 fallback 均保留。

## v1.13 群私关联与隐私边界

同 QQ 始终是一个身份主键，不猜测跨账号关联。语义缓存分为 `group`、`private`、`fused`：群聊与私聊分别分析，融合只读取两个分支的简短结构结论，呈现整体印象、共通特征、场景差异与置信信息。只有一侧材料时也可以分析；差异不等于虚伪、恶意，更不自动授权处罚。

- `cross_scene_link_enabled=true`：默认允许关联群私印象。本人或管理员在私聊查看关联视图；他人在私聊查询也只得到群聊公开视图。关闭后只使用当前场景结论；无聊天事件的可信内部 API 退回群聊视图。
- `group_chat_use_linked_impression=true`：按本次用户选择，**日常群聊模型也可内部参考整体概括**。在关联与私聊使用总策略允许时，日常群聊和本人私聊分别携带已有群聊概括、私聊概括与融合印象；任何一槽缺失明确标注信息不足，不额外请求模型或扫描。风险标签的融合不可用时仍退回群聊缓存。不传私聊原话或好友验证语；概括本身可能保留私人事件信息，提示模型不主动透露来源和细节并不能消除该风险。**用户已明确接受概括仍可能透露私聊衍生信息的残余风险；提示约束不是绝对防泄露保证。** 不愿承担此风险请关闭此项（群聊仅参考群聊缓存）。
- 群内公开命令、图片、`user_profile_query` 工具和全部/本群排名**始终只用群聊语义**，即使查询者是管理员或本人，也不会因日常内部参考开关而公开私聊派生 impression / traits / evidence。文字与图片共享相同模型。私聊查询他人同样限制。排名不批量生成 LLM。
- `llm_include_private_quotes=false` 是使用总策略：立即停用旧 private/fused 派生缓存和 prior，不经过最短刷新间隔。旧混合缓存隔离到 `legacy`，不作为 group prior；干净材料在主动查询时重建。关闭这些开关**不主动删除已有原始数据**；`collect_private=false` 停止新增私聊采集，`store_private_quotes=false` 停止新增私聊原话存储，历史路径也不能绕过它们。删除历史原始数据请用画像删除/清理命令。
- 材料保留明确的 `scene`、`group_id`、时间和兼容 `src`；实时及持久化群历史可确认来源。旧无法确定来源的会话材料标为 unknown，不当群聊、不用于语义或公开摘录。群聊、私聊各最多 `quote_keep` 条，正常材料容量最多原来的两倍；旧 unknown 兼容材料暂占非私聊配额，按有限窗口自然替换。`quote_retention_days` 仍生效；旧无结构 history_quotes 不直接补充模型或公开摘录，主动历史扫描重新取得可确认来源、时间的材料后写入同一独立配额窗口。
- 规则与管理统计继续兼容，风险按 tag 去重，不因分支/融合重复加权；禁言累计照旧。可信 `get_decision_profile` 保留旧字段，补充 `scene`、`visibility`、`sources`、`sections`。`event=None` 仅供可信插件 API，不是聊天权限授权，禁止直接将融合结果公开转发。

## 主要功能

- **LLM 语义画像**：综合真实发言、规则标签和行为比例，输出带置信度与依据的广告、诈骗、刷屏、挑衅、友好、乐于助人、正常交流等结构化标签。
- **独立行为采集**：统计群聊/私聊发言数、活跃群、首次与最近发言时间，以及图片、链接、二维码、@、消息长度、夜间活跃等信号。
- **标签融合与风险分**：合并 LLM 语义标签、客观规则标签、社交来源和管理前科，计算 0–100 风险分。
- **历史扫描回填**：优先读取新版 AstrBot“持久化群聊消息记录”（`platform_message_history`），按发送者 QQ 精确回填活跃度、首次/最近发言和近期原话；旧 `conversations` 会话历史继续作为兼容兜底，减少将老群友误判为新人。
- **社交来源记录**：记录好友添加时间、好友验证语及进群方式、群号、操作者和时间；协议未提供真实来源时明确标注为“推测来源群”。
- **灵活查询权限**：支持仅管理员、允许查自己、全员查他人、指定群公开查询等组合。
- **管理员风险排名**：`/画像排名`（别名 `/风险排名`）分页展示全部或本群已记录关联用户的只读风险快照；排名与单人画像共享规则和评分函数，但绝不逐人调用 LLM、扫描历史或请求昵称/头像。
- **统一文字或图片输出**：所有 `/我`、`/画像` 和管理员榜单入口均兼容 `image_output`；图片成功时只发送图片，渲染或发送失败时回退一条文字，并清理 UUID 临时文件。个人卡片模块仍由 `card_show_*` 独立控制。
- **邀请守卫深度联动**：画像插件读取守卫摘要，并接受守卫可信 API 写入个人禁言归因及成功拉黑/解封/删除好友事件；邀请守卫读取画像标签和风险分。写入按事件幂等、原子持久化，失败可重试或安全降级。通用管理账本要求画像 1.11.0+ 与守卫 1.22.0+。
- **低开销采集**：消息统计热路径零 LLM、零同步网络；好友列表只在后台按数小时节流对账，内存统计定期写入 AstrBot KV。日常画像注入不生成额外模型请求。

## 兼容性

- AstrBot `>=4.16`
- 平台适配器：`aiocqhttp`（OneBot V11）
- 图片输出依赖 Pillow；AstrBot 4.x 官方依赖已包含 Pillow
- 昵称、头像、好友及进群事件等能力取决于 OneBot 实现。SnowLuma、NapCat 等上报的 `friend_add` 通常不包含“从哪里加的好友”字段

## 安装

### 插件市场

在 AstrBot WebUI 的插件市场搜索“用户画像”，安装后重启或重载插件。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/TaoXiaoBai/astrbot_plugin_user_profile.git
```

然后重启 AstrBot，或在 WebUI 的插件管理中重载插件。

## 快速开始

插件默认配置采用隐私优先策略：

- 自动采集群聊和私聊行为；
- 普通用户可以查询自己的画像；
- 普通用户不能查询他人；
- AstrBot 管理员始终可以查询任意用户；
- LLM 语义标签默认开启，使用 AstrBot 默认模型；
- 图片输出默认关闭，发言摘录默认显示。

常用命令：

| 命令 | 权限 | 说明 |
| --- | --- | --- |
| `/画像 <QQ号>` | 按配置判断 | 查询指定 QQ 的画像 |
| `/画像 自己`、`/画像 我` | 按自查询配置判断 | 使用标准命令查询自己 |
| `/我`、`/我的画像`、`/查自己` | 按自查询配置判断 | 自查询快捷命令 |
| `/画像排名 [本群|全部] [页码]`、`/风险排名 ...` | 管理员 | 已记录用户风险快照；群聊默认本群，私聊默认全部 |
| `/画像扫描 <QQ号>` | 管理员 | 手动扫描指定 QQ 的 AstrBot 会话历史 |
| `/画像扫描 本群` | 管理员 | 分批扫描当前群的未完成人员 |
| `/画像扫描 全部` | 管理员 | 分批扫描所有已知未完成人员 |
| `/画像删除 自己` | 本人 | 删除自己的统计、摘录、禁言事件证据和 LLM 缓存；联动取消守卫待投递画像 |
| `/画像删除 <QQ号>` | 管理员 | 删除指定用户的全部画像数据 |
| `/画像清理` | 管理员 | 立即清理超过保留期的摘录 |

批量扫描受 `history_scan_batch_limit` 和 `history_scan_concurrency` 限制，处理中会按阶段报告调用成功/失败及分页完成/待续扫人数，最终另列批次外剩余人数。单次页数不足时保存下一页水位，再次执行会优先继续未完成人员；指定 QQ 手动扫描从第一页强制重扫。已完成扫描按 `history_rescan_interval` 增量复查。

排名只从统计、已有语义缓存、个人禁言账本、管理账本和当前黑名单收集候选，关联数据批量读取一次。`本群` 依据插件已经记录的群关联过滤，不声称是实时群成员榜；无标签且无禁言风险的用户为“未知”并排除，已有材料的 0 分用户可按最低分配置入榜。榜单展示语义缓存时间或“尚未分析”，不会为了排名刷新分析；材料不足、未刷新或尚未分析均不等于低风险。排名只供管理员排查，不构成事实定罪。

命令兜底只匹配完整边界，`/我是...`、`/画像测试...` 等普通文本不会误触发。

## 画像包含什么

有数据的项目才会显示；全部为空时返回“暂无记录”。

- QQ 号、可获取时的昵称，以及仅在查询阶段短时获取的 QQ 头像；头像失败时使用占位图，不持久保存；
- 群聊/私聊发言数、活跃群数、首次与最近发言时间；
- 人物印象、人格/行为分析、行为统计和可选的最近发言摘录；
- 带“风险 / 正向 / 行为”文字分类的中文椭圆药丸标签，风险用柔和暖色、正向用绿色、行为用蓝紫色，颜色只作辅助；
- LLM 对各项语义判断给出的置信度；
- 供 LLM 工具和可信插件读取的标签依据 `evidence`；
- 0–100 风险关注分及低、中、高、极高等级，或无材料时的“未知”；同时展示管理证据、语义、弱行为、直接/关联禁言构成和证据范围；
- 好友添加、验证语、进群来源及推测来源群；
- 邀请、拒绝、拉 bot 入群和黑名单等个人联动记录，以及明确标注的群级 bot 禁言背景。

风险分用于辅助判断，不应作为自动处罚的唯一依据。未知不等于 0 分，0 分也不等于安全；LLM 标签可能误判，重要决策请结合原始记录人工复核。

## 查询权限

权限按以下顺序判断：

1. 插件关闭时，拒绝全部聊天入口和 LLM 查询工具；
2. AstrBot 管理员始终可以查询自己或他人；
3. 查询自己由 `allow_self_query` 控制；
4. 当前群位于 `group_public_query_groups` 时，可以查询他人；
5. `allow_other_query=true` 时，可以查询他人；
6. 其它情况拒绝，并在日志中记录入口、发送者、目标、群号和命中规则。

普通用户权限矩阵：

| 配置场景 | 查询自己 | 查询他人 |
| --- | --- | --- |
| 默认配置 | 允许 | 拒绝 |
| `allow_self_query=false` | 拒绝 | 由其它规则决定 |
| `allow_other_query=true` | 由 `allow_self_query` 决定 | 允许 |
| 位于指定公开群 | 由 `allow_self_query` 决定 | 允许 |
| AstrBot 管理员 | 允许 | 允许 |

`enable_self_shortcuts` 只控制 `/我`、`/我的画像`、`/查自己`，不是自查询权限开关。关闭快捷命令后，只要 `allow_self_query=true`，仍可以使用 `/画像 自己`。

常见配置方式：

仅管理员可查：

```json
{"allow_self_query": false, "allow_other_query": false, "group_public_query_groups": ""}
```

管理员和用户本人可查（默认）：

```json
{"allow_self_query": true, "allow_other_query": false, "group_public_query_groups": ""}
```

所有人均可查询自己和他人：

```json
{"allow_self_query": true, "allow_other_query": true}
```

本人可查自己，仅指定群可查询他人：

```json
{"allow_self_query": true, "allow_other_query": false, "group_public_query_groups": "123456,789012"}
```

## 配置说明

配置项已按用途分组显示在 AstrBot WebUI。以下默认值对应全新安装；旧版本配置会自动兼容迁移。

### 基础与权限

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `enable` | `true` | 插件总开关；关闭后停止采集并拒绝查询，已有数据保留 |
| `allow_self_query` | `true` | 允许非管理员查询自己 |
| `allow_other_query` | `false` | 允许非管理员查询他人 |
| `group_public_query_groups` | `""` | 逗号、中文逗号或空白分隔的公开查询群号 |
| `enable_self_shortcuts` | `true` | 启用 `/我`、`/我的画像`、`/查自己` |
| `enable_llm_tool` | `true` | 启用 `user_profile_query`，并应用同一套聊天权限 |

### 管理员风险排名

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `profile_ranking_enabled` | `true` | 启用仅管理员可用的 `/画像排名` 与 `/风险排名`；不继承公开自查权限 |
| `profile_ranking_page_size` | `10` | 每页人数，运行时限制 1–50 |
| `profile_ranking_min_score` | `0` | 最低展示分，运行时限制 0–100；仍要求用户有实际材料 |

### 日常对话画像上下文

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_context_inject` | `true` | 对真实当前发言者自动注入已有短画像；独立于查询工具 |
| `llm_context_max_chars` | `2000` | 整个注入块硬上限，运行时限制 500–8000 字符 |
| `llm_context_include_management` | `true` | 包含禁言 bot、拉黑/解封、bot 主动删除好友等摘要 |
| `llm_context_include_social` | `true` | 包含脱敏好友关系，不含验证语 |

该注入只提供背景，不授予处罚权限；空画像不注入。仅有管理事件或好友关系时仍会注入，以覆盖没有聊天记录但有可验证管理历史的用户。

### 输出与隐私

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `image_output` | `false` | 在线程中渲染 UUID 临时图片；发送后无论成功失败都删除，失败回退文字 |
| `show_quotes` | `true` | 是否在聊天画像中展示最近发言原话；群聊查询不会展示私聊摘录 |
| `quote_show` | `5` | 最多展示的原话条数 |

`show_quotes=false` 只隐藏输出。是否保存、是否允许 LLM 使用私聊摘录分别由下方隐私开关控制。

### 图片卡片模块

以下开关只影响图片卡片的展示模块，不影响文字画像、标签生成和 LLM 分析：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `card_show_tags` | `true` | 卡片展示“画像标签”药丸区 |
| `card_show_stats` | `true` | 卡片展示“关键统计”四个统计格 |
| `card_show_impression` | `true` | 卡片展示 LLM“人物印象”区 |
| `card_show_traits` | `true` | 卡片展示“人格 / 行为分析”列表 |
| `card_show_social` | `true` | 卡片展示“社交来源”区 |
| `card_show_criminal` | `true` | 卡片展示“前科记录”区 |

全部关闭时卡片只保留头部（头像、昵称、QQ、风险徽章）和页脚；摘录区仍由 `show_quotes` 控制。

### 数据采集

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `passive_collect` | `true` | 静默采集发言统计和行为信号 |
| `collect_private` | `true` | 是否采集私聊统计 |
| `store_quotes` | `true` | 是否保存新的发言摘录 |
| `store_private_quotes` | `true` | 是否保存新的私聊摘录；可独立于私聊统计关闭 |
| `collect_groups` | `""` | 仅采集指定群；留空表示全部群，不影响查询权限 |
| `quote_keep` | `10` | 每个 QQ 群聊、私聊各最多保留的原话条数，正常容量最多两倍 |
| `quote_retention_days` | `0` | 按天清理实时摘录；0 表示不按时间过期 |
| `max_tracked_users` | `5000` | 超限后成批清理最不活跃用户及其统计、摘录、标签缓存 |
| `flush_interval` | `60` | KV 落盘间隔，单位秒，最小按 10 处理 |
| `history_fallback` | `true` | 没有已采集原话时尝试读取 AstrBot 会话历史 |

### 历史扫描

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `history_scan_enabled` | `true` | 启用按需历史扫描；关闭后历史未知时不打新人标签 |
| `history_scan_pages` | `3` | 旧 `conversations` 会话历史单次最多读取页数，未完成时保存下一页水位 |
| `history_scan_page_size` | `10` | 每页旧会话数 |
| `platform_history_scan_limit` | `700` | 新版持久化群聊历史每个已知群最多读取的最近消息数，建议与 AstrBot 保存上限一致 |
| `history_scan_cooldown` | `3600` | 未完成扫描自动续跑的冷却秒数 |
| `history_rescan_interval` | `86400` | 扫描完成后从第一页检查新会话的间隔秒数 |
| `history_scan_concurrency` | `4` | 批量扫描最大并发数 |
| `history_scan_batch_limit` | `200` | `/画像扫描 本群/全部` 每批处理人数 |

历史扫描优先读取新版 AstrBot 的 **持久化群聊消息记录**，使用 `sender_id` 精确匹配 QQ；插件会记住用户出现过的群历史作用域，聊天查询时也会补入当前群并映射已知群。旧 `conversations` 会话历史仍作为兼容兜底。两套来源的消息量不会直接相加，而与插件自有计数取较大值，避免重复统计；中途失败不会标记完成。升级前的 `history_version` 会在首次查询时自动升至 v3 并重扫。Bot 从未保存过的 OneBot 服务端历史仍无法补齐。

### LLM 画像分析

这组配置决定画像的语义分析能力。开启后，模型收到的是“近期真实发言 + 规则标签 + 行为比例”，返回机器可读的结构化判断，例如：

```json
{
  "tags": [
    {
      "tag": "ad_suspect",
      "confidence": 0.82,
      "reason": "多次发送二维码、联系方式和引流文案"
    }
  ],
  "impression": "表达直接，近期内容有较明显的推广导向。",
  "traits": ["高频推广", "重复表达"],
  "long_term_traits": [
    {
      "trait": "持续关注同类软件",
      "confidence": 0.76,
      "reason": "跨多次对话反复讨论同一主题"
    }
  ]
}
```

模型只能返回固定白名单风险/语义标签；`reason` 会清除控制字符并截断到 160 字，`impression` 截断到 240 字，`traits` 最多 5 项且每项截断到 48 字，重复项会去除。`long_term_traits` 同样最多 5 项，每项包含特点、置信度和理由，并记录首次出现与最近确认时间。发言摘录继续放在明确的 `<untrusted_evidence>` 不可信证据边界内，不能作为提示词指令。旧 tags-only 缓存因 schema 版本变化会在首次查询时自动刷新。

**增量修正而非故障清空**：重算时上一次的分析结论和长期特点会作为先验一并传入模型，并要求新材料不能明确反驳就保留。长期特点由模型返回完整复核集合：仍成立的保留，不再成立或不再值得记忆的移除；响应未提供该字段时兼容保留旧记忆，显式空数组才清空。临时情绪、一次事件、敏感信息和管理风险不应进入长期特点。模型异常、超时或空响应只记录刷新失败，继续展示最后成功画像，不会把既有印象和长期记忆替换成空白。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 画像语义分析总开关 |
| `llm_long_term_memory` | `true` | 是否让 LLM 判断并维护长期特点；关闭只停止请求和展示，不删除已有记忆 |
| `llm_provider_id` | `""` | 执行画像分析的 provider；留空使用 AstrBot 默认模型 |
| `llm_tag_cache_ttl` | `86400` | 指纹未变时成功结果缓存秒数 |
| `llm_refresh_interval` | `3600` | 材料变化后距上次成功分析不足该秒数仍沿用旧结论；0 表示材料一变立即重算 |
| `llm_failure_cache_ttl` | `300` | 空结果、超时或失败的短期缓存秒数 |
| `llm_timeout_seconds` | `45` | 单次模型调用超时 |
| `llm_max_concurrency` | `3` | 不同 QQ 的模型调用并发上限；同 QQ 同材料 singleflight 合并 |
| `llm_material_max_chars` | `6000` | 发送给模型的摘录材料总字符上限 |
| `llm_include_private_quotes` | `true` | 是否允许模型使用私聊摘录；为兼容升级默认开启 |

### 规则标签阈值

| 配置项 | 默认值 | 触发条件 |
| --- | --- | --- |
| `tag_active_high_threshold` | `100` | 高活跃累计发言数 |
| `tag_active_med_threshold` | `20` | 中活跃累计发言数 |
| `tag_newcomer_days` | `7` | 新人首次出现天数 |
| `tag_multi_group_threshold` | `3` | 多群出现的群数 |
| `tag_image_threshold` | `0.5` | 图片消息占比 |
| `tag_link_threshold` | `0.3` | 链接消息占比 |
| `tag_mention_threshold` | `0.3` | @ 消息占比 |
| `tag_verbose_threshold` | `80` | 平均消息字符数 |
| `tag_night_threshold` | `0.3` | 0:00–5:59 夜间消息占比 |
| `tag_behavior_min_samples` | `10` | 图片、链接、二维码、@、平均长度和夜间标签所需的最少实时采集消息数 |
| `tag_qr_min_count` | `2` | 达到最小样本数后，二维码标签还需命中的最少次数 |

### 风险评分与联动

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `risk_weights` | `""` | 标签权重 JSON；仅接受内置标签的有限数值，负数按 0，未知或非法项忽略并保留对应内置权重 |
| `risk_semantic_cap` | `60` | LLM 语义标签合计贡献上限，范围 0–100 |
| `risk_behavior_cap` | `15` | 弱行为标签合计贡献上限，范围 0–100 |
| `management_resolution_decay_days` | `30` | 普通解封、处罚到期或好友恢复后，结清快照内的历史拉黑/删好友/禁言风险线性归零的天数；0 表示立即归零 |
| `management_appeal_clear_history` | `true` | 申诉通过/误封立即冲销快照内拉黑、bot 删除好友及禁言历史风险；原始事件保留 |
| `risk_level_low` | `30` | 低到中风险分界 |
| `risk_level_high` | `60` | 中到高风险分界 |
| `risk_level_extreme` | `80` | 高到极高风险分界 |
| `link_invite_guard` | `true` | 只读读取守卫邀请/入群摘要及群级禁言背景 |
| `mute_event_keep` | `100` | 每 QQ 展示/保留的近期禁言明细 1–500 条；不影响累计风险与去重账本 |
| `link_qq_tools_ban` | `true` | 只读读取 qq_tools 的 `ban_list` |
| `silent_for_banned` | `true` | 黑名单用户调用画像命令时静默不回复（数据照常采集）；依赖 `link_qq_tools_ban` |

风险分由管理证据、LLM 语义、弱行为、直接禁言和关联禁言五类构成。相同 `tag` 只取最高置信度，单项贡献为“权重 × 置信度”；描述性、友好或正常标签默认权重为 0，不能抵销其他风险。无标签且无禁言风险时返回“未知”，已有标签但未命中正权重风险时可返回 0 分。

管理历史是只读审计账本：当前黑名单由 qq_tools 的当前有效条目决定；历史拉黑、bot 删除好友和禁言则根据可信插件同步的结清状态计算。解封事件会保存当时已知禁言事件及角色增量的冲销快照，普通结清按 `management_resolution_decay_days` 衰减，申诉通过或误封默认立即归零；快照外及之后的新事件仍正常计分。已结清标签保留为历史记录，不作为当前风险。

守卫的每次风险增量与归因随证据持久化。直接操作者与关联邀请人分账累计，分别应用守卫配置的增量和上限；单一 KV 账户同时保存近期展示明细、角色累计风险/归因次数及独立 seen 账本，明细截断不会降低风险。综合风险严格限制在 0–100；修改累计上限在下一次查询时生效。可靠事件 ID/协议时间用于跨重启去重，缺失时仅短窗口尽力去重。seen 账本最多 5000 键/人，达到上限后拒绝新增事件并保持待投递（不会丢弃旧键而重复计分），需人工删除/清理后恢复；`/画像删除` 与用户淘汰会清空该账户。

旧禁言账本兼容读取；迁移会新增 v2 角色汇总字段，并为旧解封记录补建可验证的禁言冲销快照。无法精确拆分的旧混合累计作为 legacy 分量保守保留，不删除原始证据，也不重复计算关联分。

自定义权重示例：

```json
{"ban_history": 40, "scam_suspect": 45, "link_spammer": 5}
```

### 社交来源

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `collect_social_events` | `true` | 记录好友添加、好友申请验证语和进群事件 |
| `show_social_origin` | `true` | 在画像中展示社交来源 |
| `guess_source_group` | `true` | 缺少真实来源时，以发言最多的群作为明确标注的推测来源 |
| `friend_reconcile_enabled` | `true` | 异步、节流、同账号 singleflight 对账 OneBot 好友列表 |
| `friend_reconcile_interval` | `21600` | 同账号最短对账间隔秒数，默认 6 小时 |

首次好友列表只建立持久基线，不产生删除事件。后续只有上次确认存在的 QQ 消失才处理：最近一条关系事件若是守卫成功调用 `delete_friend` 形成的 `bot_deleted_friend`，才归因为 bot 主动删除；用户恢复后再次消失会记录 `friend_relation_missing`（原因未知），不会被历史删除记录永久归因。重新出现记录 `friend_relation_restored`。API 失败不覆盖基线、不制造事件，并以 5 分钟起步指数退避至配置间隔，避免不支持接口时每条消息请求。画像删除的世代保护会丢弃旧的在途对账结果，防止重建已删除画像。

OneBot V11 的 `friend_add` / `friend` / `group_increase` 事件字段由协议实现决定：

- `friend_add` 通常只有 `user_id`，只能确认添加时间；
- `friend_request` 可以记录验证语；
- `group_increase` 可以记录群号、`invite` / `approve`、操作者和时间；
- “推测来源群”仅供参考，不代表真实添加来源。

## LLM 工具

插件使用 AstrBot 公共的 `@filter.llm_tool` 注册 `user_profile_query(qq)`，允许聊天 LLM 按需查询画像。`Args` docstring 会生成 `qq: string` 属性，Python 方法签名将 `qq` 设为无默认值的必填参数。当前 AstrBot 4.x 公共装饰器不会在 JSON Schema 中生成 `required` 数组；插件不直接修改内部工具 registry，以避免不同版本间的私有实现兼容风险。

- `enable_llm_tool=false` 时禁用；
- 必须从工具上下文取得真实 `AstrMessageEvent` 和发送者身份；
- 与聊天命令共用权限规则，不能绕过自查询、他人查询或公开群限制；
- 没有真实事件上下文时默认拒绝。

## 可信插件内部 API

插件间 Python API 用于后台决策，不是聊天用户入口，因此不受聊天查询权限约束。调用方必须是可信插件，不应将它们包装为无权限校验的公开命令。

```python
md = self.context.get_registered_star("astrbot_plugin_user_profile")
instance = getattr(md, "star_cls", None)

if instance is not None:
    decision_profile = await instance.get_decision_profile(
        inviter_qq,
        event,
        exclude_request_key=current_request_key,
    )
    profile = await instance.get_profile_tags_with_score(inviter_qq, event)
    score = await instance.get_risk_score(inviter_qq, event)
    tags = await instance.get_profile_tags(inviter_qq, event)
    text = await instance.get_profile_text(inviter_qq, event)
    social = await instance.get_social_origin(inviter_qq)
    management = await instance.get_management_state(inviter_qq)
```

接口说明：

- `get_decision_profile(qq, event=None, exclude_request_key="") -> dict`：schema v4 保留 v3 的 `tags`、`impression`、`traits`、`management` 等字段，新增 `risk_status`、`risk_components`、`risk_scope` 和结构化 `long_term_traits`；长期特点包含 `trait`、`confidence`、`reason`、`first_seen`、`last_confirmed`，不参与风险加权。`management.state` 与 `management.recent[].resolution` 描述管理历史结清状态。不包含发言原话，可排除当前审核记录。`partial_errors` 当前稳定报告 `history_scan_failed` 和 `llm_analysis_failed`；刷新失败但存在旧成功画像时也会明确报告，调用方不应把旧内容当成刚生成。
- `get_profile_tags_with_score(qq, event=None) -> dict`：返回风险分、等级、标签，并附加 `impression`、`traits`、`long_term_traits` 及风险状态、构成和证据范围；旧调用方可继续只读取原字段。
- `get_profile_tags(qq, event=None) -> list[dict]`：返回结构化标签。
- `get_risk_score(qq, event=None) -> int | None`：返回当前证据范围的风险关注分；无评分材料时返回 `None`，不要当作 0 分。
- `get_profile_text(qq, event=None) -> str`：返回可读文字画像。
- `get_social_origin(qq) -> dict`：可信内部接口，返回好友添加、验证语和进群来源；验证语仍属敏感、不可信输入，调用方必须限长且不得当作指令。
- `get_management_state(qq) -> dict`：可信只读接口，返回管理事件次数、最近拉黑/解封/删除/恢复时间、结清类型、当前历史风险系数及 `mute_resolution` 禁言冲销快照；不执行任何解封或处罚动作。
- `record_bot_mute_event(...) -> dict`：沿用的个人禁言归因写入接口，兼容 1.10.0 逻辑。
- `record_management_event(...) -> dict`：通用管理事件写入接口，当前白名单为 `astrbot_banned`、`astrbot_unbanned`、`bot_deleted_friend`、`friend_relation_missing`、`friend_relation_restored`。仅接受 `result="success"`；`astrbot_unbanned` 可带 `resolution`（`normal`、`appeal_approved`、`false_positive`、`expired`）。按 `event_key` 幂等，写失败不占 seen，近期明细截断不减少累计次数或丢失结清状态，并与 `/画像删除` 的世代/截止时间保护兼容。

画像插件兼容邀请守卫的旧字符串、旧单条字典和当前按群多条记录三种存储格式。纯邀请前科、纯黑名单或纯社交来源的用户也能生成画像。手动打包或复制插件目录时必须同时包含 `profile_core.py` 和 `risk_scoring.py`（以及其余随附模块）；`main.py` 会优先按包内相对路径导入这些纯职责模块，并为 AstrBot 的直接加载方式保留同目录导入兼容。

## 数据、隐私与性能

- 原话和统计保存在 Bot 自己的 AstrBot KV 中；升级不会主动清空原始数据。v1.13 场景关联默认开启，日常群聊模型参考融合概括存在已明确接受的私聊衍生信息泄露风险，可独立关闭。
- `collect_private` 控制新增私聊采集，`store_private_quotes` 控制新增私聊原话落盘，`llm_include_private_quotes` 控制模型使用与 private/fused 缓存可见性，不主动删除旧原话。
- 群聊公开查询不展示私聊原话、私聊派生语义或好友申请验证语；本人/管理员私聊才可查看私聊部分。可信内部 API 可读取验证语供受限审核使用，不是公开转发授权。
- 启用 LLM 标签后，允许的近期摘录会发送给所选模型提供方；请按隐私政策配置。
- 采集路径零 LLM、零网络；统计内存聚合后按 `flush_interval` 写入 KV。图片查询阶段会在线程中以 2.5 秒超时读取受限 QQ 官方域名头像，最多 2 MiB，不落盘；失败直接使用占位头像。
- 每个 QQ 的群聊、私聊分别最多保留 `quote_keep` 条（正常总容量最多两倍），并可按 `quote_retention_days` 清理；未知旧材料占非私聊配额但不作为群聊证据。消息热路径只检查当前 QQ，启动和管理员 `/画像清理` 才全量检查。
- LLM 成功结果使用常规缓存，空结果和失败使用短缓存；刷新失败只附加 `refresh_status`，不覆盖最后成功内容。长期特点保存在同一场景缓存中，公开群聊只读取群聊分支，私聊派生记忆不会进入公开画像；`llm_long_term_memory=false` 不主动删除旧数据。相同 QQ 和材料合并调用，不同 QQ 受有限 semaphore 控制，不同材料乱序完成时只有最新请求可更新缓存。
- `/画像删除` 通过每 QQ 世代号使旧消息、历史扫描和 LLM 写回失效；先设置持久删除时间界限并调用可信守卫 API 取消该 QQ 的 pending 画像投递，等待在途调用收敛后再次清除本地证据。删除界限 KV 读取失败时禁言事件写入严格 fail closed 并可重试加载，普通画像查询仍可降级。守卫缺失时安全跳过；旧事件时间戳不能在删除后复活，新事件仍可建立画像。删除不取消守卫的即时拉黑任务。
- 容量淘汰同步删除统计、摘录、标签缓存和禁言证据，并取消守卫 pending 画像投递；画像仍以 QQ 号为身份维度。

## 常见问题

### 开了图片输出，为什么仍然收到文字？

图片渲染失败时插件会自动回退文字。请检查 AstrBot 日志中的 Pillow、字体、文件权限或图片发送错误。图片模式成功时不会再额外发送同一份文字画像。

### 关闭发言摘录后，为什么 LLM 仍能生成标签？

`show_quotes` 只控制最终展示。LLM 标签仍会读取已保存原话；要完全停止语义分析，请关闭 `llm_tags`。

### 为什么老群友被显示为“历史状态未知”？

插件只能读取 AstrBot 已保存的会话历史。管理员可以执行 `/画像扫描 <QQ号|本群|全部>` 预热；Bot 从未保存过的历史无法补齐。

### 为什么不能准确显示“从哪个群加的好友”？

这是 OneBot 事件字段限制。SnowLuma、NapCat 等实现通常不会在 `friend_add` 中提供来源群，插件不会把推测结果伪装成事实。

### 为什么有人能查、有人不能查？

依次检查 `enable`、管理员身份、`allow_self_query`、`allow_other_query`、当前群是否在 `group_public_query_groups`，以及调用是否带有真实事件上下文。拒绝原因和命中规则会写入日志。

## 从旧版本升级

插件兼容旧扁平配置和 AstrBot 展平后的分组配置。新键存在时优先使用，旧键只在对应新键缺失时迁移：

- `public_query` → `allow_other_query`
- `enable_self_command` → `enable_self_shortcuts`
- `self_query_only=true` → `allow_other_query=false`

升级后的安全默认值为：

```text
allow_self_query=true
allow_other_query=false
enable_self_shortcuts=true
enable_llm_tool=true
```

建议升级后在 WebUI 中检查并保存一次配置，同时删除手工配置中的废弃键 `public_query`、`self_query_only`、`enable_self_command`。旧自定义负权重会按 0 处理；旧禁言账本兼容读取，迁移只新增角色汇总和可验证的结清快照，不删除原始证据。

## 开源许可

本项目基于 [MIT License](./LICENSE) 开源。
