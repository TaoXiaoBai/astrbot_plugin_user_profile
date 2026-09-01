# 用户画像（astrbot_plugin_user_profile）

一个**独立可用**的 AstrBot 用户画像 / 自动标签引擎：自己监听消息、自己积累数据、自己生成标签和风险分，不依赖任何其它插件也能完整使用。

它的核心产出是**结构化标签 + 综合风险分**：从群聊/私聊发言中自动统计活跃度、风险、社交、内容等特征，再由 LLM 从原话里打语义标签（广告嫌疑、抬杠、正常等）。标签和风险分可直接被「加群邀请守卫」等插件读取，用于辅助判断"这人能不能进群"。

纯只读：本插件不做任何拉黑、踢人、退群等敏感操作。

## 功能

**被动采集（零 LLM 零网络，不占聊天性能）**
- 静默监听群聊/私聊消息，按 QQ 统计：发言总数、活跃群数、首次/最近发言时间
- 额外行为信号：图片/链接/二维码/@次数/消息长度/夜间活跃
- 每个 QQ 保留最近 N 条发言原话（默认 10 条，可配）
- 内存累积、每分钟批量落盘（kv 存储），不打断任何事件、不影响正常聊天

**自动标签（为决策服务）**
- 基础标签（规则生成，零成本）：
  - 活跃度：`高活跃` / `较活跃` / `低活跃` / `新人` / `长期沉寂`
  - 社交：`多群出现` / `私聊活跃`
  - 风险：`黑名单记录` / `拉群前科` / `禁言前科` / `频繁邀请` / `邀请被拒`
  - 内容：`图片刷屏` / `链接刷屏` / `二维码刷屏` / `频繁@人` / `话痨` / `夜间活跃`
- LLM 语义标签（默认开启）：`广告嫌疑` / `刷屏嫌疑` / `抬杠/钓鱼` / `诈骗嫌疑` / `表现正常` 等
- 每个标签带 `confidence`（置信度）、`source`（来源）、`evidence`（证据摘要）

**综合风险分**
- 根据标签权重自动计算 0-100 风险分
- 风险等级：`低` / `中` / `高` / `极高`
- 权重可配置，正负权重均可（如 `normal` 为负向，降低风险分）

**画像查询**
- 命令：`/画像 <QQ号>`（群聊私聊均可，默认公开，可改仅管理员）
- 查自己：(`/画像 自己` 或 `/画像 我`)、`/我的画像`、`/查自己`、快捷命令 `/我`
- 无论 AstrBot 的 `wake_prefix` 是不是 `/`，直接以 `/` 开头的命令都会响应
- 可选图片形式输出（`image_output`），避免刷屏
- LLM 工具：`user_profile_query(qq)`，bot 聊天时可自主调用查询
- 细粒度权限：可指定某些群全员可查，也可开启“仅允许查自己”

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
| `show_avatar` | `true` | /画像 文字回复是否附 QQ 头像 |
| `image_output` | `false` | /画像 以图片形式发送，避免刷屏；需要 Pillow |

**查询权限**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `public_query` | `true` | 是否允许非管理员用 `/画像` 查任意 QQ |
| `self_query_only` | `false` | 开启后非管理员只能查自己的画像（管理员和指定公开群除外） |
| `group_public_query_groups` | `""` | 逗号分隔群号；在这些群里所有成员都能查任意 QQ 画像 |
| `enable_self_command` | `true` | 是否启用 `/我的画像`（含 `/查自己`、`/我`）快捷命令 |

权限优先级（高→低）：管理员 → 指定公开群 → 仅查自己 → 全局公开 → 拒绝。

典型场景：
- 全局公开：`public_query=true`（默认）。
- 仅管理员可查：`public_query=false`。
- 全员只查自己，但某些群可以任意查：`self_query_only=true`，并把群号填到 `group_public_query_groups`。

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
| `llm_tag_cache_ttl` | `86400` | LLM 标签缓存多少秒后刷新；0 表示每次都重新生成 |
| `quote_show` | `5` | /画像 里展示最近几条原话摘录 |
| `history_fallback` | `true` | 未采集到此人时用 AstrBot 会话历史补充 |
| `tag_active_high_threshold` | `100` | 累计发言数达到多少打 `高活跃` |
| `tag_active_med_threshold` | `20` | 累计发言数达到多少打 `较活跃` |
| `tag_newcomer_days` | `7` | 首次记录在多少天内打 `新人` |
| `tag_multi_group_threshold` | `3` | 活跃群数达到多少打 `多群出现` |
| `tag_image_threshold` | `0.5` | 图片消息占比超过多少打 `图片刷屏` |
| `tag_link_threshold` | `0.3` | 含链接消息占比超过多少打 `链接刷屏` |
| `tag_mention_threshold` | `0.3` | 含@消息占比超过多少打 `频繁@人` |
| `tag_verbose_threshold` | `80` | 平均消息长度超过多少字打 `话痨` |
| `tag_night_threshold` | `0.3` | 夜间（0-5点）发言占比超过多少打 `夜间活跃` |

