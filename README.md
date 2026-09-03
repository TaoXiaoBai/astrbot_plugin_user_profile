# 用户画像（astrbot_plugin_user_profile）

一个独立可用的 AstrBot 用户画像与自动标签引擎。插件被动统计群聊/私聊发言，生成结构化标签和 0-100 综合风险分，可供聊天查询，也可由加群邀请守卫等可信插件只读调用。

本插件不会主动拉黑、踢人、退群或修改其它插件数据。

## 功能

- 被动统计发言数、活跃群、首次/最近发言时间，以及图片、链接、二维码、@、消息长度和夜间活跃等信号。
- 每个 QQ 保存最近 N 条原话，内存累积并定期写入 AstrBot KV。
- 规则标签覆盖活跃度、社交、内容和前科；可选 LLM 语义标签覆盖广告、刷屏、诈骗、挑衅、友好等倾向。
- 根据标签权重计算综合风险分和低/中/高/极高风险等级。
- 支持文字或图片画像、QQ 头像、指定公开查询群、聊天命令与 LLM 工具。
- 按需扫描 AstrBot 已保存的会话历史，回填首次/最近出现时间与原话，避免刚安装时把群聊老人误标为新人；支持管理员手动批量预热。
- 可只读联动 `astrbot_plugin_group_invite_guard` 和 `astrbot_plugin_qq_tools`。

## 安装与命令

1. 将目录放入 AstrBot 的 `data/plugins/`，或从插件市场安装。
2. 重启 AstrBot，在 WebUI 的插件配置中按需调整。

聊天命令：

- `/画像 <QQ号>`：查询指定 QQ。
- `/画像 自己`、`/画像 我`：通过标准画像命令查询自己。
- `/我`、`/我的画像`、`/查自己`：自查询快捷命令，可由 `enable_self_shortcuts` 单独关闭。
- `/画像扫描 <QQ号|本群|全部>`：管理员手动扫描 AstrBot 已保存历史，批量预热历史状态；非管理员拒绝。批量模式每次只处理尚未完成的前 `history_scan_batch_limit` 人，剩余可重复执行 `/画像扫描 全部` 续扫，不会漏扫也不会重复扫已完成的人。

正则兜底只匹配完整命令边界，`/我是...`、`/画像测试...` 等文本不会误触发。无论 AstrBot 的 `wake_prefix` 是否为 `/`，合法的完整斜杠命令都可响应。

## 查询权限

默认采用隐私优先配置：普通用户可查询自己，但不能查询他人；AstrBot 管理员始终可查询任意人。

权限判断优先级：

1. 插件关闭：所有聊天入口和 LLM 查询工具拒绝。
2. AstrBot 管理员：可查询自己或任意他人。
3. 查询自己：由 `allow_self_query` 决定。
4. 当前群在 `group_public_query_groups`：可查询任意人。
5. `allow_other_query=true`：可查询任意人。
6. 其它情况拒绝，并向用户返回原因、在日志记录入口、发送者、目标、群号和命中规则。

权限矩阵（普通用户）：

| 场景 | 查自己 | 查他人 |
| --- | --- | --- |
| 默认配置 | 允许 | 拒绝 |
| `allow_self_query=false` | 拒绝 | 仍由其它规则决定 |
| `allow_other_query=true` | 由 `allow_self_query` 决定 | 允许 |
| 位于指定公开群 | 由 `allow_self_query` 决定 | 允许 |
| 管理员 | 允许 | 允许 |

注意：指定公开群是“查询他人”的例外，不会绕过 `allow_self_query=false`。`enable_self_shortcuts` 只控制快捷入口，不是自查询权限；关闭后仍可在 `allow_self_query=true` 时使用 `/画像 自己` 或 `/画像 <自己的QQ>`。

典型配置：

```json
// 仅管理员
{"allow_self_query": false, "allow_other_query": false, "group_public_query_groups": ""}
```

```json
// 管理员 + 本人（默认）
{"allow_self_query": true, "allow_other_query": false, "group_public_query_groups": ""}
```

