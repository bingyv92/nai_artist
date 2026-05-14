# nai_artist 插件

让 bot 像真人一样发照片、画图、换衣服。

这个插件现在包含三部分能力：

- `share_visual`：根据自然语言描述生成图片，支持 `photo` 和 `drawing` 两种模式
- `change_outfit`：在启用衣柜后，根据自然语言换装意图切换当前穿搭
- WebUI：挂载在主程序 HTTP 服务上，用来测试翻译、生图、当前穿搭、整套预设和各个槽位条目

---

## 快速上手

### 1. 前置准备

在使用前，请先确认：

- 已执行过 `uv sync`
- 已准备好可用的 ikun / 7877 中转，或 idlecloud API Key
- `config/model.toml` 中已经配置好一个可用的翻译模型

### 2. 基础配置

编辑 `config/plugins/nai_artist/config.toml`：

```toml
[plugin]
enabled = true

[api]
provider = "ikun"
base_url = "https://your-ikun-gateway.example.com/v1"
api_key = "pst-xxxxxxxxxxxxxxxxxxxx"
model = "nai-diffusion-4-5-curated"
translate_model = "deepseek-chat"

[character]
base_tags = "1girl, silver hair, blue eyes, medium hair"
fixed_tags = "fox girl, white dress"
negative_tags = "lowres, bad anatomy, bad hands, text, watermark, blurry"

[wardrobe]
enabled = true
auto_daily = true
data_file = "data/nai_artist/wardrobe.json"

[webui]
mount_on_main_http = true
route_path = "/plugins/nai-artist"
```

如果你要接 ikun：

```toml
[api]
provider = "ikun"
base_url = "https://your-ikun-gateway.example.com/v1"
api_key = "pst-xxxxxxxxxxxxxxxxxxxx"
model = "nai-diffusion-4-5-curated"
translate_model = "deepseek-chat"
```

如果你要接 idlecloud：

```toml
[api]
provider = "idlecloud"
base_url = "https://api.idlecloud.cc"
api_key = "your_idlecloud_api_key"
model = "nai-diffusion-4-5-full"
timeout = 180.0
poll_interval = 5.0
translate_model = "deepseek-chat"
```

如果你要明确使用 IdleCloud 官方兼容端点：

```toml
[api]
provider = "idlecloud"
base_url = "https://api.idlecloud.cc/api/ai/generate-image"
api_key = "your_idlecloud_api_key"
model = "nai-diffusion-4-5-full"
timeout = 180.0
translate_model = "deepseek-chat"
```

如果你要接 7877：

```toml
[api]
provider = "7877"
base_url = "http://127.0.0.1:7877"
api_key = "your_7877_api_key"
model = "nai-diffusion-4-5-curated"
translate_model = "deepseek-chat"
```

几个最重要的点：
- `api.provider` 现在必须手动指定，不再根据 `base_url` 自动识别。
- 旧配置如果还在使用 `provider = "auto"`，插件会拒绝执行生图，直到你手动改成 `ikun`、`idlecloud` 或 `7877`。
- ikun 推荐填写供应商给你的站点根地址或完整的 `/v1/chat/completions` 地址；idlecloud 如果要走本站自有轮询接口，填 `https://api.idlecloud.cc`；如果要走官方兼容协议，直接填完整 `https://api.idlecloud.cc/api/ai/generate-image`；7877 同样推荐填写站点根地址或完整的 `/v1/chat/completions` 地址。`https://nai.idlecloud.cc/login`是`https://api.idlecloud.cc`的官网，具体用这个，7877请咨询言柒
- idlecloud 的 `api_key` 不是账号密码，而是站点里单独生成的 API Key。
- idlecloud 低档位出图通常更慢，建议把 `timeout` 提高到 `180` 秒左右。
- idlecloud 当 `base_url` 配成 `/api/ai/generate-image` 时，nai_artist 会按官方 NovelAI 兼容协议直接同步请求并解压返回的压缩包；当 `base_url` 配成站点根地址时，才会走本站自有的 `/api/generate_image` + `/api/get_result/{job_id}` 轮询接口。
- ikun 协议走 `/v1/chat/completions`，nai_artist 会按文档把 `prompt` / `negative_prompt` / `size` 编成 user message 的 JSON 字符串，并在响应里继续下载 markdown 图片 URL。
- 7877 协议走 `/v1/chat/completions`，正向提示词放在 user message，负面提示词放在 system message，响应里返回 markdown 图片 URL，插件会继续下载图片。
- `api.translate_model` 现在只需要填一个。普通出图的描述翻译和换装选择共用同一个模型。
- `character.base_tags` 不再直接拼进最终 prompt。它只在 `photo` 模式下作为翻译阶段的人设输入，帮助模型理解“你是谁、长什么样”。
- `character.fixed_tags` 才是会稳定拼到 `photo` 最终 prompt 里的固定词条。
- `wardrobe.enabled = true` 时，插件会加载衣柜系统，并额外暴露 `change_outfit` 动作。

