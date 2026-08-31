# 用户画像（astrbot_plugin_user_profile）

一个**独立**的 AstrBot 用户画像分析插件：自己监听消息、自己积累数据、自己生成画像，不依赖任何其它插件就能完整使用。

它默默记下每个 QQ 用户在群聊/私聊里的发言习惯——发言总数、活跃群、最近都说了什么——查询时再由 LLM 浓缩成一段印象小结。群里有人发广告、来路不明的人私聊 bot、或者你单纯想知道"这人什么来头"，`/画像 <QQ号>` 一下就有答案。

纯只读：本插件不做任何拉黑、踢人、退群等敏感操作。

## 功能

**被动采集（零 LLM 零网络，不占聊天性能）**
- 静默监听群聊/私聊消息，按 QQ 统计：发言总数、活跃群数、首次/最近发言时间
- 每个 QQ 保留最近 N 条发言原话（默认 10 条，可配）
- 内存累积、每分钟批量落盘（kv 存储），不打断任何事件、不影响正常聊天

**画像查询**
- 命令：`/画像 <QQ号>`（群聊私聊均可，默认公开，可改仅管理员）
- LLM 工具：`user_profile_query(qq)`，bot 聊天时可自主调用查询
- 画像内容（有几项写几项，全没有回"暂无记录"）：
  - 昵称/头像（OneBot `get_stranger_info` + qlogo 头像）
  - 活跃度：发言总数、活跃群数、首次/最近发言时间
  - 发言风格：最近原话摘录 + LLM 浓缩的印象小结（按发言数缓存，发言数没变就用缓存；LLM 失败降级为只列原话）
  - 自己没采集到时，用 AstrBot 会话历史搜索补充原话

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

**画像生成**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `llm_summary` | `true` | 是否生成 LLM 印象小结；关闭后只列原话 |
| `llm_provider_id` | `""` | 小结用模型，留空用 AstrBot 默认模型 |
| `summary_max_chars` | `100` | 印象小结字数上限 |
| `quote_show` | `5` | 画像里展示最近几条原话摘录 |
| `history_fallback` | `true` | 未采集到此人时用 AstrBot 会话历史补充 |

**插件联动**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `link_invite_guard` | `true` | 画像里显示加群邀请守卫的前科记录 |
| `link_qq_tools_ban` | `true` | 画像里显示 qq_tools 黑名单条目 |

## 与加群邀请守卫的联动（可选，装了才生效）

本插件独立可用；如果检测到装了「加群邀请守卫」（astrbot_plugin_group_invite_guard），联动自动开启，无需配置：

- 通过 AstrBot 共享偏好存储（`astrbot.core.sp`）只读访问邀请守卫的 `invite_records` / `join_records` / `mute_records`，以及 qq_tools 的 `ban_list`
- 画像里多出「前科记录」段：TA 邀请 bot 进过哪些群及处理结果、操作拉群记录、所在群禁言 bot 次数、黑名单条目（含原因）
- 邀请守卫没装、读取失败时对应段落自动省略，不影响画像其余部分

### 给邀请守卫的 API

邀请守卫（或其它插件）可以反过来读取画像，用于进群决策等场景：

```python
md = self.context.get_registered_star("astrbot_plugin_user_profile")
instance = getattr(md, "star_cls", None)
if instance is not None and hasattr(instance, "get_profile_text"):
    text = await instance.get_profile_text(inviter_qq, event)  # event 可传 None
    if text:
        ...  # 拼进决策 prompt
```

- `get_profile_text(qq, event=None) -> str`：返回画像文本；未采集到数据时返回空字符串，方便判空
- 只读、无副作用；`event` 传 None 时自动跳过昵称/头像段

## 性能设计

对比同类插件（如 astrbot_plugin_portrayal 每次查询都拉群聊记录 + 调 LLM），本插件把成本压到最低：

- **采集路径零 LLM 零网络**：监听只更新内存，每分钟批量落盘一次 kv
- **LLM 小结按发言数缓存**：发言数没变直接用缓存；并发查询有锁防重复调用；LLM 失败降级为只列原话
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
