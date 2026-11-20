# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次；重跑同一内容返回同一批次，不重复占道。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。M5 另含 plans（分段方案）与未采用方案。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；失效批次409 `SUPERSEDED`；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV，含运行摘要、逐段段速/最低潮位/富余、采用与未采用方案、隔离记录。`GET /health`：存活检查。

`GET /api/sample`：默认分段样例；`GET /api/sample?legacy=1`：旧口径样例。

错误：结构/数值422，版本冲突/失效409，不存在404，文件过大413。逐行异常进入quarantine。
