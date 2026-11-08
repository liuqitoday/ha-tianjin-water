# 天津自来水 · Home Assistant

<img src="custom_components/tianjin_water/brand/icon.png" alt="Tianjin Water icon" width="96" height="96">

[![Checks](https://github.com/liuqitoday/ha-tianjin-water/actions/workflows/checks.yml/badge.svg)](https://github.com/liuqitoday/ha-tianjin-water/actions/workflows/checks.yml)
[![Release](https://img.shields.io/github/v/release/liuqitoday/ha-tianjin-water)](https://github.com/liuqitoday/ha-tianjin-water/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

通过天津市自来水集团线上服务查询本人缴费账户的余额、应缴金额和 **NB 水表**抄表记录。
原生 UI 配置、中文/英文界面、重新认证、异常停止与脱敏诊断，无需另外运行服务。

这是社区维护的非官方自定义集成，与天津市自来水集团、微信和 Home Assistant 官方无隶属关系。
适用于使用 `https://www.twwc.net.cn/water-supply-front` 后台的账户；其他城市、其他后台和 IC 表尚未支持。

**要求：Home Assistant Core 2026.10.4 或更新版本。** 自动测试使用 Core 2026.10.4，
核心查询及定时采集已在 Core 2026.11.0 验证；后续 HA 版本仍可能需要适配。

## 安装

### Home Assistant 2026.11+ Marketplace

打开「设置 → Marketplace」，使用从链接添加自定义仓库的入口，填入：

```text
https://github.com/liuqitoday/ha-tianjin-water
```

选择集成类别并安装，重启 HA 后从「设置 → 设备与服务」添加 **Tianjin Water**。
入口名称可能随界面语言变化；参见 [官方 Marketplace 文档](https://contributors.home-assistant.io/marketplace/)。

### 使用旧版 HACS 的环境

1. 打开 HACS 的「自定义存储库 / Custom repositories」。
2. 添加 `https://github.com/liuqitoday/ha-tianjin-water`，类别选择 **Integration**。
3. 搜索 **Tianjin Water 天津自来水**，下载并重启 Home Assistant。
4. 在「设置 → 设备与服务 → 添加集成」搜索 **Tianjin Water**。

本仓库支持 Marketplace / HACS 自定义仓库安装，尚未进入默认目录。

### 手动安装

从 [Releases](https://github.com/liuqitoday/ha-tianjin-water/releases) 下载 `tianjin_water.zip`，
解压后将 `custom_components/tianjin_water` 复制到 HA 配置目录。最终应存在：

```text
/config/custom_components/tianjin_water/manifest.json
```

重启 HA，再从「设置 → 设备与服务」添加集成。`/config` 指 HA 的配置目录；
HA Container 安装使用自己挂载的配置目录。

## 获取认证信息

需要自己账户的缴费户号，以及一次**成功的余额查询请求**使用的 `SESSION`。
凭据只能从本人已登录的水务页面取得，本项目不能用微信密码直接登录。

详细操作见 **[认证信息抓取与更新教程](docs/authentication.md)**，包括 Stream / ProxyPin 导出 HAR、
准确的接口和字段位置，以及本地离线提取方法。

```bash
python3 tools/extract_credentials.py /path/to/your-capture.har --out private/credentials.json
```

提取工具需要 Python 3.10+、只用标准库、不联网、不在终端打印凭据。
把本地输出中的 `custom_id`、`session`、`user_agent` 对应填入配置表单；
SESSION 字段也接受整个 `Cookie: SESSION=…` 请求头。User-Agent 可选，建议沿用成功请求中的值。

**不要把 HAR、Cookie、凭据文件或 HA 备份发到 GitHub。** 抓包包含的个人信息往往远多于这三个字段。

## 实体与数据含义

每个账户有 3 个实体，每块受支持的 NB 表有 4 个实体。

| 设备 | 实体 | 含义 |
| --- | --- | --- |
| 账户 | 预缴余额 | CNY，当前预缴金额 |
| 账户 | 本次应缴金额 | CNY，上游本次应缴金额 |
| 账户 | 采集状态 | 正常、等待恢复连接、需要重新认证、已停止 |
| 每块 NB 表 | 最近抄表指数 | m³，累计指数，支持水耗统计 |
| 每块 NB 表 | 本期用水量 | m³，最近一个抄表周期的增量 |
| 每块 NB 表 | 本期水费 | CNY，最近一个抄表周期的费用 |
| 每块 NB 表 | 最近抄表日期 | 实际抄表业务日期 |

这些是**抄表周期数据**，不是实时流量或每日实际用量。周期可能跨月，不应把本期用量再次累计。
仅「最近抄表指数」设置为 `water / total`；余额、周期用量和周期费用不伪装成累计统计。

本期用水量实体的 `readings` 属性最多包含 48 条历史记录，可供自定义卡片绘图。
历史数组不重复写入 Recorder，本集成不回填长期统计。首次加入 HA 后，水耗统计从 HA 开始记录时起积累。

水表使用编号的哈希作为稳定标识，列表排序改变不影响实体身份。换表创建新设备；
旧表实体变为不可用并保留历史。设备名可在 HA 中自行修改。

## 更新频率与停止规则

- 启动时查询余额、水表列表及每块 NB 表历史。
- 余额每 **10 分钟**查询；水表列表与历史每 **6 小时**查询。
- 每个账户共用一个协调器和缓存，串行请求，20 秒超时。手动刷新也受 60 秒最小间隔约束。
- 不提供高频轮询选项，不创建另一套“保活”任务。打开仪表盘读取现有实体不会额外查询水务后台。
- HTTP/业务 401、403 或明确登录失效响应：立即停止该账户请求并触发重新认证。
- 404、户号不匹配、无法理解的响应：停止并提示检查，**不直接判断为令牌过期**。
- 网络、超时、429、5xx：下次轮询再试；连续 3 次失败后停止。
- 停止标记跨 HA 重载/重启保留；重启 HA 不会重新尝试已被拒绝的凭据。

停止时数据实体变为不可用，不会把错误变成零值。状态实体在集成仍加载时可读；
停止后重启可能显示配置条目加载失败，可在集成页面和诊断中查看原因。

恢复方法见 [故障排查](docs/troubleshooting.md)。通常需要在集成菜单「重新配置」或重新认证入口中
提交可用 SESSION。更新同一条目可保留设备、实体和历史。

## 会话有效期与隐私

SESSION 不是 JWT，目前没有确认的 TTL、refresh token 或自动续期接口。
定时查询成功只证明当时凭据有效，**不保证延长有效期或永久可用**。
切换微信账号、改变户号绑定和重新登录可能影响查询；404 本身无法区分这些原因。

认证信息保存在 HA 本地配置条目中，**不是加密保险箱**，HA 备份也可能包含它。
集成仅请求固定水务 HTTPS 地址，不上传数据至本仓库、第三方统计或作者服务器。
诊断不包含原始户号、表号、Cookie、姓名、地址、手机号或账单值。
哈希标识只是隐藏原始编号，不是不可逆匿名化承诺；请勿公开完整 HA 状态或注册表。

更多信息见 [隐私说明](PRIVACY.md) 与 [安全问题报告](SECURITY.md)。

## 开发与贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。测试使用完全虚构的凭据与业务数据，
并通过 pytest-socket 禁止真实网络请求。提交问题前请先阅读 [故障排查](docs/troubleshooting.md)，
仅提供版本、静态错误码和最小脱敏描述。

- [认证教程](docs/authentication.md)
- [实现与接口语义](docs/design.md)
- [变更记录](CHANGELOG.md)
- [MIT 许可证](LICENSE)
