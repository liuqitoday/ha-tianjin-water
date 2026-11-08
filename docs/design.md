# 设计与接口语义

## Home Assistant 生命周期

- UI config flow 验证余额、去重，并支持原条目 reauth / reconfigure。
- `ConfigEntry.runtime_data` 保存一个异步 `DataUpdateCoordinator`，所有实体共用数据。
- 账户内串行查询，手动更新有 60 秒最小间隔；HTTP 总超时 20 秒、响应上限 2 MiB。
- 每个账户使用 HA 的连接池与独立 CookieJar，禁止跳转，不自动采用 Set-Cookie。
- HTTP/业务认证拒绝使用 `ConfigEntryAuthFailed`，异常停止使用 `ConfigEntryError`。
- 停止原因持久化，未知响应与连续三次网络失败也停止，需要用户明确重新配置恢复。
- 不可用数据不转换为零；旧表保留实体身份，失去关联时显示不可用。
- 历史与采集时间/计数使用 `_unrecorded_attributes`，避免重复写入 Recorder。

## 仅使用三个只读接口

后台前缀：`https://www.twwc.net.cn/water-supply-front`。
认证为请求 Cookie 的 SESSION，正文为 URL 编码表单。

| 接口 | `customId` 的含义 | 用途 |
| --- | --- | --- |
| `POST /waterPayment/customBill` | 缴费户号 | 余额与应缴 |
| `POST /waterService/customBillMeter` | 缴费户号 | 账号下水表列表 |
| `POST /waterService/getUseWater` | 水表注册号 | 当前账号返回的 NB 表历史 |

不会调用支付、充值、绑定、解绑、短信或资料修改接口。
没有公开官方 API 契约，字段及认证行为可能变化。404 和新的匿名 SESSION 不等同于登录过期或续期成功。

## 数值与请求量

金额和水量使用 Decimal，日期显式解析，拒绝非有限数值和相同日期的冲突记录。
`waterIndex` 是累计指数，`waterConsumption` 是周期增量，`waterRate` 是周期费用。
不从 HTML 备注推算实时/日用量，不给缺失月份补零，不回填 Recorder。

每户每日通常 144 次余额请求，加 4 次水表列表及每表历史：
对于 N 块受支持的表，约 `144 + 4 × (1 + N)` 次/天，另有初始化与配置验证。
余额 10 分钟、历史 6 小时的周期由集成统一设置。没有额外保活定时器或第三方上报。

## 参考

- [统一获取数据](https://developers.home-assistant.io/docs/integration_fetching_data/)
- [配置流程](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)
- [运行时数据](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/runtime-data/)
- [设置失败与重新认证](https://developers.home-assistant.io/docs/integration_setup_failures/)
- [传感器](https://developers.home-assistant.io/docs/core/entity/sensor/)
- [诊断](https://developers.home-assistant.io/docs/core/platform/diagnostics/)
- [Manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)

这些链接用于开发说明，集成不会在运行时访问它们。
