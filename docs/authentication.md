# 从本人成功查询中取得认证信息

本集成使用水务网站发给已登录微信用户的 `SESSION` Cookie。
不需要微信密码、短信验证码、OpenID、OAuth code 或微信的本地数据文件。
一次性登录回调 code 不能作为可长期使用的 SESSION。

## 1. 先确认能够查询

在自己手机的微信中打开平时用于查询「天津市自来水集团」的页面，
确认本人缴费户号已绑定，且能看到余额或账单。
入口菜单可能变化，以水务官方当前页面为准；不要使用第三方提供的登录链接。

本项目验证的后台是：

```text
https://www.twwc.net.cn/water-supply-front
```

如果抓到的是其他地址，此教程不能证明它兼容。电脑微信和手机微信可能使用不同会话，
应优先抓取当前能够成功查询的设备，而不是复制另一个设备上的旧 Cookie。

## 2. 抓取一次余额查询

可使用自己信任的 HTTP 调试工具，例如 iOS 的 Stream，或支持 HAR 导出的 ProxyPin。
具体按钮以所用版本为准，流程是：

1. 按工具自身说明启用 HTTPS 解密。HTTPS 内容不可见时，检查其证书安装与信任设置。
2. 开始录制，将过滤范围尽量限制为 `www.twwc.net.cn`。
3. 回到微信水务页面，打开余额/水费查询，等页面显示查询成功。
4. 停止录制，找到下面这一条 **POST 请求**，确认 HTTP 状态为 200，响应 JSON 的业务 `code` 也为 200。
5. 将此次记录导出为 HAR，保存在自己的电脑，随后关闭调试代理；不再使用时可按工具说明移除调试证书。

```text
POST https://www.twwc.net.cn/water-supply-front/waterPayment/customBill
```

这里只需要成功查询，不需要充值、缴费、发送短信或改变户号绑定。
如果工具无法解密，或页面只返回错误，先恢复官方页面正常查询，不要从错误响应猜测凭据。

## 3. 字段从哪里取

所有认证字段都来自**同一次成功请求**：

| HA 配置字段 | HAR 中的位置 | 注意 |
| --- | --- | --- |
| 缴费户号 / `custom_id` | 请求正文 `postData.text` 的 `customId`，或 `postData.params` | 是缴费户号；不要使用用量接口里的水表注册号 |
| SESSION / `session` | 请求 Headers 的 `Cookie` 中 `SESSION=…` | 取请求 Cookie，不是响应 `Set-Cookie` |
| User-Agent / `user_agent` | 请求 Headers 的 `User-Agent` | 可选，建议从同一请求复制 |

成功响应的 `data.customId` 应与请求户号完全一致，同时有有效的 `prepayBalance` 和 `totalAmount`。
这可以避免把匿名会话、错误页面或另一户的数据误当成自己的登录凭据。

**不要使用以下内容替代 SESSION：** URL 中的 `code`、微信 OpenID、其他域名 Cookie、
失败响应下发的 `Set-Cookie`。失败响应也可能创建新的匿名 SESSION，那不代表刷新成功。

## 4. 推荐：本地离线提取

从本仓库下载源码，将 HAR 留在源码目录之外。在终端运行（需要 Python 3.10+）：

```bash
python3 tools/extract_credentials.py /path/to/your-capture.har --out private/credentials.json
```

工具会：

- 只接受指定 HTTPS 域名与余额接口的 POST 请求；
- 验证 HTTP 200、业务 200、请求/响应户号相同，以及有效的余额和应缴数值；
- 从请求 Cookie 读取 SESSION，忽略响应 Set-Cookie；
- 支持 HAR 的 base64 响应和两种表单正文格式；
- 在同一户的成功记录中选择最新一条；多个户号时要求明确指定；
- 只在本地生成文件，不联网、不输出凭据、不覆盖已有文件。

若 HAR 包含多个成功查询的户号，添加 `--account` 指定本人的目标户号。
为避免户号直接出现在终端历史中，可以先交互输入：

```bash
read -r -p '缴费户号: ' water_account
python3 tools/extract_credentials.py /path/to/your-capture.har --account "$water_account" --out private/credentials.json
unset water_account
```

上面的 `read -p` 示例适用于 Bash；也可在图形界面手动核对并只导出一个户号的成功请求。
命令运行期间，`--account` 参数可能对本机具有相应权限的进程可见。

输出文件只含 `custom_id`、`session`、`user_agent`。在本机编辑器中打开，将值填入 HA 表单即可，
不需要把该文件传到 HA，也不要将它放到 `www`、GitHub、issue、聊天或截图中。
新文件使用权限 0600，目录默认 0700；Windows 上仍需依靠自己的账户与文件权限保护。
输出已存在时，选择一个新文件名或在确认后手动删除旧文件，再重新执行。

## 5. 配置、失效与更新

在 HA 的「设置 → 设备与服务 → 添加集成」中选择 **Tianjin Water**。
输入上面三个字段；配置流程会做一次只读余额验证，成功后再自动发现 NB 表。

认证失效后，集成停止请求并提供重新认证入口。重新进入官方页面，确认余额查询成功，
再按本教程获得新 SESSION。在原集成中完成重新认证或「重新配置」，避免删除后重建条目造成实体变更。
已经明确拒绝的同一 SESSION 不会再次请求；如果只是临时网络或接口异常，
「重新配置」允许在用户明确操作下验证原凭据。

认证是否会自然过期、固定期限是多少、定时请求是否延长寿命，目前均未确认。
本项目不会后台自动登录微信，也不会自动保存错误响应生成的新 Cookie。