### 3. 旧配置升级

如果你以前用的是：

```toml
scene_translate_model = "..."
outfit_translate_model = "..."
```

现在请手动改成：

```toml
translate_model = "..."
```

当前方案已经不再保留分离字段。

如果你以前用的是：

```toml
provider = "auto"
```

现在请手动改成以下其一：

```toml
provider = "ikun"
# 或
provider = "idlecloud"
# 或
provider = "7877"
```

### 4. 启动方式

先正常启动主程序：

```bash
uv run main.py
```

然后通过“主程序 HTTP 地址 + WebUI 子路径”访问页面。默认情况下，如果主程序 HTTP 服务监听在 `http://127.0.0.1:8000`，那么 WebUI 地址就是：

```text
http://127.0.0.1:8000/plugins/nai-artist
```

前提是 `config/core.toml` 中的 `[http_router].enable_http_router = true`。

---

## 当前行为

### `photo` 模式

- 会把 `character.base_tags` 作为翻译模型输入的一部分，帮助理解主体和取景
- 当前穿搭 tags 只会作为翻译阶段的连续性上下文输入，不再固定拼进最终 prompt
- 最终 prompt 固定由：`photo.style_tags + character.fixed_tags + 翻译结果` 组成
- 如果 `wardrobe.auto_daily = true`，出图前还可能先按日期把当前状态切到当天预设
- `wardrobe.enabled = true` 时，只有 `photo` 模式会注入当前穿搭 tags

### `drawing` 模式

- 不会自动绑定 `character.base_tags`
- 不会注入衣柜穿搭 tags
- 最终 prompt 只有：`drawing.style_tags + 翻译结果`

### 翻译模型回退

- 如果 `api.translate_model` 为空，会回退到 `UTILS_SMALL`

---

## 配置项说明