**风险评分**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `risk_weights` | `""` | JSON 字符串，自定义每个标签的权重，例如 `{"ban_history":40,"normal":-15}` |
| `risk_level_low` | `30` | 风险分达到多少显示为"中" |
| `risk_level_high` | `60` | 风险分达到多少显示为"高" |
| `risk_level_extreme` | `80` | 风险分达到多少显示为"极高" |

**插件联动**

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `link_invite_guard` | `true` | 读取加群邀请守卫的前科记录生成风险标签 |
| `link_qq_tools_ban` | `true` | 读取 qq_tools 黑名单生成 `黑名单记录` 标签 |

## 与加群邀请守卫的联动

本插件独立可用；如果检测到装了「加群邀请守卫」（`astrbot_plugin_group_invite_guard`），联动自动开启，无需配置：

- 通过 AstrBot 共享偏好存储（`astrbot.core.sp`）只读访问邀请守卫的 `invite_records` / `join_records` / `mute_records`，以及 qq_tools 的 `ban_list`
- 生成 `频繁邀请`、`邀请被拒`、`拉群前科`、`禁言前科`、`黑名单记录` 等风险标签
- 邀请守卫没装、读取失败时对应标签自动省略，不影响其它标签

### 给邀请守卫的 API

邀请守卫（或其它插件）可以读取标签和风险分，用于进群决策：

```python
md = self.context.get_registered_star("astrbot_plugin_user_profile")
instance = getattr(md, "star_cls", None)
if instance is not None:
    # 带风险分的完整结果
    result = await instance.get_profile_tags_with_score(inviter_qq, event)
    # result = {"score": 72, "level": "高", "tags": [...]}

    # 只看风险分
    score = await instance.get_risk_score(inviter_qq, event)

    # 只看标签
    tags = await instance.get_profile_tags(inviter_qq, event)
    risk_tags = {"ban_history", "kick_history", "mute_history", "invite_rejected", "spam_suspect", "ad_suspect", "scam_suspect", "troll"}
    if any(t["tag"] in risk_tags for t in tags):
        # 提高警惕或拒绝
        pass
```

- `get_profile_tags(qq, event=None) -> list[dict]`：返回标签列表，每个标签含 `tag` / `confidence` / `source` / `evidence`；无数据返回 `[]`
- `get_profile_tags_with_score(qq, event=None) -> dict`：返回 `{"score", "level", "tags"}`
- `get_risk_score(qq, event=None) -> int`：返回 0-100 风险分
- `get_profile_text(qq, event=None) -> str`：保留，返回人类可读画像文本；无数据返回空串
- 只读、无副作用；`event` 传 None 时自动跳过昵称/头像

### 标签格式示例

```json
{
  "score": 72,
  "level": "高",
  "tags": [
    {"tag": "ban_history", "confidence": 0.95, "source": "ban_list", "evidence": "在 bot 黑名单中（1 条记录）"},
    {"tag": "ad_suspect", "confidence": 0.82, "source": "llm", "evidence": "多次发送二维码和联系方式"},
    {"tag": "active_high", "confidence": 0.95, "source": "stats", "evidence": "发言总数 523（群聊 511 / 私聊 12）"}
  ]
}
```

## 图片输出

开启 `image_output` 后，`/画像` 会把结果渲染成一张图片发送：

- 顶部蓝色标题栏，显示 QQ 号
- 风险分用颜色高亮：绿/黄/橙/红
- 标签以蓝色圆角块展示，过多时自动换行
- 按字符显示宽度自动折行，适配中英文混排

如果环境没有安装 `Pillow`，会自动回退为文字输出。

## 性能设计

- **采集路径零 LLM 零网络**：监听只更新内存，每分钟批量落盘一次 kv
- **LLM 标签按发言数+TTL 缓存**：发言数没变且在缓存有效期内直接用缓存；并发查询有锁防重复调用；LLM 失败降级为空
- **查询路径全并发**：昵称、前科、黑名单用 `asyncio.gather` 并行拉取，单点失败互不影响
- **体积有上限**：每人只留最近 N 条原话，跟踪人数超限自动清理最不活跃的

## 隐私说明

- 本插件会记录群员的发言原话（每人最近 N 条），且 `/画像` 默认**任何人都能查任何人**
- 介意的群可关闭 `public_query`、开启 `self_query_only`、配置 `group_public_query_groups` 限定公开群，或用 `collect_groups` 限定采集范围
- 画像数据仅存于本 bot 的 AstrBot 数据目录，不上传任何第三方

## 已知局限

- 画像严格按 QQ 号维度，**没有跨账号关联**：同一个人换个 QQ 号就是一份新档案
- 昵称/头像依赖 OneBot V11（aiocqhttp）；其它平台仅保留发言统计

## License

MIT
