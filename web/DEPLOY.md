# 统计后台部署

## 启动

Ubuntu 24.04 自带的 Python 即可运行：

```bash
cd ~/svip-timer-android/web
export ADMIN_TOKEN='请换成随机长密码'
python3 app.py
```

默认监听 `0.0.0.0:8080`。生产环境建议使用 HTTPS 反向代理，并把 Android 工程中
`ANALYTICS_BASE` 改成自己的 HTTPS 域名后重新构建。

## 数据

- SQLite 文件：`web/data/analytics.sqlite3`
- 汇总接口：`GET /api/analytics/summary`
- 反馈接口：`GET /api/analytics/feedback`，请求头需要 `Authorization: Bearer ADMIN_TOKEN`
- 下载地址：`GET /download?from=渠道名`

数据库和用户 HAR 均不会提交到 GitHub。

## 指标口径

- 下载：下载接口被成功访问的次数；
- 累计安装：同一匿名安装编号只计算一次首次启动；
- 今日/近30天活跃：时间范围内产生匿名事件的去重安装编号；
- 成功记录：客户端主动上报的 `claim_success` 次数；
- 卸载重装会生成新的匿名安装编号，因此安装人数属于近似值。