| 节 | 字段 | 默认值 | 说明 |
|---|---|---|---|
| `[plugin]` | `enabled` | `true` | 是否启用整个插件 |
| `[api]` | `provider` | `ikun` | 生图接口提供方；需要手动指定，支持 `ikun` / `idlecloud` / `7877`；旧值 `auto` 不再执行自动识别 |
| | `base_url` | `https://your-ikun-gateway.example.com/v1` | ikun 填供应商给你的站点根地址或完整 `/v1/chat/completions`；idlecloud 填 `https://api.idlecloud.cc`；7877 也填站点根地址或完整 `/v1/chat/completions` |
| | `api_key` | `""` | 访问令牌，统一走 Bearer 认证 |
| | `model` | `nai-diffusion-4-5-curated` | 实际调用的 NAI 模型名称 |
| | `timeout` | `120.0` | 请求超时（秒） |
| | `poll_interval` | `5.0` | idlecloud 轮询任务结果的间隔（秒） |
| | `translate_model` | `""` | 同时用于场景描述翻译和换装选择的模型名称；留空回退到 `UTILS_SMALL` |
| `[character]` | `base_tags` | `"1girl"` | 角色人设输入；仅 `photo` 模式下作为翻译阶段上下文，不直接拼进最终 prompt |
| | `fixed_tags` | `""` | 仅 `photo` 模式固定拼入最终 prompt 的稳定词条 |
| | `negative_tags` | 见默认值 | 负向 tags |
| `[photo]` | `style_tags` | 见默认值 | `photo` 模式固定风格词 |
| | `width` / `height` | `832 × 1216` | 图片分辨率 |
| | `steps` | `23` | 采样步数 |
| `[drawing]` | `style_tags` | 见默认值 | `drawing` 模式固定风格词 |
| | `width` / `height` | `832 × 1216` | 图片分辨率 |
| | `steps` | `23` | 采样步数 |
| `[storage]` | `cache_dir` | `data/media_cache/images/nai_artist` | 图片缓存目录 |
| | `max_cache` | `100` | 最大缓存数量 |
| `[wardrobe]` | `enabled` | `true` | 是否启用衣柜系统和换装动作 |
| | `auto_daily` | `true` | 是否在 `photo` 模式按日期自动套用每日穿搭 |
| | `data_file` | `data/nai_artist/wardrobe.json` | 衣柜 JSON 数据文件路径 |
| `[webui]` | `mount_on_main_http` | `true` | 是否将 WebUI 挂载到主程序 HTTP 服务；关闭后不再暴露 WebUI 页面 |
| | `route_path` | `/plugins/nai-artist` | WebUI 在主程序 HTTP 服务下的访问子路径 |

---

## 工作原理

### 生图链路：`share_visual`

```text
对话触发
   │
   ▼
LLM 选择 share_visual(mode, content)
   │
   ├─ photo:
   │    base_tags 作为翻译输入上下文
   │    outfit_tags 仅作为翻译阶段的连续性参考
   │    translate_model 翻译为 NAI tags
   │    最终 prompt = photo.style_tags + fixed_tags + 翻译结果
   │
   └─ drawing:
        translate_model 翻译为 NAI tags
        最终 prompt = drawing.style_tags + 翻译结果
   │
   ▼
NaiArtistService 按 provider 选择生图接口
   │
   ├─ ikun:
   │    调用 /v1/chat/completions
   │    从 message.content 里解析图片 URL 并下载
   │
   ├─ 7877:
   │    调用 /v1/chat/completions
   │    从 message.content 里解析图片 URL 并下载
   │
   └─ idlecloud:
        POST /api/generate_image 提交任务
        GET /api/get_result/{job_id} 轮询结果
   │
   ▼
解析返回的 base64 图片
   │
   ├─ 保存到 cache_dir
   └─ send_image 发给对方
```

### 换装链路：`change_outfit`

当 `wardrobe.enabled = true` 时，插件会额外注册 `change_outfit`：

- 输入是自然语言换装意图，比如“换成睡衣”“把外套脱了”“加一条项链”
- 翻译阶段会把以下内容一起提供给同一个 `translate_model`：
  - 当前所有整套预设摘要
  - 各个槽位条目摘要
  - 当前穿搭摘要
  - 最近一段对话上下文
- 翻译结果会被解析为具体换装计划，再写回衣柜 state
- 下一次 `photo` 出图时，会自动带入新的 outfit tags

---

## 衣柜系统

默认数据文件：`data/nai_artist/wardrobe.json`

衣柜系统分成四个核心概念：

### 1. 槽位

单选槽位：

- `top` 上衣
- `bottom` 下装
- `outerwear` 外套
- `shoes` 鞋子

多选槽位：

- `accessories` 饰品

### 2. 槽位条目

每个槽位条目都有三个字段：

- `name`：引用名，用来在 state 和 preset 里保存
- `description`：给换装模型理解风格和用途
- `tags`：真正会在 `photo` 最终 prompt 中注入的 booru-style tags

可以把它理解成：

- `description` 负责“理解这件衣服是什么感觉”
- `tags` 负责“真正把这件衣服画出来”

### 3. 整套预设

预设会保存一整套穿搭引用：

- 说明文字 `description`
- 四个单选槽位的引用
- 一组饰品引用

预设适合处理“整套换装”，比如：