```json
// 全员可查询自己和他人
{"allow_self_query": true, "allow_other_query": true}
```

```json
// 本人可查自己，仅群 123456 和 789012 可查他人
{"allow_self_query": true, "allow_other_query": false, "group_public_query_groups": "123456,789012"}
```

## 配置

### 基础开关

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable` | `true` | 插件总开关；关闭后停止采集并拒绝聊天查询和 LLM 工具。 |

### 查询权限

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `allow_self_query` | `true` | 允许非管理员查询自己的画像。 |
| `allow_other_query` | `false` | 允许非管理员查询他人的画像。 |
| `group_public_query_groups` | `""` | 逗号、中文逗号或空白分隔群号；这些群中可查询任意他人。 |
| `enable_self_shortcuts` | `true` | 启用 `/我`、`/我的画像`、`/查自己`；不影响 `/画像 自己`。 |
| `enable_llm_tool` | `true` | 启用 `user_profile_query`；工具与命令使用相同用户权限。 |

### 输出与隐私

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `image_output` | `false` | 将画像渲染为图片；需要 Pillow，失败自动回退文字。 |
| `show_avatar` | `true` | 文字回复附加 QQ 头像；当前版本暂未实现头像附加，此配置项保留但暂不生效。 |
| `show_quotes` | `true` | 在聊天画像中展示最近发言原话。 |
| `quote_show` | `5` | 最多展示几条原话摘录。 |

### 数据采集

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `passive_collect` | `true` | 被动统计消息；关闭后已有数据保留。 |
| `collect_private` | `true` | 是否采集私聊。 |
| `collect_groups` | `""` | 仅采集指定群，留空为全部群；不控制查询权限。 |
| `quote_keep` | `10` | 每个 QQ 保存的最近原话条数。 |
| `max_tracked_users` | `5000` | 超限后清理最不活跃用户。 |
| `flush_interval` | `60` | KV 落盘间隔，单位秒，最小按 10 处理。 |
| `history_fallback` | `true` | 无采集原话时尝试从 AstrBot 会话历史补充。 |

### 历史扫描

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `history_scan_enabled` | `true` | 按需只读扫描 AstrBot 已保存的会话历史，回填首次/最近出现时间与原话。关闭后历史状态未知，不再打新人标签。 |
| `history_scan_pages` | `3` | 每次扫描最多翻几页会话历史，最小按 1。 |
| `history_scan_page_size` | `10` | 每页会话数，最小按 1；与页数相乘即单次最多读取的会话数。 |
| `history_scan_cooldown` | `3600` | 同一 QQ 两次自动扫描的最小间隔（秒），0 表示不冷却；管理员手动扫描绕过冷却。 |
| `history_scan_batch_limit` | `200` | 管理员 `/画像扫描 全部/本群` 时单次最多处理的**未完成**人数，最小按 1；剩余可重复执行续扫。 |

> 历史扫描只读取 **AstrBot 已保存的会话历史**（`conversation_manager`），不会读取 OneBot 服务器上 AstrBot 从未记录过的群历史。会话无消息级时间戳时，使用会话的 `created_at`/`updated_at` 作为保守的首次/最近时间，并据此避免误标新人。新人标签仅在历史扫描完成后、且首次出现时间足够近时才打上；历史状态未知时宁可漏标，不误伤。

### LLM 标签

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 根据原话生成语义标签；与 LLM 查询工具开关无关。 |
| `llm_provider_id` | `""` | 标签模型 provider ID，留空使用 AstrBot 默认模型。 |
| `llm_tag_cache_ttl` | `86400` | 标签缓存秒数；0 表示每次重新生成。 |

### 规则标签阈值

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `tag_active_high_threshold` | `100` | 高活跃累计发言数。 |
| `tag_active_med_threshold` | `20` | 中活跃累计发言数。 |
| `tag_newcomer_days` | `7` | 新人判定天数。 |
| `tag_multi_group_threshold` | `3` | 多群出现的群数。 |
| `tag_image_threshold` | `0.5` | 图片消息占比阈值。 |
| `tag_link_threshold` | `0.3` | 链接消息占比阈值。 |
| `tag_mention_threshold` | `0.3` | @消息占比阈值。 |
| `tag_verbose_threshold` | `80` | 平均消息字符数阈值。 |
| `tag_night_threshold` | `0.3` | 0:00-5:59 夜间消息占比阈值。 |

### 风险评分

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `risk_weights` | `""` | 标签权重 JSON，例如 `{"ban_history":40,"normal":-15}`。 |
| `risk_level_low` | `30` | 低到中风险分界。 |
| `risk_level_high` | `60` | 中到高风险分界。 |
| `risk_level_extreme` | `80` | 高到极高风险分界。 |

### 插件联动

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `link_invite_guard` | `true` | 只读读取邀请守卫记录生成前科标签。 |
| `link_qq_tools_ban` | `true` | 只读读取 qq_tools 黑名单生成标签。 |

## LLM 工具与可信内部 API

`user_profile_query(qq)` 是面向聊天对话的 LLM 工具：

- `enable_llm_tool=false` 时拒绝调用。
- 必须能从工具上下文取得真实 `AstrMessageEvent` 和发送者身份；没有真实事件时默认拒绝。
- 有事件时与标准命令、正则兜底完全使用同一权限规则，不能绕过 `allow_self_query`、`allow_other_query` 或指定群限制。

插件间 Python API 是可信内部只读接口，不是聊天用户入口，因此不受上述聊天权限约束，确保邀请守卫等插件可在后台决策中读取画像：

```python
md = self.context.get_registered_star("astrbot_plugin_user_profile")
instance = getattr(md, "star_cls", None)
if instance is not None:
    result = await instance.get_profile_tags_with_score(inviter_qq, event)
    score = await instance.get_risk_score(inviter_qq, event)
    tags = await instance.get_profile_tags(inviter_qq, event)
    text = await instance.get_profile_text(inviter_qq, event)
