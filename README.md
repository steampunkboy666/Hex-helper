<p align="center"><img src="assets/icon.png" width="88" height="88" alt="Hex Helper 项目标志"></p>

# Hex Helper · 英雄联盟海克斯大乱斗助手

Windows 桌面助手，适用于英雄联盟海克斯大乱斗（ARAM Mayhem）。选人时比较英雄强度，打开商店时查看出装，选择强化时根据当前英雄和已选强化比较候选项。

**[下载最新版](https://github.com/steampunkboy666/Hex-helper/releases/latest)**　·　[版本记录](https://github.com/steampunkboy666/Hex-helper/releases)　·　[反馈问题](https://github.com/steampunkboy666/Hex-helper/issues/new/choose)

## 最新稳定版 v1.4.0

支持国服和台服官方简繁译名，内置离线 OCR，并支持三选一和四选一。关闭商店后，助手会根据画面自动收起出装面板。反馈入口可提交截图、复现描述和诊断摘要。

商店关闭逻辑已通过回归测试，三选一已用国服实机截图回放，四选一已用简繁体样图验证。鼠标关店、真实 Maestro 场景和台服整局仍在收集实测反馈。

## 开始使用

1. 在对应发布页的 **Assets** 中下载 Windows x64 的 `.exe` 文件，放到固定文件夹。适用于 64 位 Windows 10 / 11，无需安装 Python。
2. 双击启动并完成 Windows 管理员确认。按**游戏内文字**选择「国服 · 简体中文」或「台服 · 繁體中文」。控制台会显示数据来源、客户端连接状态和 OCR 引擎。
3. 打开英雄联盟，进入海克斯大乱斗。游戏窗口建议使用「无边框」。也可以先点控制台的「预览效果」查看面板。

升级前从系统托盘退出旧版，再把新 EXE 放回同一文件夹，即可沿用旁边 `data/` 中的设置和缓存。各版本发布页均提供附件的 SHA-256，可在 PowerShell 中用 `Get-FileHash .\文件名.exe` 核对。

## 对局中的助手

| 阶段 | 你会看到什么 |
| --- | --- |
| **选择英雄** | 当前英雄和板凳席英雄的梯队、胜率、排名、装备和强化建议。面板靠近客户端边缘摆放，优先显示缓存，并标出更新时间。 |
| **打开商店** | 三件高胜率非鞋装备、三套非鞋核心出装，以及已拥有、可购买或还差多少金币。关闭商店后，助手复查画面并收起推荐。开启导入后，可在客户端的「Hex Helper · 英雄名」配装页中查看，鞋子单独列出。 |
| **选择强化** | 支持三选一和四选一。助手结合英雄单项统计、已选强化组合和描述中的联动给出推荐顺序，并提示后续可搭配的强化。强化推荐可在控制台单独缩放。 |
| **对局结束** | 客户端提供结算数据后，战报卡展示参团、伤害、承伤和治疗等表现，并保存到 `data/reports/`。 |

出装导入和赛后战报默认开启，可在「显示与功能」中分别关闭。希望对局中少看几行文字时，可将面板信息量切换为「精简」。

## 数据与推荐依据

- 英雄榜、强化和装备按数据类型尝试 [aramgg](https://aramgg.com/)、[海斗榜](https://haidoubang.com/) 和 [OP.GG](https://op.gg/lol/modes/aram-mayhem)。海斗榜补英雄及单强化胜率、单件装备统计；OP.GG 兜底英雄与强化梯队，并补完整的非鞋三件套。海斗榜使用区服未核验的混合样本。
- 装备排序考虑样本量，面板展示原始胜率和场次。强化组合优先参考该英雄已有的三件组合统计，基于描述关键词的联动属于推断。暂无英雄样本的新强化会展示名称，取得统计后再参与排序。
- 榜单缓存 6 小时，英雄详情缓存 12 小时。站点不可用时优先沿用本地缓存。
- 海克斯识别适配国服、台服官方译名。台服译名按游戏 ID 匹配，胜率沿用数据站共用样本。助手从 KIWI 模式资料补充新强化，内置 OCR 字库来自 [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)。[字库许可证](assets/tessdata/LICENSE)

<details>
<summary><strong>快捷键、窗口布局与性能设置</strong></summary>

| 操作 | 默认键位 |
| --- | --- |
| 打开／关闭游戏商店 | `P` |
| 手动开关出装面板 | `Ctrl+Alt+S` |
| 打开强化速查 | `Ctrl+Alt+A` |
| 撤销上次强化记录 | `Ctrl+Alt+Z` |
| 隐藏／显示全部面板 | `Ctrl+Alt+H` |
| 缩小／放大面板 | `Ctrl+Alt+[` ／ `Ctrl+Alt+]` |
| 面板移到左／右侧 | `Ctrl+Alt+,` ／ `Ctrl+Alt+.` |

可在 `data/settings.json` 修改快捷键。游戏商店改键后，将 `shop_key` 设成实际使用的键。助手按游戏窗口所在显示器、Windows 缩放、商店位置及卡片边缘摆放面板；`panel_top` 和 `panel_bottom` 控制面板的纵向范围。

默认启用防闪烁模式（`overlay_safe`）。助手只截取强化标题区域，识别较慢时会降低扫描频率。强化面板默认显示 25 秒，`offer_panel_secs` 可调整时长，设为 0 可持续显示。使用可变刷新率时若仍出现异常，可在反馈中注明显卡驱动、窗口模式和刷新率。

</details>

<details>
<summary><strong>诊断、源码运行与打包</strong></summary>

控制台可「重新自检」「导出诊断文件」或「反馈问题」。反馈向导依次收集截图、复现描述和可预览的诊断摘要。确认后可一键提交并获得反馈编号。反馈包存入维护者的私有存储，保留 30 天。网络失败时可另存 ZIP。[提交问题](https://github.com/steampunkboy666/Hex-helper/issues/new/choose) · [维护者部署说明](feedback-worker/README.md)

在 Windows 项目目录中运行：

```powershell
py -3 -m pip install -r requirements.txt
py -3 main.py --demo
py -3 main.py
.\build.bat
```

演示模式模拟选人和局内流程，打包产物位于 `dist/HexHelper.exe`。截图识别、缓存、布局、强化推荐及战报的回归脚本见 [tests/](tests/)。

</details>

## 作者与许可

作者为 **steampunk boy**。本软件采用 **CC BY-NC-ND 4.0**，允许署名后免费使用和原样分享；商业使用或分发修改版需另行取得作者授权。[项目许可证](LICENSE)

Hex Helper 是独立开发的第三方工具，与 Riot Games、腾讯无隶属关系。助手使用本机客户端接口、Live Client Data API 和屏幕识别；开启配装导入时会写入助手自己的装备页。游戏名称与素材归相应权利人所有，使用请遵守游戏运营方及数据来源的相关条款。
