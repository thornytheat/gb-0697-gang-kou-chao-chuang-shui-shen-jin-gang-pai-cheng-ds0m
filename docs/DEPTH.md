# 深度登记 港口潮窗水深与进港排程

- 规模：中型
- 核心规则：按航段各自的最低潮位与限速核算，逐段选速，取最早可行起航；段内富余与未采用方案可核对

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 潮位接收 | 运行潮位接收并核对证据 | `POST /api/1` | `app/modules/m1_tides/rules.py` | `tests/test_tides.py` |
| M2 船舶条件 | 运行船舶条件并核对证据 | `POST /api/2` | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py` |
| M3 水深求窗 | 运行水深求窗并核对证据 | `POST /api/3` | `app/modules/m3_windows/rules.py` | `tests/test_windows.py` |
| M4 航道冲突 | 运行航道冲突并核对证据 | `POST /api/4` | `app/modules/m4_channel/rules.py` | `tests/test_channel.py` |
| M5 引航排程 | 运行引航排程并核对证据 | `POST /api/5` | `app/modules/m5_schedule/rules.py`、`app/plan.py` | `tests/test_schedule.py`、`tests/test_plan.py` |
| M6 计划对账 | 运行计划对账并核对证据 | `POST /api/6` | `app/modules/m6_balance/rules.py` | `tests/test_balance.py` |
| M7 变更复核 | 运行变更复核并核对证据 | `POST /api/7` | `app/modules/m7_changes/rules.py` | `tests/test_changes.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | BOM | 静默采信错误值 | 消除BOM | `app/modules/m1_tides/rules.py` | `tests/test_tides.py::test_tides_bom` |
| TRAP-06 | 全角单位 | 静默采信错误值 | NFKC归一 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_unit` |
| TRAP-07 | 空值 | 静默采信错误值 | 隔离空值 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_missing` |
| TRAP-11 | 偏移混用 | 静默采信错误值 | 转换UTC秒 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_timezone` |
| TRAP-13 | 乱序潮位 | 静默采信错误值 | 先排序后插值 | `app/modules/m3_windows/rules.py` | `tests/test_windows.py::test_window_sample` |
| TRAP-14 | 同版冲突 | 静默采信错误值 | 隔离冲突 | `app/modules/m1_tides/rules.py` | `tests/test_tides.py::test_tides_duplicate` |
| TRAP-23 | 相邻端点 | 静默采信错误值 | 半开区间 | `app/modules/m4_channel/rules.py` | `tests/test_channel.py::test_channel_end` |
| TRAP-25 | 旧修订 | 静默采信错误值 | 拒绝陈旧版本 | `app/modules/m7_changes/rules.py` | `tests/test_changes.py::test_change_stale` |
| TRAP-31 | 整程一刀切 | 用最浅段+最大下沉量核算，错过潮窗 | 逐段按段内最低潮位核算，保守口径留档 | `app/plan.py` | `tests/test_plan.py::test_conservative_route_kept_as_alternative` |
| TRAP-32 | 只看进段潮位 | 落潮段段末余量不足仍放行 | 段内最低点复核，误放方案留档 | `app/plan.py` | `tests/test_plan.py::test_naive_entry_only_rejected` |
| TRAP-33 | 高限速即全速 | 外航道全速下沉超出水深 | 富余约束下取最大可行段速 | `app/plan.py` | `tests/test_plan.py::test_outer_leg_faster_than_inner` |
| TRAP-34 | 测深更新后旧富余 | 旧方案仍被当作有效 | 旧批次标记失效、释放占道 | `app/store.py` | `tests/test_plan.py::test_depth_update_invalidates_old_surplus` |
| TRAP-35 | 重跑重复占道 | 同一批航次多次占道 | 按批覆盖登记、内容摘要幂等 | `app/store.py` | `tests/test_plan.py::test_replan_same_batch_single_reservation` |

## 对账规则

输入记录=采信记录+隔离记录+空行；重复记录归隔离；算法输出另记业务单位，不冒充输入行数。时长使用Decimal；证据含原始行号、逐段段速与富余。

## 已知不覆盖

不支持超过2MB输入，不执行生产自动放行；异常值需人员复核。分段方案按段内最低潮位核算，不做潮位预报与航速动态优化。
