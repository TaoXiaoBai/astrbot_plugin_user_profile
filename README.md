<div align="center">
  <img src="./logo.png" width="160" alt="用户画像插件 Logo">
  <h1>AstrBot 用户画像</h1>
  <p>以 LLM 语义理解为分析核心，把聊天记录转化为可读标签、判断依据和风险画像。</p>

  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-6f42c1)](https://github.com/AstrBotDevs/AstrBot)
  [![Version](https://img.shields.io/badge/version-1.9.3-blue)](./metadata.yaml)
  [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
</div>

`astrbot_plugin_user_profile` 是一个 **LLM 驱动的独立 QQ 用户画像插件**。插件持续积累群聊、私聊、社交来源和管理前科等证据，再由 LLM 阅读近期真实发言、结合行为统计理解语义，生成带置信度和理由的结构化标签，最终形成可供人类查看、也可供其它插件直接消费的风险画像。

这里的重点不是简单统计“发了多少条消息”，而是让 LLM 回答更接近实际判断的问题：这个人长期在聊什么、表达方式如何、是否像广告引流或诈骗、是否经常刷屏挑衅、是否友好可靠，以及这些判断分别基于哪些发言和行为信号。

插件不依赖其它插件也能完成采集和画像分析。如果同时安装加群邀请守卫，画像插件会读取有限的邀请、拒绝和入群摘要；群级 bot 禁言次数只作为群背景，不归因为个人前科。邀请守卫则读取不含原话的结构化画像。任何联动插件缺失或异常时，基础画像功能仍可独立运行。

> 本插件只做记录、分析和查询，不会主动拉黑、踢人、禁言、退群，也不会修改其它插件的数据。画像和风险分用于辅助判断，不应替代人工复核。

## LLM 在插件中做什么

LLM 是画像的**语义分析核心**，规则统计是它的**证据底座和故障降级保障**。完整流程如下：

1. **持续积累证据**：插件静默收集近期发言原话、群聊/私聊活跃度、图片、链接、二维码、@、夜间发言、社交来源及管理前科。
2. **规则预处理**：先把客观数据转换为高活跃、多群出现、链接偏多、邀请被拒等基础标签和行为比例。
3. **LLM 阅读与理解**：查询画像时，LLM 同时阅读近期原话、基础标签和行为比例，不只按关键词匹配，而是结合上下文识别广告引流、诈骗索要信息、刷屏、重复内容、挑衅、友好、乐于助人、正常交流等倾向。
4. **输出结构化判断**：LLM 返回 JSON 对象，包含最多 5 个带 `tag`、`confidence`、`reason` 的语义标签、一段 `impression` 人物印象和最多 5 项 `traits` 人格/行为特征。
5. **合并形成画像**：插件将 LLM 标签与客观规则标签合并，再按可配置权重计算 0–100 风险分和风险等级；文字与图片入口共享同一个展示模型。
6. **缓存与复用**：摘录、行为统计、基础标签、provider 和 schema 共同构成指纹；任一实际 prompt 输入变化都会失效。1.9.0 会在首次查询时刷新旧 tags-only 缓存，成功、空结果和短期失败均缓存，同一材料并发请求自动合并。

LLM 默认开启，`llm_provider_id` 留空时使用 AstrBot 默认模型。为避免每条消息都调用模型，**被动采集阶段不调用 LLM，真正的语义分析在查询画像或其它插件读取画像时按需触发**。如果模型不可用、没有可分析原话或主动关闭 `llm_tags`，插件会保留统计、规则标签、前科和风险计算能力，但画像的语义理解会明显减弱。

`llm_tags` 和 `enable_llm_tool` 是两个不同开关：

- `llm_tags`：控制是否让模型分析用户发言并生成语义画像标签，是画像分析的核心开关；
- `enable_llm_tool`：控制聊天机器人能否自主调用 `user_profile_query` 查询已经生成的画像，不控制画像本身是否使用 LLM 分析。

## 主要功能

- **LLM 语义画像**：综合真实发言、规则标签和行为比例，输出带置信度与依据的广告、诈骗、刷屏、挑衅、友好、乐于助人、正常交流等结构化标签。
- **独立行为采集**：统计群聊/私聊发言数、活跃群、首次与最近发言时间，以及图片、链接、二维码、@、消息长度、夜间活跃等信号。
- **标签融合与风险分**：合并 LLM 语义标签、客观规则标签、社交来源和管理前科，计算 0–100 风险分。
- **历史扫描回填**：按需扫描 AstrBot 已保存的会话历史，补充 LLM 分析材料，并减少将老群友误判为新人的情况。
- **社交来源记录**：记录好友添加时间、好友验证语及进群方式、群号、操作者和时间；协议未提供真实来源时明确标注为“推测来源群”。
- **灵活查询权限**：支持仅管理员、允许查自己、全员查他人、指定群公开查询等组合。
- **统一文字或图片输出**：所有 `/我`、`/画像` 入口共用展示模型；动态高度卡片包含白环圆形头像或可靠占位头像、昵称/QQ、按风险分着色的头部描边条与右侧风险徽章、按“风险 / 正向 / 行为”区分的柔和色椭圆药丸标签、人物印象、人格/行为分析、横向关键统计、社交来源/前科及可选摘录，页脚标注生成时间。长内容自动换行，图片成功时不再双发文字；每个展示模块都有独立开关（`card_show_*`），可裁剪成极简卡片。
- **邀请守卫深度联动**：画像插件吸收邀请守卫前科，邀请守卫读取画像标签和风险分，形成双向只读的信息闭环。
- **低开销采集**：消息监听路径零 LLM、零网络，内存聚合后定期写入 AstrBot KV；模型只在需要画像时调用。

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
| `/画像扫描 <QQ号>` | 管理员 | 手动扫描指定 QQ 的 AstrBot 会话历史 |
| `/画像扫描 本群` | 管理员 | 分批扫描当前群的未完成人员 |
| `/画像扫描 全部` | 管理员 | 分批扫描所有已知未完成人员 |
| `/画像删除 自己` | 本人 | 删除自己的统计、摘录和 LLM 缓存 |
| `/画像删除 <QQ号>` | 管理员 | 删除指定用户的全部画像数据 |
| `/画像清理` | 管理员 | 立即清理超过保留期的摘录 |

批量扫描受 `history_scan_batch_limit` 和 `history_scan_concurrency` 限制，处理中会按阶段报告调用成功/失败及分页完成/待续扫人数，最终另列批次外剩余人数。单次页数不足时保存下一页水位，再次执行会优先继续未完成人员；指定 QQ 手动扫描从第一页强制重扫。已完成扫描按 `history_rescan_interval` 增量复查。

命令兜底只匹配完整边界，`/我是...`、`/画像测试...` 等普通文本不会误触发。

## 画像包含什么

有数据的项目才会显示；全部为空时返回“暂无记录”。

- QQ 号、可获取时的昵称，以及仅在查询阶段短时获取的 QQ 头像；头像失败时使用占位图，不持久保存；
- 群聊/私聊发言数、活跃群数、首次与最近发言时间；
- 人物印象、人格/行为分析、行为统计和可选的最近发言摘录；
- 带“风险 / 正向 / 行为”文字分类的中文椭圆药丸标签，风险用柔和暖色、正向用绿色、行为用蓝紫色，颜色只作辅助；
- LLM 对各项语义判断给出的置信度；
- 供 LLM 工具和可信插件读取的标签依据 `evidence`；
- 0–100 综合风险分及低、中、高、极高等级；
- 好友添加、验证语、进群来源及推测来源群；
- 邀请、拒绝、拉 bot 入群和黑名单等个人联动记录，以及明确标注的群级 bot 禁言背景。

风险分用于辅助判断，不应作为自动处罚的唯一依据。LLM 标签也可能误判，重要决策请结合原始记录人工复核。

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
| `quote_keep` | `10` | 每个 QQ 保存的最近原话条数 |
| `quote_retention_days` | `0` | 按天清理实时摘录；0 表示不按时间过期 |
| `max_tracked_users` | `5000` | 超限后成批清理最不活跃用户及其统计、摘录、标签缓存 |
| `flush_interval` | `60` | KV 落盘间隔，单位秒，最小按 10 处理 |
| `history_fallback` | `true` | 没有已采集原话时尝试读取 AstrBot 会话历史 |

### 历史扫描

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `history_scan_enabled` | `true` | 启用按需历史扫描；关闭后历史未知时不打新人标签 |
| `history_scan_pages` | `3` | 单次最多读取的会话页数，未完成时保存下一页水位 |
| `history_scan_page_size` | `10` | 每页会话数 |
| `history_scan_cooldown` | `3600` | 未完成扫描自动续跑的冷却秒数 |
| `history_rescan_interval` | `86400` | 扫描完成后从第一页检查新会话的间隔秒数 |
| `history_scan_concurrency` | `4` | 批量扫描最大并发数 |
| `history_scan_batch_limit` | `200` | `/画像扫描 本群/全部` 每批处理人数 |

历史扫描只读取 **AstrBot 已保存的会话历史**，严格匹配说话人 QQ；页中途失败不会标记完成。旧 `history_complete` 数据会自动补齐版本、页水位和计数字段。Bot 从未保存过的 OneBot 服务端历史无法补齐。

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
  "traits": ["高频推广", "重复表达"]
}
```

模型只能返回固定白名单标签；`reason` 会清除控制字符并截断到 160 字，`impression` 截断到 240 字，`traits` 最多 5 项且每项截断到 48 字，重复项会去除。发言摘录继续放在明确的 `<untrusted_evidence>` 不可信证据边界内，不能作为提示词指令。旧 tags-only 缓存因 schema 版本变化会在首次查询时自动刷新。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 画像语义分析总开关 |
| `llm_provider_id` | `""` | 执行画像分析的 provider；留空使用 AstrBot 默认模型 |
| `llm_tag_cache_ttl` | `86400` | 成功及空结果缓存秒数；材料/provider/schema 改变立即失效 |
| `llm_failure_cache_ttl` | `300` | 超时或失败的短期缓存秒数 |
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

### 风险评分与联动

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `risk_weights` | `""` | 标签权重 JSON；仅接受内置标签的有限数值，未知或非法项忽略并回退内置权重 |
| `risk_level_low` | `30` | 低到中风险分界 |
| `risk_level_high` | `60` | 中到高风险分界 |
| `risk_level_extreme` | `80` | 高到极高风险分界 |
| `link_invite_guard` | `true` | 只读读取邀请守卫记录并生成前科标签 |
| `link_qq_tools_ban` | `true` | 只读读取 qq_tools 的 `ban_list` |

自定义权重示例：

```json
{"ban_history": 40, "scam_suspect": 35, "friendly": -10, "normal": -15}
```

### 社交来源

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `collect_social_events` | `true` | 记录好友添加、好友申请验证语和进群事件 |
| `show_social_origin` | `true` | 在画像中展示社交来源 |
| `guess_source_group` | `true` | 缺少真实来源时，以发言最多的群作为明确标注的推测来源 |

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
```

接口说明：

- `get_decision_profile(qq, event=None, exclude_request_key="") -> dict`：兼容既有 schema v2 决策接口；保留 `tags` 等旧字段并附加 `impression`、`traits`，不包含发言原话，可排除当前审核记录。`partial_errors` 当前稳定报告 `history_scan_failed` 和 `llm_analysis_failed`。
- `get_profile_tags_with_score(qq, event=None) -> dict`：返回风险分、等级、标签，并附加 `impression`、`traits`；旧调用方可继续只读取原字段。
- `get_profile_tags(qq, event=None) -> list[dict]`：返回结构化标签。
- `get_risk_score(qq, event=None) -> int`：返回风险分。
- `get_profile_text(qq, event=None) -> str`：返回可读文字画像。
- `get_social_origin(qq) -> dict`：可信内部接口，返回好友添加、验证语和进群来源；验证语仍属敏感、不可信输入，调用方必须限长且不得当作指令。

画像插件兼容邀请守卫的旧字符串、旧单条字典和当前按群多条记录三种存储格式。纯邀请前科、纯黑名单或纯社交来源的用户也能生成画像。手动打包或复制插件目录时必须包含 `profile_core.py`；`main.py` 会优先按包内相对路径导入该纯职责模块，并为 AstrBot 的直接加载方式保留同目录导入兼容。

## 数据、隐私与性能

- 原话和统计保存在 Bot 自己的 AstrBot KV 中；升级不会主动清空数据，新隐私开关默认保持旧行为。
- `collect_private` 控制统计，`store_private_quotes` 控制落盘，`llm_include_private_quotes` 控制发送给模型，三者相互独立。
- 群聊查询永不展示私聊摘录或好友申请验证语；私聊查询可以显示，可信内部 API 可读取验证语供受限审核使用。
- 启用 LLM 标签后，允许的近期摘录会发送给所选模型提供方；请按隐私政策配置。
- 采集路径零 LLM、零网络；统计内存聚合后按 `flush_interval` 写入 KV。图片查询阶段会在线程中以 2.5 秒超时读取受限 QQ 官方域名头像，最多 2 MiB，不落盘；失败直接使用占位头像。
- 每个 QQ 只保留最近 `quote_keep` 条，并可按 `quote_retention_days` 清理；消息热路径只检查当前 QQ，启动和管理员 `/画像清理` 才全量检查。
- LLM 成功、空结果和短期失败都会缓存；同 QQ 同材料合并调用，不同 QQ 受有限 semaphore 控制，不同材料乱序完成时只有最新请求可更新缓存。
- `/画像删除` 通过每 QQ 世代号使删除前已经在途的消息、历史扫描和 LLM 分析失去写回资格，不持锁等待；删除后的新消息仍可重新建立画像。
- 容量淘汰同步删除统计、摘录、标签缓存；画像仍以 QQ 号为身份维度。

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

建议升级后在 WebUI 中检查并保存一次配置，同时删除手工配置中的废弃键 `public_query`、`self_query_only`、`enable_self_command`。

## 开源许可

本项目基于 [MIT License](./LICENSE) 开源。
