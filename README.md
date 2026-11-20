# 港口潮窗水深与进港排程

引航计划数据员使用本工具整合潮位和船舶计划，在吃水安全余量限制下寻找进港窗口。

## 核心计算

外航道与内航道各有海图水深和限速；船体下沉随航速平方增长。逐段按“船位于该段那段时间里的最低潮位”核算：
可用水深 = 段海图水深 + 段内最低潮位；需求 = 吃水 + 下沉 + 1m 余量。每段取限速内仍满足富余 ≥ 0 的最大航速，
起航取申请窗、引航窗、封航和已占道下的最早可行时刻。整程一刀切和只看进段潮位的做法都会偏离，均作为未采用方案留档。

航道测深更新后，同批航次旧方案的富余立刻作废并释放占道；同一内容重跑复用同一批次，不重复占道。

## 启动

```bash
docker compose up --build -d
docker compose exec -T app pytest -q
docker compose config --quiet
```

浏览器访问 http://localhost:18211 。默认载入分段样例 `sample_data/berth.csv`（外/内航道、限速、测深版本）。工具条可选择CSV文件运行；旧口径样例为 `sample_data/instrument.csv`。重复执行同一内容复用同一批次。

## 模块

- M1 潮位接收：`/module/1`，独立计算 `POST /api/1`。
- M2 船舶条件：`/module/2`，独立计算 `POST /api/2`。
- M3 水深求窗：`/module/3`，独立计算 `POST /api/3`。
- M4 航道冲突：`/module/4`，独立计算 `POST /api/4`。
- M5 引航排程：`/module/5`，独立计算 `POST /api/5`；输出分段方案、段速、富余与未采用方案。
- M6 计划对账：`/module/6`，独立计算 `POST /api/6`。
- M7 变更复核：`/module/7`，独立计算 `POST /api/7`。

## 输入与输出

CSV可用UTF-8或GB18030；上传保留字符串编号和物理行号。POST `/api/run` 接收 `text` 字段。`GET /api/runs` 查询历史，`POST /api/review` 接收 id、revision、decision。导出按钮下载唯一证据CSV，含运行摘要、逐段段速/最低潮位/富余、采用与未采用方案、隔离记录。

SQLite命名卷保存运行记录。重建演示数据可更换卷名后重启。无需外部网络数据。规则定义见 `docs/DATA-DICTIONARY.md`；边界和测试对应关系见 `docs/DEPTH.md`。

## 范围

只处理仓内CSV定义，不连接生产系统。单次输入最多2MB。原始输入流式解析，业务计算阶段需容纳该文件的采信行。并非无限容量任务。
