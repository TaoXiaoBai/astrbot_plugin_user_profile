# 用户画像（astrbot_plugin_user_profile）

一个**完全独立**的 AstrBot 插件：自己监听消息、自己积累数据、自己生成画像，不依赖任何其它插件也能用。如果检测到装了「加群邀请守卫」（astrbot_plugin_group_invite_guard），自动把邀请/被禁言等前科纳入画像；再装了 qq_tools，还会显示黑名单记录。

纯只读：本插件不做任何拉黑、踢人、退群等敏感操作。

## 功能

**被动采集（默认开，零 LLM 零网络）**
- 静默监听群聊/私聊消息，按 QQ 统计：发言总数、活跃群数、首次/最近发言时间
- 每个 QQ 保留最近 N 条发言原话（默认 10 条，可配）
- 内存累积、每分钟批量落盘（kv 存储），不打断任何事件、不影响正常聊天

**画像查询**
- 命令：`/画像 <QQ号>`（群聊私聊均可，默认公开，可改仅管理员）
- LLM 工具：`user_profile_query(qq)`，bot 聊天时可自主调用查询
- 画像内容（有几项写几项，全没有回"暂无记录"）：
  - 昵称/头像（OneBot `get_stranger_info` + qlogo 头像）
  - 活跃度：发言总数、活跃群数、首次/最近发言时间（自有统计）
  - 发言风格：最近原话摘录 + 一次 LLM 浓缩的 100 字印象小结（按发言数缓存，发言数没变就用缓存；LLM 失败降级为只列原话）
  - 前科联动（装了邀请守卫才有）：TA 邀请 bot 进过哪些群及处理结果、操作拉群记录、所在群禁言 bot 次数；qq_tools 黑名单中的条目（含原因）
  - 自己没采集到时，用 AstrBot 会话历史搜索补充原话

## 安装

1. 把本目录放进 AstrBot 的 `data/plugins/`（或在插件市场搜索安装）
2. 重启 AstrBot
3. WebUI → 插件 →「用户画像」里按需改配置

## 配置

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `public_query` | `true` | 是否允许非管理员用 /画像 |
| `passive_collect` | `true` | 是否被动采集发言统计；关闭后已有数据保留 |
| `max_tracked_users` | `5000` | 最多跟踪多少个 QQ，超过自动清理最不活跃的 |
| `llm_summary` | `true` | 是否生成 LLM 印象小结；关闭后只列原话 |
| `llm_provider_id` | `""` | 小结用模型，留空用 AstrBot 默认模型 |
| `quote_keep` | `10` | 每人保留的最近原话条数 |

## 与加群邀请守卫的联动

联动是**自动**的，无需配置：

- 本插件通过 AstrBot 的共享偏好存储（`astrbot.core.sp`，plugin 作用域）只读访问邀请守卫的 `invite_records` / `join_records` / `mute_records`，以及 qq_tools 的 `ban_list`
- 邀请守卫没装、版本不含这些数据、读取失败时，对应段落自动省略，不影响画像其余部分

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
- **查询路径全并发**：昵称、邀请守卫前科、黑名单用 `asyncio.gather` 并行拉取，单点失败互不影响
- **体积有上限**：每人只留最近 N 条原话（默认 10），跟踪人数超限（默认 5000）自动清理最不活跃的

## 隐私说明

- 本插件会记录群员的发言原话（每人最近 N 条），且 `/画像` 默认**任何人都能查任何人**
- 介意的群请关闭 `public_query`（仅管理员可查）或 `passive_collect`（停止采集）
- 画像数据仅存于本 bot 的 AstrBot 数据目录，不上传任何第三方

## 已知局限

- 画像严格按 QQ 号维度，**没有跨账号关联**：同一个人换个 QQ 号就是一份新档案
- 只支持 OneBot V11（aiocqhttp）的昵称/头像/前科联动；其它平台仅保留发言统计

## License

MIT
