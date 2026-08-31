# 用户画像（astrbot_plugin_user_profile）

一个**独立可用**的 AstrBot 用户画像 / 自动标签引擎：自己监听消息、自己积累数据、自己生成标签，不依赖任何其它插件也能完整使用。

它的核心产出是**结构化标签**：从群聊/私聊发言中自动统计活跃度、风险、社交等特征，再由 LLM 从原话里打语义标签（广告嫌疑、抬杠、正常等）。标签可直接被「加群邀请守卫」等插件读取，用于辅助判断"这人能不能进群"。

纯只读：本插件不做任何拉黑、踢人、退群等敏感操作。

## 功能

**被动采集（零 LLM 零网络，不占聊天性能）**
- 静默监听群聊/私聊消息，按 QQ 统计：发言总数、活跃群数、首次/最近发言时间
- 每个 QQ 保留最近 N 条发言原话（默认 10 条，可配）
- 内存累积、每分钟批量落盘（kv 存储），不打断任何事件、不影响正常聊天

**自动标签（为决策服务）**
- 基础标签（规则生成，零成本）：
  - 活跃度：`active_high` / `active_medium` / `active_low` / `newcomer` / `long_inactive`
  - 社交：`multi_group`（多群出现） / `private_active`（私聊活跃）
  - 风险：`ban_history`（黑名单） / `kick_history`（操作拉 bot 进群） / `mute_history`（相关禁言） / `frequent_inviter` / `invite_rejected`
- LLM 语义标签（默认开启）：`spam_suspect` / `ad_suspect` / `troll` / `friendly` / `helpful` / `nsfw_tendency` / `political_sensitive` / `scam_suspect` / `repetitive` / `normal`
- 每个标签带 `confidence`（置信度）、`source`（来源）、`evidence`（证据摘要）

**画像查询**
- 命令：`/画像 <QQ号>`（群聊私聊均可，默认公开，可改仅管理员）
- LLM 工具：`user_profile_query(qq)`，bot 聊天时可自主调用查询
- 查询结果展示：昵称/头像、基础标签、LLM 标签、活跃度、最近原话摘录

## 安装

1. 把本目录放进 AstrBot 的 `data/plugins/`（或在插件市场搜索安装）
2. 重启 AstrBot
3. WebUI → 插件 →「用户画像」里按需改配置（默认配置即可直接用）

## 配置

**基础**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `enable` | `true` | 总开关；关闭后停止采集、/画像 与 LLM 工具都不再响应 |
| `public_query` | `true` | 是否允许非管理员用 /画像 |
| `show_avatar` | `true` | /画像 回复是否附 QQ 头像 |

**数据采集**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `passive_collect` | `true` | 是否被动采集发言统计；关闭后已有数据保留 |
| `collect_private` | `true` | 是否统计私聊发言；在意隐私可关掉 |
| `collect_groups` | `""` | 只采集这些群（逗号分隔群号），留空采集全部 |
| `quote_keep` | `10` | 每人保留的最近原话条数 |
| `max_tracked_users` | `5000` | 最多跟踪多少个 QQ，超过自动清理最不活跃的 |
| `flush_interval` | `60` | 采集数据落盘间隔（秒） |

**标签生成**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `llm_tags` | `true` | 是否由 LLM 生成语义标签；关闭后只出基础标签 |
| `llm_provider_id` | `""` | 标签用模型，留空用 AstrBot 默认模型 |
| `quote_show` | `5` | /画像 里展示最近几条原话摘录 |
| `history_fallback` | `true` | 未采集到此人时用 AstrBot 会话历史补充 |
| `tag_active_high_threshold` | `100` | 累计发言数达到多少打 `active_high` |
| `tag_active_med_threshold` | `20` | 累计发言数达到多少打 `active_medium` |
| `tag_newcomer_days` | `7` | 首次记录在多少天内打 `newcomer` |
| `tag_multi_group_threshold` | `3` | 活跃群数达到多少打 `multi_group` |

**插件联动**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `link_invite_guard` | `true` | 读取加群邀请守卫的前科记录生成风险标签 |
| `link_qq_tools_ban` | `true` | 读取 qq_tools 黑名单生成 `ban_history` 标签 |

## 与加群邀请守卫的联动

本插件独立可用；如果检测到装了「加群邀请守卫」（`astrbot_plugin_group_invite_guard`），联动自动开启，无需配置：

- 通过 AstrBot 共享偏好存储（`astrbot.core.sp`）只读访问邀请守卫的 `invite_records` / `join_records` / `mute_records`，以及 qq_tools 的 `ban_list`
- 生成 `frequent_inviter`、`invite_rejected`、`kick_history`、`mute_history`、`ban_history` 等风险标签
- 邀请守卫没装、读取失败时对应标签自动省略，不影响其它标签

### 给邀请守卫的 API

邀请守卫（或其它插件）可以读取标签，用于进群决策等场景：

```python
md = self.context.get_registered_star("astrbot_plugin_user_profile")
instance = getattr(md, "star_cls", None)
if instance is not None and hasattr(instance, "get_profile_tags"):
    tags = await instance.get_profile_tags(inviter_qq, event)  # event 可传 None
    risk_tags = {"ban_history", "kick_history", "mute_history", "invite_rejected", "spam_suspect", "ad_suspect", "scam_suspect", "troll"}
    if any(t["tag"] in risk_tags for t in tags):
        # 提高警惕或拒绝
        pass
```

- `get_profile_tags(qq, event=None) -> list[dict]`：返回标签列表，每个标签含 `tag` / `confidence` / `source` / `evidence`；无数据返回 `[]`
- `get_profile_text(qq, event=None) -> str`：保留，返回人类可读画像文本；无数据返回空串
- 只读、无副作用；`event` 传 None 时自动跳过昵称/头像

### 标签格式示例

```json
[
  {"tag": "active_high", "confidence": 0.95, "source": "stats", "evidence": "发言总数 523（群聊 511 / 私聊 12）"},
  {"tag": "multi_group", "confidence": 0.90, "source": "stats", "evidence": "活跃群数 5"},
  {"tag": "ad_suspect", "confidence": 0.82, "source": "llm", "evidence": "多次发送二维码和联系方式"}
]
```

## 性能设计

- **采集路径零 LLM 零网络**：监听只更新内存，每分钟批量落盘一次 kv
- **LLM 标签按发言数缓存**：发言数没变直接用缓存；并发查询有锁防重复调用；LLM 失败降级为空
- **查询路径全并发**：昵称、前科、黑名单用 `asyncio.gather` 并行拉取，单点失败互不影响
- **体积有上限**：每人只留最近 N 条原话，跟踪人数超限自动清理最不活跃的

## 隐私说明

- 本插件会记录群员的发言原话（每人最近 N 条），且 `/画像` 默认**任何人都能查任何人**
- 介意的群请关闭 `public_query`（仅管理员可查）、`collect_private`（不记私聊）或用 `collect_groups` 限定采集范围
- 画像数据仅存于本 bot 的 AstrBot 数据目录，不上传任何第三方

## 已知局限

- 画像严格按 QQ 号维度，**没有跨账号关联**：同一个人换个 QQ 号就是一份新档案
- 昵称/头像依赖 OneBot V11（aiocqhttp）；其它平台仅保留发言统计

## License

MIT
