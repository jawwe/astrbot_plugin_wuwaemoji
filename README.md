# 鸣潮表情包 · AstrBot 插件

<img src="logo.png" alt="鸣潮表情包插件 Logo" width="128" height="128">

[![Plugin checks](https://github.com/jawwe/astrbot_plugin_wuwaemoji/actions/workflows/test.yml/badge.svg)](https://github.com/jawwe/astrbot_plugin_wuwaemoji/actions/workflows/test.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-blue)](CHANGELOG.md)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.29.0-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

使用 [呜哇小站 · 表情包仓鼠库](https://emoji.wuwa.games) 的 [API](https://emoji.wuwa.games/api)，搜索和随机发送鸣潮表情包。

## 安装

要求 AstrBot 4.29.0 或更新版本、Python 3.12+，并能访问 `emoji.wuwa.games` 和 `media.wuwa.games`。

1. 打开 AstrBot WebUI 的插件管理，选择通过仓库链接安装，填写 `https://github.com/jawwe/astrbot_plugin_wuwaemoji`。
2. 安装完成后，在插件配置中填写自己的 `api_token`；留空可使用匿名额度。
3. 保存配置并重载插件，发送 `/鸣潮表情帮助`。

也可以把本项目放到 AstrBot 的 `data/plugins/astrbot_plugin_wuwaemoji/`，在 AstrBot 的 Python 环境中安装 `requirements.txt` 后重启或重载插件。源码和安装包不包含任何个人 Token。

## 平台与验证范围

插件使用 AstrBot 标准文本、图片消息组件，不依赖专有平台接口。已在 AstrBot 4.29.0 验证命令注册、消息结果构建、真实 API 查询和图片下载，尚未连接 QQ、Telegram 等实际聊天平台验证发送，因此暂不声明 `support_platforms` 认证列表。多图数量、动态 WebP 展示及平台图片审核结果取决于对应适配器与平台能力。

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
| `/鸣潮表情帮助` | 查看命令帮助 |

别名：`呜哇表情`、`鸣潮随机表情`、`呜哇搜图`、`鸣潮表情搜索`、`呜哇取图`、`呜哇角色`、`呜哇帮助`。

筛选也可写英文参数 `character`、`artist`、`pack`、`q`、`page`、`match`。无参数名的内容视为角色名称；含空格的值使用英文引号，例如 `画师="Some Artist"`。多个角色用英文逗号分隔。`匹配=and` 同时满足条件（默认），`匹配=or` 满足任一条件。随机命令支持角色、画师、表情包及匹配方式，不支持关键词或页码。

API 的 `q` 搜索所属表情包标题或资源 ID，并不识别图片上的文字。角色和画师按文章标签归属，多角色包可能包含其他角色。取图记录按会话及发送用户隔离，有效期 10 分钟；每页序号从 1 开始。图片 URL 是服务签发的临时地址，请勿当作永久链接收藏。

## 配置

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `api_token` | 空 | 留空使用匿名额度；填写自己的 Token |
| `page_size` | 4 | 每页展示 1–8 张图片 |
| `image_format` | webp | 默认 WebP，动态资源可保留动画；可选 original 原始格式 |
| `timeout` | 20 | API 超时秒数，3–60 |

HTTP 请求支持运行环境的 `HTTP_PROXY` / `HTTPS_PROXY`。插件不打印请求头、Token 或上游错误正文。限流、Token 错误、无结果、网络错误均返回中文提示。图片发送及动画展示取决于平台适配器及平台能力。

本地实测部分原始 GIF/PNG 的 CDN 下载返回 HTTP 567，同批 WebP 下载正常，因此默认使用 WebP。若切换 `original` 后发图失败，请改回 `webp`。

按照 API 文档，匿名每分钟 15 次、自助 Token 每分钟 60 次，所有接口共享额度；每次分页请求计一次调用。图片经签名 URL 发送给平台适配器，下载时不附加 API Token。

## 本地验证

从源码仓库开发时使用 PowerShell 7，文本编码统一为 UTF-8（无 BOM）、LF 换行。在已安装 AstrBot 依赖的 Python 环境中运行：

```powershell
python -m pytest -q tests
ruff format --check main.py tests scripts
ruff check main.py tests scripts
python scripts/validate_plugin_layout.py
python scripts/astrbot_smoke.py --astrbot-root E:\code\AstrBot
```

添加 `--live` 可读取 AstrBot `data/config/astrbot_plugin_wuwaemoji_config.json` 中的 Token，验证真实 API 搜索、随机及图片下载。该脚本不输出 Token 或签名图片地址。

GitHub Actions 自动运行格式检查、回归测试、插件布局及安装包检查，并使用 AstrBot 源码环境执行兼容性冒烟测试，不需要个人 API Token。

## 发布

运行 `python scripts/package_plugin.py` 生成仅含运行所需文件、README、更新日志、许可证和 Logo 的 ZIP。脚本从 `metadata.yaml` 读取版本并检查大小小于 16 MiB；`.gitattributes` 也会在 Git 归档时排除测试及开发文件。

本仓库可直接用于 AstrBot 仓库链接安装。若要申请插件市场收录，按 [AstrBot 官方发布文档](https://docs.astrbot.app/dev/star/plugin-publish.html) 在 [AstrBot Cloud](https://cloud.astrbot.app/publish) 提交本仓库链接；GitHub 发布与市场收录是两个独立步骤。

## 反馈与许可

问题及建议请提交到 [GitHub Issues](https://github.com/jawwe/astrbot_plugin_wuwaemoji/issues)。排查问题时可提供 AstrBot 版本、平台适配器、脱敏后的报错和复现命令，请删除 Token 与签名图片地址。

开发结构和验证流程参考 [astrbot_plugin_tataru](https://github.com/jawwe/astrbot_plugin_tataru)。插件代码使用 [MIT 许可证](LICENSE)，仓库 Logo 为本项目原创几何图标。表情素材版权归原作者所有，插件通过小站 API 获取临时链接，不捆绑分发表情素材；来源及二次配布要求请参见小站页面。