- 睡衣
- 夏日外出
- 居家轻松穿搭
- 晚宴礼服

### 4. `daily_pool`

`daily_pool` 是每日轮换候选列表。

- 开启 `wardrobe.auto_daily` 后
- 插件会在 `photo` 模式按日期选择一套预设
- 同一天只会切一次，避免一整天内来回跳

### 5. 当前状态 `state`

衣柜运行时会维护：

- `slots`：当前实际穿着的槽位引用
- `active_preset`：如果当前状态完整来自某套预设，这里会记录预设名
- `last_auto_date`：最近一次自动每日轮换的日期

---

## LLM 触发机制

插件加载后会向 `actor` bucket 注入一条 system reminder，大意是：

- 你有手机，可以随时拍照发给对方
- 你也会画画
- 你还有一套可切换的衣柜，会根据情境自然换穿搭

当前动作如下：

### `share_visual`

| 字段 | 值 |
|---|---|
| `action_name` | `share_visual` |
| `primary_action` | `True` |

参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `mode` | `"photo" \| "drawing"` | 选择照片感图还是手绘图 |
| `content` | `str` | 自然语言画面描述 |

### `change_outfit`

仅在 `wardrobe.enabled = true` 时存在。

| 字段 | 值 |
|---|---|
| `action_name` | `change_outfit` |
| `primary_action` | `False` |

参数：

| 参数 | 类型 | 说明 |
|---|---|---|
| `description` | `str` | 自然语言换装意图，比如“换成睡衣”“脱掉外套”“再加一条项链” |

---

## WebUI 访问

WebUI 不再单独监听一个新端口，而是直接挂在主程序 HTTP 服务下面。

### 访问方式

1. 在仓库根目录启动主程序：

```bash
uv run main.py
```

2. 确认 `config/core.toml` 中 `[http_router].enable_http_router = true`。

3. 打开“主程序 HTTP 地址 + `[webui].route_path`”。默认情况下是：

```text
http://127.0.0.1:8000/plugins/nai-artist
```

如果你修改过主程序 HTTP 的 host、port 或 `[webui].route_path`，就按修改后的地址访问。

### WebUI 主要区域

上半部分是出图测试：

- 基础设定
- 这次想生成什么
- 系统是怎么理解你的
- 结果预览

下半部分是衣柜控制台。

### 衣柜控制台的当前结构

现在的衣柜控制台是三段式：

1. 左侧一级菜单

- 当前
- 预设
- 上衣
- 下装
- 外套
- 鞋子
- 饰品

2. 中间二级目录

- 顶部有“回到顶部”按钮
- 下面有“新增”按钮
- 新增出的空白条目会直接出现在按钮下方
- 再下面才是已有预设或已有槽位条目

3. 右侧编辑面板

- 编辑当前选中的预设或槽位条目
- 预设可以改名字、说明、槽位引用、饰品和每日轮换状态
- 槽位条目可以编辑 `description` 和 `tags`

### 二级目录的颜色状态

- 点击后：条目背景会变黄，表示“当前正在编辑”
- 保存后：条目会短暂变成淡蓝，表示“刚保存成功”
- 一旦继续执行别的操作，淡蓝会恢复默认；再次编辑时又会回到黄色

### “当前”页是干什么的

“当前”页仍然保留，用来做两件事：

- 直接切换当前实际穿着的槽位
- 把当前穿搭一键保存成整套预设

### 出图区的快速预设选择

出图区上方的“快速选择一套预设”现在支持留空。

- 你可以选择“暂时不套用预设”
- 这适合测试自然语言里临时写的待定服装
- 这里的下拉框只是快捷入口，不会自动替换当前 state

也就是说：

- 如果你只是从这里选中某套，但没有点“应用到当前”，当前穿搭 state 不会被改掉
- 普通 `photo` 出图默认还是读取当前衣柜 state
- 如果你想直接用某个预设试图，可以点“只预览这套”

### 页面里的修改什么时候会写回配置文件

默认不会立刻写回 TOML。

只有你主动执行以下任一操作，配置才会持久化：

