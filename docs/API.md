# 接口

`POST /api/run`：JSON中的csv文本，省略使用样例；或multipart字段file。最大5MB。

`POST /api/m1`至`POST /api/m8`：独立运行到指定模块，JSON结构同运行接口。

`POST /api/run` 与 `POST /api/m8` 支持可选 `order_kg`（订单千克，默认2400）与 `batch`（炉次）；两者进入运行清单参数，改变订单会得到新的批次。

`POST /api/requisition`：接收 `id`（批次）与 `option`（cheap/balanced/tight），按订单幂等保存唯一领料记录；相同方案重复确认返回 `reused=true`，方案变化则原地更新并递增 `revision`，不新增第二条。`GET /api/requisition/<order_id>?run_id=` 读回记录并按当前批次体检（`current`/`same`/`stale`/`invalid`）；`GET /api/requisitions` 列出全部领料记录。验收页按已确认用量重算。

`GET /api/history`：历史批次。`POST /api/review`接收id、revision和decision（通过或退回）。`GET /api/evidence.csv`提供单一证据导出。

结构错误422，版本变化409，文件过大413；错误返回code与message。所有页面可从左侧七个模块入口访问。

`GET /api/runs/<id>`读取指定批次；页面与证据CSV通过run_id查询参数绑定批次，模块导航保留该参数。证据CSV在已确认领料方案时额外包含“领料方案”阶段行。

JSON请求体必须是对象，数组、标量、null、损坏JSON及错误上传字段返回400，且不运行默认样例。
