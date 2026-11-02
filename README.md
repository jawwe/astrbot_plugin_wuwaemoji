# 鸣潮表情包 · AstrBot 插件

<img src="logo.png" alt="鸣潮表情包插件 Logo" width="128" height="128">

[![Plugin checks](https://github.com/jawwe/astrbot_plugin_wuwaemoji/actions/workflows/test.yml/badge.svg)](https://github.com/jawwe/astrbot_plugin_wuwaemoji/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-1.3.0-blue)](CHANGELOG.md)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.29.0-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

使用 [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games) 的 [API](https://emoji.wuwa.games/api)，搜索和随机发送鸣潮表情包，提供可视化管理页面、人物与情感用途并行分类缓存、手动及批量缓存、AstrBot 模型工具检索和直接发图。

## 安装

要求 AstrBot 4.29.0 或更新版本、Python 3.12+，并能访问 `emoji.wuwa.games` 和 `media.wuwa.games`。

1. 打开 AstrBot WebUI 的插件管理，选择通过仓库链接安装，填写 `https://github.com/jawwe/astrbot_plugin_wuwaemoji`。
2. 安装完成后，在插件配置中填写自己的 `api_token`；留空可使用匿名额度。
3. 保存配置并重载插件，发送 `/鸣潮表情帮助`。

也可以把本项目放到 AstrBot 的 `data/plugins/astrbot_plugin_wuwaemoji/`，在 AstrBot 的 Python 环境中安装 `requirements.txt` 后重启或重载插件。源码和安装包不包含任何个人 Token。

## 平台与验证范围

插件使用 AstrBot 标准文本、图片消息组件，不依赖专有平台接口。已在 AstrBot 4.29.0 验证命令注册、消息结果构建、真实 API 查询和图片下载，尚未连接 QQ、Telegram 等实际聊天平台验证发送，因此暂不声明 `support_platforms` 认证列表。多图数量、动态 WebP 展示及平台图片审核结果取决于对应适配器与平台能力。

## 可视化管理页面

更新后重载插件，在 **AstrBot WebUI → 插件 → 鸣潮表情包 → 表情图库** 打开管理视图。如果旧页面没有显示新入口，请刷新插件详情。

- **本地图库**：分页查看缩略图，按人物、用途和关键词组合筛选；可单张编辑用途与图片描述，也可勾选本页多张进行批量归类。人物标签继续自动维护。
- **小站分类 → 表情包 → 图片**：进入“小站搜索”自动拉取小站的人物目录，可切换画师目录、按名称查找分类、分页或刷新。点击分类会拉取对应表情包，支持人物和画师同时筛选；点击具体包即可查看其中图片。目录显示小站的包数和图片数，数据来自官方 `/characters`、`/artists`、`/packs` 接口，目录缓存 5 分钟，点击“刷新目录”可立即重拉。网站其他标签（如企划、官方/动态静态分组）未在此 API 提供同等筛选参数，因此不作为伪分类注入。
- **小站搜索与单张缓存**：也可直接按人物、画师、包标题关键词、指定表情包筛选。搜索和预览不会自动入库；点击“缓存此图”可收藏单张，勾选后点击“缓存已选”可一次收藏本页最多 20 张。
- **按条件批量缓存**：展开批量面板，设置起始页、连续页数和最多缓存张数。每个任务最多连续 5 页、100 张，每页数量遵循 `cache_batch_size` 配置；必须至少指定人物、画师、表情包或关键词之一。
- **按包缓存**：选择具体表情包后点击“批量缓存本包”，按照该包及每页数量持续拉取，每次最多 100 张；不会因普通查询的 5 页限制提前停止。小于 100 张的包可拉取到末页，超过 100 张的包仅缓存本次前 100 张，可在批量面板指定后续起始页继续，不会静默遍历整站。
- **动图播放**：GIF、动态 WebP、APNG 在页面中直接播放，保留源文件帧、时长、循环信息。本地旧缓存无需重新下载。关闭“播放动图”时显示静态封面，滚出可视范围会暂停，滚回后恢复；系统设置“减少动态效果”时默认暂停，可手动开启。
- **进度与取消**：后台任务显示成功数、失败数、处理上限及最近错误。刷新页面后可以重新连接最近任务；取消在当前图片处理完毕后停止，保留已成功缓存的图片。同一实例每次只运行一个页面缓存任务，重复提交同一请求不会重复执行。
- **用途分类**：页面中可新建分类并填写用途说明，直接供 AstrBot 模型理解和选图。

远程搜索结果只在内存保留 10 分钟，过期后请重新搜索。任务及进度也保存在内存中，插件重载会停止任务，但已完成的图片不会丢失；页面不支持断点续传。静态图使用小缩略图，动画保留原文件直接播放；单张动画超过 8 MiB 时提示无法直接预览，仍可按缓存下载上限保存原图，不会冒充为已播放的静态图。服务端预览内存限制为 32 MiB。批量任务可能因为去重而“处理成功数大于新增图片数”，图片容量达到上限时会逐项报告失败。

页面使用 AstrBot 官方视图桥接接口及后台登录鉴权，不能通过双击本地 HTML 使用。API Token 留在服务器；浏览器只取得不含路径和签名链接的元数据、静态预览或动图内容，不直接获取原始凭据或缓存文件路径。

## 命令

以下示例使用默认唤醒前缀 `/`，实际以前台 AstrBot 设置为准。

| 命令 | 功能 |
| --- | --- |
| `/鸣潮表情` | 全库随机一张 |
| `/鸣潮表情 爱弥斯` | 随机一张该角色表情 |
| `/鸣潮搜图 爱弥斯` | 搜索角色表情并展示图片 |
| `/鸣潮搜图 角色=爱弥斯 画师=雾雪 页码=2` | 多条件筛选及分页 |
| `/鸣潮搜图 关键词=慵懒` | 按所属包标题或资源 ID 搜索 |
| `/鸣潮搜图 表情包=aimisi-xinjilemu-outfit-1` | 查询指定表情包 |
| `/鸣潮取图 2` | 单独发送上次搜索的第 2 张 |
| `/鸣潮角色 爱` | 查询角色名称、slug 和表情数量 |
| `/鸣潮图库` | 查看本地分类、用途说明与容量 |
| `/鸣潮图库 检索 开心` | 检索分类说明、图片描述、角色及包标题，展示图片和缓存 ID |
| `/鸣潮本地 开心` | 离线随机发送“开心”分类中的一张图片 |
| `/鸣潮本地 开心 角色=爱弥斯` | 情感用途和角色同时满足；也可只写 `角色=爱弥斯` |
| `/鸣潮表情帮助` | 查看命令帮助 |

别名：`呜哇表情`、`鸣潮随机表情`、`呜哇搜图`、`鸣潮表情搜索`、`呜哇取图`、`呜哇角色`、`呜哇帮助`。

筛选也可写英文参数 `character`、`artist`、`pack`、`q`、`page`、`match`。无参数名的内容视为角色名称；含空格的值使用英文引号，例如 `画师="Some Artist"`。多个角色用英文逗号分隔。`匹配=and` 同时满足条件（默认），`匹配=or` 满足任一条件。随机命令支持角色、画师、表情包及匹配方式，不支持关键词或页码。

API 的 `q` 搜索所属表情包标题或资源 ID，并不识别图片上的文字。角色和画师按文章标签归属，多角色包可能包含其他角色。取图记录按会话及发送用户隔离，有效期 10 分钟；每页序号从 1 开始。缓存成功后使用本地图片，未缓存成功时保留临时链接发送；请勿把远程 URL 当作永久链接收藏。

## 人物与情感用途并行分类

2026-11-02 检查 [小站完整标签页](https://emoji.wuwa.games/tags) 和 API 文档，公开标签主要包括角色、画师、动态/静态、企划、分组等，未见单图情绪字段。因此插件区分两个来源：

- **人物维度**：从 API 的角色名称和 slug 自动建立“角色：爱弥斯”等分类，保留画师及表情包标题。沿用上游文章标签，不保证多角色包里的每一张都只含该人物。
- **情感/用途维度**：本地预置“开心、难过、赞同、疑惑、生气、问候、未分类”，由管理员挑图归类，可扩展分类并填写用途和图片描述。未整理的图片保持“未分类”，不会因为包标题含某个词就自动判定情绪。

一张图片可同时属于人物、多个用途分类，文件按内容哈希去重，只存一份。`/鸣潮本地 开心 角色=爱弥斯` 和模型工具均支持两个条件同时满足；没有匹配时明确返回无结果，不忽略情感条件改发其他图片。

搜索和随机命令默认在成功下载后自动缓存；`/鸣潮本地`、本地检索和已有缓存 ID 的工具发送无需访问远程 API。`/鸣潮搜图` 仍执行在线搜索，以保持分页语义。缓存文件及索引保存在 AstrBot 的 `data/plugin_data/astrbot_plugin_wuwaemoji/emoji_cache/`，插件更新、重载或远程签名到期不会删除它们。

### 管理员整理

修改图库的命令仅 AstrBot 管理员可用，查看、检索、本地发送不受此限制。

| 命令 | 功能 |
| --- | --- |
| `/鸣潮图库 分类 害羞 适合害羞、被夸奖或轻微不好意思的场景` | 新建分类或更新用途说明 |
| `/鸣潮图库 收藏 开心 1,3` | 将本人当前会话最近一页搜索中的第 1、3 张归入“开心” |
| `/鸣潮图库 缓存 未分类 角色=爱弥斯 页码=1` | 从 API 缓存一页，数量由配置限制；之后逐张按用途整理 |
| `/鸣潮图库 归类 缓存ID 开心 开心笑着比耶` | 给已有图片新增用途分类、填写描述，保留人物与其他用途分类 |
| `/鸣潮图库 清空 开心` | 移除该分类的图片关联；仍被其他分类引用的文件保留 |
| `/鸣潮图库 清空 全部` | 删除所有缓存图片和索引条目，保留分类名称及说明 |

`收藏` 的序号沿用 10 分钟搜索记录，最多处理配置允许的数量；`缓存` 每次只取一页，不遍历全站。分类名可用英文引号包裹空格，不允许斜杠；“全部”是清空命令的保留字，“角色：”前缀由上游标签自动维护。图库是本实例共享的公共表情素材，不存储聊天身份或私人上传图片。

## AstrBot 模型理解与直接调用

启用当前对话模型的工具调用能力，并保持插件 `enable_llm_tools` 开启，即可用自然语言要求“发一张开心的爱弥斯表情”。插件注册三个工具：

| 工具 | 行为 |
| --- | --- |
| `list_wuwa_emoji_categories` | 返回分类名称、用途说明、来源维度和数量 |
| `search_wuwa_emojis(query, category, character)` | 在本地按分类、角色、管理员描述及包标题检索，返回真实缓存 ID；不发图 |
| `send_wuwa_emoji(emoji_id, category, character)` | 按候选 ID 或“用途＋角色”选图，直接发送到当前聊天 |

`inject_category_hints` 会将最多 20 个非空分类的简短说明加入本轮临时上下文，不修改人格或系统提示词。工具结果不包含本地路径、API Token 或签名图片地址。发送工具一次一张，仅在当前聊天发送；同一用户和会话默认冷却 10 秒，失败不会报告发送成功。

只指定角色且本地无候选时，发送工具可以联网随机获取并缓存。指定情感分类而无候选时会如实返回无结果。可通过 AstrBot 工具管理界面单独禁用这些工具；关闭插件的 `enable_llm_tools` 后所有工具都会返回停用状态且不发图。

这里的“理解”来自模型读取分类用途、图片文字描述及工具参数，不是自动看图识别。当前版本没有调用额外视觉/向量模型、自动收集群聊图片、图床同步或特殊回复标记解析；分类是否准确取决于管理员整理。即使工具可用，也不保证每个模型都会主动调用。

## 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `api_token` | 空 | 留空使用匿名额度；填写自己的 Token |
| `page_size` | 4 | 每页展示 1–8 张图片 |
| `image_format` | webp | 默认 WebP，动态资源可保留动画；可选 original 原始格式 |
| `timeout` | 20 | API 超时秒数，3–60 |
| `cache_enabled` | true | 自动缓存以及本地图库；关闭时保留原文件 |
| `cache_max_items` | 500 | 最大缓存数量，1–5000 |
| `cache_max_size_mb` | 256 | 图片总容量上限（MiB），1–2048；索引不计入图片容量 |
| `cache_image_limit_mb` | 10 | 单张下载上限（MiB），1–30 |
| `cache_batch_size` | 8 | 批量缓存每页数量，1–20；命令取一页，管理页可连续取多页 |
| `enable_llm_tools` | true | 允许三个模型工具执行；模型需支持工具调用 |
| `inject_category_hints` | true | 将已有分类摘要加入本轮临时上下文 |
| `tool_cooldown_seconds` | 10 | 按用户及会话限制工具发图间隔，0–300；0 为关闭 |

HTTP 请求支持运行环境的 `HTTP_PROXY` / `HTTPS_PROXY`。插件不打印请求头、Token 或上游错误正文。限流、Token 错误、无结果、网络错误均返回中文提示。图片发送及动画展示取决于平台适配器及平台能力。

图片下载不附带 API Token，每一步跳转都验证小站/媒体域名；只接受通过格式与像素尺寸校验的 PNG/JPEG/GIF/WebP。达到数量或容量限制后停止新增，不自动淘汰管理员整理的图片；可清空全部释放容量。普通在线命令在缓存失败时仍尝试用原链接发图，管理员批量缓存则报告成功与失败数量。

本地实测部分原始 GIF/PNG 的 CDN 下载返回 HTTP 567，同批 WebP 下载正常，因此默认使用 WebP。若切换 `original` 后发图失败，请改回 `webp`。

按照 API 文档，匿名每分钟 15 次、自助 Token 每分钟 60 次，所有接口共享额度；每次分页请求计一次调用。图片经签名 URL 发送给平台适配器，下载时不附加 API Token。

## 本地验证

从源码仓库开发时使用 PowerShell 7，文本编码统一为 UTF-8（无 BOM）、LF 换行。在已安装 AstrBot 依赖的 Python 环境中运行：

```powershell
python -m pytest -q tests
ruff format --check main.py emoji_cache.py admin_page.py tests scripts
ruff check main.py emoji_cache.py admin_page.py tests scripts
node --check views/admin/app.js
python scripts/validate_plugin_layout.py
python scripts/astrbot_smoke.py --astrbot-root E:\code\AstrBot
```

添加 `--live` 可读取 AstrBot `data/config/astrbot_plugin_wuwaemoji_config.json` 中的 Token，验证真实 API 搜索、随机及图片下载和本地缓存。冒烟测试使用独立临时图库并清理，不修改正式图库；模型工具发送只验证模拟发送流程，不代表已通过真实聊天平台或真实模型选图验证。该脚本不输出 Token 或签名图片地址。

GitHub Actions 自动运行格式检查、回归测试、插件布局及安装包检查，并使用 AstrBot 源码环境执行兼容性冒烟测试，不需要个人 API Token。

管理页浏览器回归测试使用 Playwright、AstrBot 原始 bridge SDK 和独立临时测试图库，覆盖单张/多张缓存、多页任务、取消、用途编辑、保存后刷新失败、亮暗主题及手机布局，不读取真实凭据。在已安装 `playwright` 的 Node 环境中设置 `ASTRBOT_TEST_ROOT` 和 `ASTRBOT_TEST_PYTHON` 后运行 `node scripts/test_admin_ui.cjs`。如需使用本机 Chrome，可设置 `ADMIN_UI_CHROME` 为其可执行文件路径。截图写入被 Git 忽略的 `.pytest_cache/admin-ui/`。

## 发布

运行 `python scripts/package_plugin.py` 生成仅含运行所需文件、README、更新日志、许可证和 Logo 的 ZIP。脚本从 `metadata.yaml` 读取版本并检查大小小于 16 MiB；`.gitattributes` 也会在 Git 归档时排除测试及开发文件。

本仓库可直接用于 AstrBot 仓库链接安装。若要申请插件市场收录，按 [AstrBot 官方发布文档](https://docs.astrbot.app/dev/star/plugin-publish.html) 在 [AstrBot Cloud](https://cloud.astrbot.app/publish) 提交本仓库链接；GitHub 发布与市场收录是两个独立步骤。

## 反馈与许可

问题及建议请提交到 [GitHub Issues](https://github.com/jawwe/astrbot_plugin_wuwaemoji/issues)。排查问题时可提供 AstrBot 版本、平台适配器、脱敏后的报错和复现命令，请删除 Token 与签名图片地址。

开发结构和验证流程参考 [astrbot_plugin_tataru](https://github.com/jawwe/astrbot_plugin_tataru)。分类用途说明、模型选择和直接发送的产品思路参考 [astrbot_plugin_meme_manager](https://github.com/anka-afk/astrbot_plugin_meme_manager) 的公开功能说明，本插件的缓存、检索、工具和权限代码独立实现，未复制其代码或素材。

插件代码使用 [MIT 许可证](LICENSE)，仓库 Logo 为本项目原创几何图标。表情素材版权归原作者所有，插件通过小站 API 获取临时链接并在本实例本地缓存，不捆绑进仓库或安装包；来源及个人/非商业使用边界请参见小站页面。不要把本地缓存打包转售或二次分发。
