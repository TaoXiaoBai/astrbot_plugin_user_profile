<div align="center">
  <img src="./logo.png" width="160" alt="用户画像插件 Logo">
  <h1>AstrBot 用户画像</h1>
  <p>独立采集聊天行为，由规则与 LLM 生成可读标签和风险画像。</p>

  [![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.16-6f42c1)](https://github.com/AstrBotDevs/AstrBot)
  [![Version](https://img.shields.io/badge/version-1.7.1-blue)](./metadata.yaml)
  [![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
</div>

`astrbot_plugin_user_profile` 是一个完全独立的 QQ 用户画像插件。它自己监听消息、积累统计、生成标签和风险分，不安装其它插件也能使用。

如果同时安装了[加群邀请守卫](https://github.com/TaoXiaoBai/astrbot_plugin_group_invite_review)，画像插件会只读整合邀请、拒绝、入群、被踢、禁言和黑名单等记录；邀请守卫也可以通过稳定的内部 API 读取决策画像。任何联动插件缺失或异常时，画像插件都会自动降级，不影响基础功能。

> 本插件只做记录、分析和查询，不会主动拉黑、踢人、禁言、退群，也不会修改其它插件的数据。

## 主要功能

- **独立行为采集**：统计群聊/私聊发言数、活跃群、首次与最近发言时间，以及图片、链接、二维码、@、消息长度、夜间活跃等信号。
- **自动标签与风险分**：规则标签即时生成；可由 LLM 根据最近原话补充广告、诈骗、刷屏、挑衅、友好、乐于助人等语义标签，并计算 0–100 风险分。
- **历史扫描回填**：按需扫描 AstrBot 已保存的会话历史，减少刚安装插件时将老群友误判为新人的情况。
- **社交来源记录**：记录好友添加时间、好友验证语及进群方式、群号、操作者和时间；协议未提供真实来源时可明确标注为“推测来源群”。
- **灵活查询权限**：支持仅管理员、允许查自己、全员查他人、指定群公开查询等组合。
- **文字或图片输出**：长画像可渲染成图片，避免刷屏；发言原话可以单独隐藏。
- **插件间只读联动**：可读取邀请守卫和 `astrbot_plugin_qq_tools` 的前科数据，并向可信插件提供结构化画像 API。
- **低开销采集**：消息采集路径零 LLM、零网络，内存聚合后定期写入 AstrBot KV；LLM 只在查询画像时按需调用并缓存。

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
- 人类和 LLM 都容易阅读的中文标签；
- 0–100 综合风险分及低、中、高、极高等级；
- LLM 生成的简短行为印象；
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

### LLM 标签

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 根据已保存原话生成语义标签 |
| `llm_provider_id` | `""` | 模型 provider ID；留空使用 AstrBot 默认模型 |
| `llm_tag_cache_ttl` | `86400` | 标签缓存秒数；0 表示每次重新生成，发言数变化也会失效 |

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