```

- `get_profile_tags(qq, event=None) -> list[dict]`
- `get_profile_tags_with_score(qq, event=None) -> {"score", "level", "tags"}`
- `get_risk_score(qq, event=None) -> int`
- `get_profile_text(qq, event=None) -> str`

这些接口只读、无敏感操作；调用方应是受信任插件，不应直接将其包装成无权限校验的聊天接口。

## 从 1.3.x 升级

AstrBot 通常已通过 `_flatten_plugin_config` 展平分组配置；插件仍兼容嵌套分组和旧扁平配置。新键只要存在就优先，旧键仅在对应新键缺失时迁移，不参与后续权限判断：

- `public_query` → `allow_other_query`。
- `enable_self_command` → `enable_self_shortcuts`，并在 `allow_self_query` 缺失时作为旧版自查询许可。
- `self_query_only=true` → `allow_other_query=false`；自查询默认允许，除非旧 `enable_self_command=false` 或新 `allow_self_query=false`。

升级后的安全默认值是 `allow_self_query=true`、`allow_other_query=false`、`enable_self_shortcuts=true`、`enable_llm_tool=true`。建议升级后在 WebUI 保存一次新配置，并删除外部手工配置中已废弃的 `public_query`、`self_query_only`、`enable_self_command`。

## 隐私与性能

- 原话仅保存在本 bot 的 AstrBot 数据目录；启用 LLM 标签时，原话会发送给所选模型提供方处理。
- `show_quotes=false` 只隐藏聊天输出，不停止原话采集；如需降低隐私风险，可同时关闭 `collect_private`、减小 `quote_keep` 或关闭 `llm_tags`。
- 采集路径不调用 LLM、不发起网络请求；LLM 标签按发言数和 TTL 缓存，并用锁避免并发重复生成。
- 历史扫描只读 AstrBot 已保存的会话历史，受页数、冷却和批量上限约束，扫描失败自动降级；仅在查询或管理员手动预热时触发，采集路径零额外开销。
- 昵称和头像依赖 OneBot V11；其它平台仍可使用发言统计、标签和风险分。
- 画像按 QQ 号维度，不做跨账号关联。

## License

MIT
