<div align="center">
  <img src="./logo.png" width="160" alt="用户画像插件 Logo">
  <h1>AstrBot 用户画像</h1>
  <p>以 LLM 语义理解为分析核心，把聊天记录转化为可读标签、判断依据和风险画像。</p>

  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-6f42c1)](https://github.com/AstrBotDevs/AstrBot)
  [![Version](https://img.shields.io/badge/version-1.7.2-blue)](./metadata.yaml)
  [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
</div>

`astrbot_plugin_user_profile` 是一个 **LLM 驱动的独立 QQ 用户画像插件**。插件持续积累群聊、私聊、社交来源和管理前科等证据，再由 LLM 阅读近期真实发言、结合行为统计理解语义，生成带置信度和理由的结构化标签，最终形成可供人类查看、也可供其它插件直接消费的风险画像。

这里的重点不是简单统计“发了多少条消息”，而是让 LLM 回答更接近实际判断的问题：这个人长期在聊什么、表达方式如何、是否像广告引流或诈骗、是否经常刷屏挑衅、是否友好可靠，以及这些判断分别基于哪些发言和行为信号。

插件不依赖其它插件也能完成采集和画像分析。如果同时安装了[加群邀请守卫](https://github.com/TaoXiaoBai/astrbot_plugin_group_invite_review)，画像插件会把邀请、拒绝、入群、被踢、禁言和黑名单等事实加入分析材料；邀请守卫则可直接读取结构化画像，让 LLM 审核邀请时不只看本次申请，而是参考邀请人的长期行为和历史标签。任何联动插件缺失或异常时，基础画像功能仍可独立运行。

> 本插件只做记录、分析和查询，不会主动拉黑、踢人、禁言、退群，也不会修改其它插件的数据。画像和风险分用于辅助判断，不应替代人工复核。

## LLM 在插件中做什么

LLM 是画像的**语义分析核心**，规则统计是它的**证据底座和故障降级保障**。完整流程如下：

1. **持续积累证据**：插件静默收集近期发言原话、群聊/私聊活跃度、图片、链接、二维码、@、夜间发言、社交来源及管理前科。
2. **规则预处理**：先把客观数据转换为高活跃、多群出现、链接偏多、邀请被拒、禁言前科等基础标签和行为比例。
3. **LLM 阅读与理解**：查询画像时，LLM 同时阅读近期原话、基础标签和行为比例，不只按关键词匹配，而是结合上下文识别广告引流、诈骗索要信息、刷屏、重复内容、挑衅、友好、乐于助人、正常交流等倾向。
4. **输出结构化判断**：LLM 返回最多 5 个语义标签；每个标签都包含 `tag`、`confidence` 和 `reason`，便于人类阅读，也方便邀请守卫等插件稳定调用。
5. **合并形成画像**：插件将 LLM 标签与客观规则标签合并，再按可配置权重计算 0–100 风险分和风险等级。
6. **缓存与复用**：分析结果按用户发言数和 TTL 缓存。发言数变化或缓存过期后重新分析，避免同一批材料被并发重复提交给模型。

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
- **文字或图片输出**：长画像可渲染成图片，避免刷屏；发言原话可以单独隐藏。
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

批量扫描每次最多处理 `history_scan_batch_limit` 个**尚未完成**的用户。人数较多时重复执行命令即可续扫，已完成用户不会重复扫描。

命令兜底只匹配完整边界，`/我是...`、`/画像测试...` 等普通文本不会误触发。

## 画像包含什么

有数据的项目才会显示；全部为空时返回“暂无记录”。

- QQ 号、昵称和头像；
- 群聊/私聊发言数、活跃群数、首次与最近发言时间；
- 行为统计和最近发言摘录；
- 人类和其它插件都容易读取的中文语义标签；
- LLM 对各项语义判断给出的置信度；
- 供 LLM 工具和可信插件读取的标签依据 `evidence`；
- 0–100 综合风险分及低、中、高、极高等级；
- 好友添加、验证语、进群来源及推测来源群；
- 邀请、拒绝、被踢、禁言和黑名单等联动记录。

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
| `image_output` | `false` | 将画像渲染为图片；失败时自动回退文字 |
| `show_avatar` | `true` | 预留的文字头像选项，当前版本暂不生效 |
| `show_quotes` | `true` | 是否在聊天画像中展示最近发言原话 |
| `quote_show` | `5` | 最多展示的原话条数 |

`show_quotes=false` 只隐藏输出，不停止原话采集和 LLM 分析。如果希望减少敏感数据处理，请同时关闭 `collect_private`、缩小 `quote_keep` 或关闭 `llm_tags`。

### 数据采集

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `passive_collect` | `true` | 静默采集发言统计和行为信号 |
| `collect_private` | `true` | 是否采集私聊消息 |
| `collect_groups` | `""` | 仅采集指定群；留空表示全部群，不影响查询权限 |
| `quote_keep` | `10` | 每个 QQ 保存的最近原话条数 |
| `max_tracked_users` | `5000` | 超限后清理最不活跃用户 |
| `flush_interval` | `60` | KV 落盘间隔，单位秒，最小按 10 处理 |
| `history_fallback` | `true` | 没有已采集原话时尝试读取 AstrBot 会话历史 |

### 历史扫描

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `history_scan_enabled` | `true` | 启用按需历史扫描；关闭后历史未知时不打新人标签 |
| `history_scan_pages` | `3` | 每次最多读取的会话页数 |
| `history_scan_page_size` | `10` | 每页会话数 |
| `history_scan_cooldown` | `3600` | 同一 QQ 自动扫描冷却秒数；手动扫描不受限 |
| `history_scan_batch_limit` | `200` | `/画像扫描 本群/全部` 每批处理的未完成人数 |

历史扫描只能读取 **AstrBot 已保存的会话历史**，无法获取 Bot 从未记录过的 OneBot 服务端群历史。会话缺少消息级时间戳时，插件使用会话创建/更新时间作为保守估计。

### LLM 画像分析

这组配置决定画像的语义分析能力。开启后，模型收到的是“近期真实发言 + 规则标签 + 行为比例”，返回机器可读的结构化判断，例如：

```json
[
  {
    "tag": "ad_suspect",
    "confidence": 0.82,
    "reason": "多次发送二维码、联系方式和引流文案"
  },
  {
    "tag": "repetitive",
    "confidence": 0.74,
    "reason": "近期反复发送高度相似的内容"
  }
]
```

`reason` 会保存为标签的 `evidence`，供聊天 LLM 工具和邀请守卫等可信插件读取。普通 `/画像` 页面以紧凑方式显示标签和置信度，避免长理由刷屏。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 画像语义分析总开关；让 LLM 生成带置信度和理由的行为标签 |
| `llm_provider_id` | `""` | 执行画像分析的模型 provider ID；留空使用 AstrBot 默认模型 |
| `llm_tag_cache_ttl` | `86400` | 分析结果缓存秒数；0 表示每次查询都重新分析，发言数变化也会使缓存失效 |

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
| `risk_weights` | `""` | 标签权重 JSON；正数提高风险，负数降低风险，留空使用内置权重 |
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

插件注册 `user_profile_query(qq)`，允许聊天 LLM 按需查询画像。

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

- `get_decision_profile(qq, event=None, exclude_request_key="") -> dict`：推荐的决策接口；返回风险分、结构化标签、精简活跃度和社交来源，不包含发言原话，并可排除当前审核记录。
- `get_profile_tags_with_score(qq, event=None) -> dict`：返回风险分、等级和标签。
- `get_profile_tags(qq, event=None) -> list[dict]`：返回结构化标签。
- `get_risk_score(qq, event=None) -> int`：返回风险分。
- `get_profile_text(qq, event=None) -> str`：返回可读文字画像。
- `get_social_origin(qq) -> dict`：返回好友添加、验证语和进群来源。

画像插件兼容邀请守卫的旧字符串、旧单条字典和当前按群多条记录三种存储格式。纯邀请前科、纯黑名单或纯社交来源的用户也能生成画像。

## 数据、隐私与性能

- 原话和统计保存在 Bot 自己的 AstrBot KV 中；更新插件不会主动清空数据。
- 启用 LLM 标签后，近期原话会发送给所选模型提供方处理，请根据群规和隐私要求决定是否开启。
- 采集路径不调用 LLM、不查询昵称、不请求网络。
- 统计先在内存聚合，再按 `flush_interval` 批量落盘。
- 每个 QQ 只保留最近 `quote_keep` 条原话，不保存完整聊天历史副本。
- LLM 标签按发言数和 TTL 缓存，并使用并发锁避免重复请求。
- 昵称、历史、联动数据读取均可单点失败并自动降级。
- 画像以 QQ 号为身份维度，当前不自动合并多个 QQ 账号。

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
