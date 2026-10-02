# dailycheckin-ql

青龙面板每日签到脚本合集。**纯 Python 标准库，零第三方依赖**，装完即用。

## 脚本列表

把 `scripts/` 下的文件放到青龙脚本目录（如 `/ql/data/scripts/`），**各建一个定时任务，各自独立推送通知**。

### 1. 奶昔论坛签到：`scripts/naixi_checkin.py`

奶昔论坛（forum.naixi.net）签到，Discuz k_misign 插件。

| 环境变量 | 说明 |
|---|---|
| `NAIXI_ACCOUNT` | 账密，格式：`账号1,密码1丨账号2,密码2`（多账号用 `丨` 分隔） |
| `NAIXI_COOKIE` | 浏览器 Cookie，优先级高于账密；多账号用 `&` 或换行分隔 |
| `NAIXI_UA` | User-Agent（选填，建议与抓 Cookie 的浏览器一致） |
| `NAIXI_WRITEBACK` | 设 `1` 时账密登录成功后把新 Cookie 写回青龙环境变量 |

- 登录模式：Cookie > 账密（自动降级）
- 通知：青龙 QLAPI

### 2. 烧饼论坛签到：`scripts/sb_checkin.py`

烧饼论坛（sb.sb）签到。

> 站点登录有人机验证（Cap.js PoW + 浏览器环境探针），纯脚本无法完成登录，因此采用**浏览器 Cookie 直连**方案——签到接口本身不需要过验证。

| 环境变量 | 说明 |
|---|---|
| `SB_COOKIE` | `__Host-bbs_csrf=xxx; __Host-bbs_session=yyy` |
| `SB_COOKIE_FILE` | Cookie 文件路径（青龙容器内默认 `/ql/data/config/sb_cookie`） |
| `SB_MESSAGE` | 签到留言（选填，≤100 字） |
| `SB_UA` | User-Agent（选填） |
| `SB_FORCE_IPV4` | 设 `1` 时强制走 IPv4 出口 |
| `HTTPS_PROXY` | 选填，网络不可达时自动走代理 |

- **Cookie 自动续期**：服务端滑动续期时自动把新 Cookie 写回文件，长期免维护
- **异常兜底**：403/404 封禁页、网络错误都不会崩，照常推送通知
- ⚠️ 站点对中国大陆部分出口 IP 有地域限制（返回 404 + 封禁页），部署机需网络可达；可用 `HTTPS_PROXY` 指定代理

## 青龙定时任务（各建一个，独立推送通知）

```bash
# 奶昔论坛
23 8,21 * * * task /ql/data/scripts/naixi_checkin.py

# 烧饼论坛
23 8,21 * * * task /ql/data/scripts/sb_checkin.py
```

> 两次执行是为了容错：第一次失败/漏签时，晚上那次还能补上；已签到的情况下脚本会自动跳过，不会重复签到，也不会重复推送。

## 青龙环境变量速查

| 变量 | 用途 |
|---|---|
| `NAIXI_ACCOUNT` | 奶昔账密 |
| `SB_COOKIE_FILE` | 烧饼 Cookie 文件（默认路径已内置，可不填） |
| `HTTPS_PROXY` | 全局代理（如 `http://ip:port`） |

## 拉库（青龙订阅）

青龙面板 → 订阅管理 → 新建订阅：

| 字段 | 值 |
|---|---|
| 类型 | 公开仓库 |
| 名称 | `dailycheckin-ql` |
| 链接 | `https://github.com/XTL841/dailycheckin-ql.git` |
| 分支 | `main` |
| 定时规则 | `0 3 * * *`（每天 3 点拉取，可自定义） |

> 白名单可填 `scripts`，只拉取脚本目录。

命令行方式：

```bash
ql repo https://github.com/XTL841/dailycheckin-ql.git "" "" "scripts"
```

## 免责声明

仅供学习交流，请遵守目标站点的用户协议。Cookie / 账号密码等凭据请仅配置在环境变量中，不要提交到仓库。