- 点击“只保存当前设置”
- 勾选“把这次修改永久保存到配置文件”后再执行翻译或出图

衣柜数据则是单独写入 `wardrobe.data_file` 指向的 JSON 文件。

### WebUI 使用前需要准备什么

在打开 WebUI 之前，请先确认：

- 已经执行过 `uv sync`
- `config/plugins/nai_artist/config.toml` 里的 `api.base_url`、`api_key`、`model` 已正确填写
- `config/model.toml` 里已经配置好可用的 `translate_model`
- `config/core.toml` 里 `[http_router].enable_http_router = true`

WebUI 挂载时会自动初始化这些配置文件：

- `config/core.toml`
- `config/model.toml`
- `config/plugins/nai_artist/config.toml`

### 修改 WebUI 挂载路径

挂载路径来自：

```toml
[webui]
mount_on_main_http = true
route_path = "/plugins/nai-artist"
```

如果你想换一个子路径，比如：

```toml
[webui]
route_path = "/tools/nai-artist"
```

那么访问地址就会变成“主程序 HTTP 地址 + 新路径”，例如：

```text
http://127.0.0.1:8000/tools/nai-artist
```

如果要让局域网其他设备访问，请改的是主程序 HTTP 服务自己的监听地址，而不是 nai_artist 单独配端口。

---

## Tips

- `base_tags` 现在不是“固定拼进最终 prompt 的角色词”，而是翻译阶段的人设输入。
- 如果你有必须稳定保留的词条，请放进 `fixed_tags`，而不是继续堆到 `base_tags`。
- `photo` 模式下，如果你想完全不让衣柜影响结果，请同时确保当前穿搭为空，并关闭 `wardrobe.auto_daily` 或临时关闭 `wardrobe.enabled`。
- `drawing` 模式天然不会注入衣柜穿搭，也不会自动绑定 bot 自己的人设。
- 如果你要画别人、OC、二创角色，优先使用 `drawing`，并且只写用户已经明确给出的设定。
- `steps` 最好控制在 28 以内，以免额外消耗点数。
- 分辨率必须是 64 的倍数，例如 `832×1216`、`1216×832`、`1024×1024`。
- 翻译失败时，系统仍然会继续拼接可用的固定部分：`photo` 只会剩下 `style_tags + fixed_tags`，`drawing` 只会剩下 `style_tags`。

---

## 常见问题

### 1. 页面打不开怎么办？

优先检查：

- 你是不是在仓库根目录启动的
- 主程序是否真的已经启动并且 `config/core.toml` 中启用了 `[http_router]`
- 你访问的是不是“主程序 HTTP 地址 + `[webui].route_path`”，默认是 `http://127.0.0.1:8000/plugins/nai-artist`
- 如果你改过 `[webui].route_path`，地址是否也一起改了

### 2. 页面能打开，但出图失败怎么办？

优先检查：

- `config/plugins/nai_artist/config.toml` 里的 API 地址、模型名和密钥
- NewAPI / OneAPI 是否真的在运行
- `config/model.toml` 中 `translate_model` 对应的模型是否可用

### 3. 为什么我改了 `base_tags`，最终 prompt 里却不一定直接看到它？

这是现在的设计。

- `base_tags` 主要用于帮助翻译模型理解你的人设
- 最终 prompt 里稳定保留什么，主要看 `fixed_tags`
- `photo` 模式下的角色词会更偏“理解阶段输入”，而不是机械硬拼

### 4. 为什么我选了某套预设，但普通出图没有立刻变成那套？

因为快速预设选择本身只是快捷入口。

- 只有点“应用到当前”，当前衣柜 state 才真的被替换
- 如果只是想临时看一下某套效果，请点“只预览这套”

### 5. 衣柜里的 `description` 和 `tags` 到底分别干什么？

- `description`：给换装模型理解这件衣服是什么风格、什么语义
- `tags`：真正用来注入 `photo` prompt 的 booru-style tags

如果你只填了描述、没填 tags，那么换装模型可能知道“这是什么”，但生图时不一定能稳定画出来。