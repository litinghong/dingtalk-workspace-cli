# 全局参考

## CLI 可执行文件（发布约定）

为保证 skill 可离线分发且不依赖宿主机预装 `dws`，建议将 Linux amd64 二进制放在：

- `skills/dws/bin/dws`

命令解析优先级：

1. `DWS_BIN`（显式指定）
2. `skills/bin/dws`（随 skill 分发）
3. `PATH` 中的 `dws`（兜底）

示例：

```bash
if [ -n "${DWS_BIN:-}" ] && [ -x "${DWS_BIN}" ]; then
  DWS_CMD="${DWS_BIN}"
elif [ -x "skills/dws/bin/dws" ]; then
  DWS_CMD="$(pwd)/skills/dws/bin/dws"
else
  DWS_CMD="dws"
fi
```

## 认证

```bash
# 设备流两阶段（先输出授权链接与授权码，再等待授权）
"$DWS_CMD" auth login --device --device-step init
"$DWS_CMD" auth login --device --device-step wait

# 查看状态
"$DWS_CMD" auth status

# 退出
"$DWS_CMD" auth logout

# 重置本地凭证 (Token 解密失败时使用)
"$DWS_CMD" auth reset
```

登录后自动管理 token 刷新，日常使用无需重复登录。

| Token | 有效期 | 说明 |
|-------|--------|------|
| Access Token | 2 小时 | 调用 API 的凭证，过期自动刷新 |
| Refresh Token | 30 天 | 换新 Access Token，使用后轮转 |

30 天内使用一次即自动续期。

### 认证失败处理
- 任何**非 auth 子命令**返回以下登录态错误：
  - `reason=not_authenticated`
  - `message` 包含“未登录，请先执行 dws auth login”
  - `hint` 包含“运行 'dws auth login' 完成登录后重试”
  - `AUTH_TOKEN_EXPIRED` / `USER_TOKEN_ILLEGAL` / "Token验证失败"
  处理流程统一为：
  1. 先执行 `dws auth login --device --device-step init`
  2. 从输出中提取并发给用户以下信息：
     - 认证链接：`https://login.dingtalk.com/oauth2/device/verify.htm?user_code=...`（优先发送带 `user_code` 的直达链接）
     - 授权码：`XXXX-XXXX`
     - 过期时间（通常 900 秒）
  3. 提示用户完成授权后回复“已授权”
  4. 用户确认后执行 `dws auth login --device --device-step wait`
  5. wait 成功后，重试原业务命令一次
- 命令返回 `PAT_MEDIUM_RISK_NO_PERMISSION`，且 `data.desc` 提示“在浏览器中打开以下链接进行认证”：
  1. 提取并输出 `data.uri` 给用户点击授权（不要由助手代点）
  2. 同时可展示 `requiredScopes`、`grantOptions` 供用户确认授权范围
  3. 等用户回复“已授权”后，重试原命令一次

示例输出（面向用户）：

```text
检测到登录态已失效，请先完成设备授权。

请打开以下链接（已带授权码）：
https://login.dingtalk.com/oauth2/device/verify.htm?user_code=DXSR-VXJB

若页面要求手动输入，授权码是：DXSR-VXJB
该授权码将在 900 秒后过期。

完成后请回复“已授权”，我会继续下一步认证并重试刚才的操作。
```

### Headless 环境 (CI/CD)

```bash
# 通过环境变量配置认证（无需交互式登录）
export DWS_CLIENT_ID=<your-app-key>
export DWS_CLIENT_SECRET=<your-app-secret>
dws auth login

# 或使用 --device 设备流登录（远程服务器/Docker）
dws auth login --device

# 或使用两阶段设备流（适合分离终端执行）
dws auth login --device --device-step init
dws auth login --device --device-step wait
```
refresh_token 单设备独占，远程刷新后源设备凭证失效。

## Recovery

当 runtime/MCP 命令失败且 stderr 额外输出 `RECOVERY_EVENT_ID=<event_id>` 时，说明 CLI 已经持久化了失败快照，可进入 recovery 闭环：

```bash
dws recovery plan --event-id <event_id> --format json
dws recovery execute --event-id <event_id> --format json
dws recovery finalize --event-id <event_id> --outcome recovered|failed|handoff --execution-file execution.json --format json
```

- `plan` / `execute` 也支持 `--last`，但 `--last` 与 `--event-id` 互斥
- recovery 文件保存在 `DWS_CONFIG_DIR/recovery/`
- CLI 会自动清理 30 天前的 recovery 文件和事件记录
- recovery 自己发起的文档检索与只读 probe 不会再创建新的 recovery 事件

更多闭环要求见 [recovery-guide.md](./recovery-guide.md)。


## 全局标志

| 标志 | 短名 | 说明 | 默认 |
|------|:---:|------|------|
| `--format` | `-f` | 输出格式: json / table / raw | json |
| `--jq` | | jq 表达式过滤输出 (如: `.items[] \| .name`) | 无 |
| `--fields` | | 筛选输出字段 (逗号分隔, 如: name,id,status) | 无 |
| `--verbose` | `-v` | 详细日志 | false |
| `--debug` | | 调试日志 | false |
| `--yes` | `-y` | 跳过确认提示 | false |
| `--dry-run` | | 预览操作不执行 | false |
| `--timeout` | | HTTP 超时 (秒) | 30 |
| `--mock` | | Mock 数据 (开发用) | false |
| `--client-id` | | 覆盖 OAuth Client ID | 无 |
| `--client-secret` | | 覆盖 OAuth Client Secret | 无 |

## 输出格式

### --format json (机器可读, 默认)

```json
{"success": true, "body": {...}}
```

### --format table (人类可读)

```
已创建 AI 表格 "项目管理" (UUID: abc123)

下一步:
  dws aitable base get --base-id abc123
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `DWS_CONFIG_DIR` | 覆盖默认配置目录 |
| `DWS_SERVERS_URL` | 自定义服务发现端点 |
| `DWS_CLIENT_ID` | 覆盖 OAuth Client ID (DingTalk AppKey) |
| `DWS_CLIENT_SECRET` | 覆盖 OAuth Client Secret (DingTalk AppSecret) |

凭证优先级: `--token` > `DWS_CLIENT_ID`/`DWS_CLIENT_SECRET` > OAuth 加密存储 (.data)
