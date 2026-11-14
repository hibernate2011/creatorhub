# CreatorHub

本地运行的多平台内容管理工作台，支持 **抖音 / 小红书 / 快手 / 视频号 / TikTok**。

[在线预览](https://3441293738.github.io/creatorhub/) · [平台能力](#平台能力) · [使用指南](https://3441293738.github.io/creatorhub/guide/) · [下载安装](https://github.com/3441293738/creatorhub/releases/latest) · [问题反馈](https://github.com/3441293738/creatorhub/issues) · [赞赏与交流](#赞赏与交流)

<p align="center">
  <a href="https://3441293738.github.io/creatorhub/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/hero-banner-dark.svg">
      <img alt="CreatorHub 本地多平台内容管理工作台动态架构预览" src="assets/hero-banner.svg" width="100%">
    </picture>
  </a>
</p>

统一管理账号、采集与监控内容、下载素材和处理发布及互动任务。账号登录态、数据库与媒体文件保存在本地，各平台能力以[使用指南](https://3441293738.github.io/creatorhub/guide/#welcome)为准。

> 在线预览使用脱敏示例数据，仅展示界面与交互；真实登录、采集、下载和发布需要在本地运行。

## 平台能力

| 平台 | 主要功能 |
|---|---|
| 抖音 | 作品 / 评论 / 弹幕监控、批量采集、下载、发布与私信管理 |
| 小红书 | 笔记 / 评论监控、关键词与收藏采集、下载、发布与私信管理 |
| 快手 | 作品 / 评论监控、批量采集、下载与发布 |
| 视频号 | 本账号作品、数据与评论管理，批量采集与发布 |
| TikTok | 关键词视频采集、作品 / 评论监控、下载、视频发布与用户互动 |

> 视频号仅处理本账号内容；TikTok 当前支持单视频发布，暂不支持图文发布与直播。详细能力、操作步骤和使用限制见[使用指南](https://3441293738.github.io/creatorhub/guide/#welcome)。

## 快速开始

### 评论 AI 潜客分析

在「评论监控 → AI 潜客分析配置」填写 OpenRouter API Key，可开启抓取后自动分析。历史评论可在「评论记录」中勾选后点击「分析所选」，也可点击「分析所有记录」，将所有平台尚未分析的评论加入队列，范围包含当前筛选和分页以外的记录。使用 [Gemini Decision 1](https://openrouter.ai/google/gemini-decision-1) 的 [Decisions API](https://openrouter.ai/docs/guides/community/multimodal-decisions)，使用同一提示词版本和排除规则的评论可以跨作品、跨平台合并请求。每条 Question 包含平台、作品标题、用户昵称和评论内容，昵称用于辅助判断行业与需求的匹配度。列表可按分析状态和潜客置信度筛选，当前结果导出遵循相同筛选。

- 内置默认提示词，可添加行业规则并关联一个或多个账号/作品监控、作品 ID 或监控分组。匹配优先级为作品 ID → 监控任务 → 分组 → 默认；同级按列表顺序。修改规则自动递增版本，已入队的评论保留提示词快照。
- 以平台、作品 ID、评论 ID 去重，跨监控任务复用结果；同一评论以首次入队时的规则为准。成功结果不会因修改提示词、重复抓取或删除后重新抓取而重新分析。
- 可配置多个钉钉/飞书群机器人，各自设置最低置信度、关键词和签名密钥。开启自动转发后，新完成的分析按目标阈值入队；已有结果可勾选「按阈值转发所选」。发出内容包含评论、用户、作品标题、可用作品链接和置信度。
- 在「评论监控 → 监控目标 → 编辑 → AI 潜客分析与推送」中，可让单个目标继承全局设置，或单独开启、关闭自动分析和结果推送。推送可使用全局渠道，也可配置该目标专用的钉钉或飞书机器人及阈值。关闭自动分析仍可手动分析，关闭目标推送会停止该目标的自动和手动转发。
- 分析与转发独立持久化，转发失败可在评论行单独重试。发送前会检查当前推送设置和阈值。同一条评论在不同监控目标间复用分析结果，同一渠道的成功推送会去重。
- 置信度表示模型认为“可能成为潜客”的概率，不代表实际成交率。自动分析和自动转发默认关闭；启用自动分析仅处理之后新抓取的评论。密钥和 webhook 保存于本地数据库，接口返回时隐藏。

### Windows 安装版（推荐）

1. 在 [Releases](https://github.com/3441293738/creatorhub/releases/latest) 的 Assets 中下载 `CreatorHub-Setup-版本-windows-x64.exe`。
2. 双击安装，通过桌面快捷方式启动。
3. 点击「启动本地服务」，等待浏览器组件准备完成，打开工作台并登录账号。

适用于 Windows 10/11 x64，无需手动安装 Python；安装与首次启动请保持联网。版本更新见[更新与备份](https://3441293738.github.io/creatorhub/guide/#backup)。

### 源码运行（开发者 / macOS / Linux）

准备 Python 3.10+ 和桌面环境，其他依赖与平台要求见[安装指南](https://3441293738.github.io/creatorhub/guide/#install)。

```bash
git clone https://github.com/3441293738/creatorhub.git
cd creatorhub
```

Windows：

```powershell
.\start.cmd
```

macOS / Linux：

```bash
chmod +x start.sh
./start.sh
```

首次运行自动准备虚拟环境、Python 依赖、浏览器组件和 `config.yaml`，随后打开 `http://127.0.0.1:8000`。

## 使用文档

详细功能、平台差异、操作步骤和配置统一维护在 **[使用指南](https://3441293738.github.io/creatorhub/guide/)**：

- 按平台上手：[抖音](https://3441293738.github.io/creatorhub/guide/douyin/) · [小红书](https://3441293738.github.io/creatorhub/guide/xhs/) · [快手](https://3441293738.github.io/creatorhub/guide/kuaishou/) · [视频号](https://3441293738.github.io/creatorhub/guide/shipinhao/) · [TikTok](https://3441293738.github.io/creatorhub/guide/tiktok/)
- 日常使用：[功能手册](https://3441293738.github.io/creatorhub/guide/#monitor) · [配置与账号环境](https://3441293738.github.io/creatorhub/guide/#settings) · [更新与备份](https://3441293738.github.io/creatorhub/guide/#backup)
- 开发参考：[配置示例](config.example.yaml) · [更新日志](CHANGELOG.md) · [文档维护](guide/README.md)

## 技术栈

基于 Python、FastAPI、SQLite 与兼容 Playwright 的浏览器自动化接口。浏览器按平台使用系统 Chrome/CDP 或 [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)。

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/3441293738/creatorhub/star-history/assets/star-history-dark.svg">
  <img alt="CreatorHub Star History Chart" src="https://raw.githubusercontent.com/3441293738/creatorhub/star-history/assets/star-history.svg">
</picture>

## 赞助商

<p align="center">
  <a href="https://www.ipwo.net/?code=PPBFE3E2F" target="_blank" rel="noopener noreferrer">
    <img src="assets/sponsors/ipwo-banner.jpg" alt="IPWO 住宅代理：195+ 地区，多协议适配，动态 / 静态 / 不限量代理" width="100%">
  </a>
</p>

<p align="center">
  <a href="https://www.ipwo.net/?code=PPBFE3E2F" target="_blank" rel="noopener noreferrer">IPWO</a>
  住宅代理资源，适用于爬虫开发、数据研究、市场分析等场景，为开发者探索更多数据应用方向提供支持。
  <br>
  覆盖 195+ 地区，包含动态代理 / 静态代理 / 不限量代理，支持 HTTP / HTTPS / SOCKS5 多种协议。
  可免费试用，优惠折扣码：<code>0201</code>。
</p>

## 赞赏与交流

如果 CreatorHub 对你有帮助，欢迎自愿赞赏，支持项目持续维护与改进。

欢迎加入 **CreatorHub 交流群**，交流使用经验、问题反馈和功能建议。

<table>
  <tr>
    <th align="center" width="33%">赞赏支持</th>
    <th align="center" width="33%">扫码加群</th>
    <th align="center" width="33%">添加作者微信</th>
  </tr>
  <tr>
    <td align="center" valign="middle">
      <a href="assets/community/wechat-reward.jpg">
        <img src="assets/community/wechat-reward.jpg" alt="CreatorHub 作者微信赞赏码" width="240">
      </a>
    </td>
    <td align="center" valign="middle">
      <a href="assets/community/wechat-group.png">
        <img src="assets/community/wechat-group.png" alt="CreatorHub 交流群二维码" width="240">
      </a>
    </td>
    <td align="center" valign="middle">
      <a href="assets/community/wechat-personal.jpg">
        <img src="assets/community/wechat-personal.jpg" alt="作者个人微信二维码，扫码添加好友" width="240">
      </a>
    </td>
  </tr>
</table>

<p align="center">
  使用微信扫码赞赏、加入交流群或添加作者，点击图片可查看原图。
  <br>
  赞赏金额随心，感谢每一份支持！入群遇到问题，也可以添加作者微信联系。
</p>

## 友链

- [LINUX DO](https://linux.do/) — 感谢社区提供的帮助与支持。

## 使用须知

本项目用于技术学习和个人内容管理，不提供账号、Cookie、代理或平台数据。使用时请遵守目标平台规则及所在地法律法规，并尊重内容版权和个人隐私。
