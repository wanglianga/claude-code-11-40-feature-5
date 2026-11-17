# 自然保护区季节调查完整性与覆盖数据服务

面向自然保护区研究人员的**覆盖、调查有效性与公开分布数据服务**：按「站点—样区—日期」组装调查单元，
逐项判断设备可用性、时间可信度、空间覆盖与素材判读是否满足规则册门槛，签发
**有效调查 / 受限调查 / 未有效调查 / 待核**证书，并并列展示经历史校准的检测概率估计。

在此之上提供**空间泛化与调查统计交付**：按接收范围与物种敏感级别配置最低空间尺度与最低候选生境面积，
在固定基础网格上按真实生境连通关系合并、仅在受保护区域降低精度，把观察、有效设备时段与路线覆盖重新汇入
最终发布分区，内部可逐项对账，对外地图与下载文件只能拿到获准的空间粒度。

> 服务只交付覆盖、有效性与分布数据。它**不派发巡护任务、不预约人员器材、不创建执法/捕捉/资源调度动作**，
> 也不把调查缺口转成行动指令，不生成巡护路线、捕捉建议或保护行动。

---

## 1. 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3.5 + TypeScript + Vue Router + Vite 7 |
| 后端 | FastAPI 0.163 + SQLAlchemy 2.1 + Shapely 2.2 |
| 数据库 | PostgreSQL 18（JSONB 存原始载荷 / 门槛快照 / 证书结论） |
| 测试 | pytest（26 个用例：7 个验收场景、修订/回溯、空间发布 11 项）；Vitest（8 个前端用例） |

## 2. 一键启动（Docker Compose）

```bash
cd <仓库根目录>
docker compose up --build
```

启动后：

- 前端（内部工作台）：<http://localhost:8080>
- 公开分布视图（无需登录）：<http://localhost:8080/public-map>
- 后端 OpenAPI 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/api/health>

首次启动时后端容器会等待 PostgreSQL 就绪，自动建表并写入演示种子数据：春季与秋季各 1 个运行、
10 个站点、30 个春季调查单元、715 条档案记录，以及两季的公开分布交付
（生境 / 适生条件 / 敏感级别 / 接收范围样本文件已加载）。

停止：`docker compose down`；连同数据卷清理：`docker compose down -v`。

## 3. 本地开发（不使用 Docker）

运行时已在容器内预装（Python 3.12 / Node 22 / PostgreSQL 18），也可用本机同版本。

### 3.1 数据库

```bash
# 数据库账号 ccdev / ccdev，端口 5432
createdb -h 127.0.0.1 -U ccdev survey_integrity        # 开发库
createdb -h 127.0.0.1 -U ccdev survey_integrity_test   # 测试库（pytest 使用）
```

### 3.2 后端

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity'
.venv/bin/python -m app.seed                # 建表 + 写入演示数据（可重复执行，已有数据则跳过）
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- API 根路径：`http://127.0.0.1:8000/api`
- Swagger UI：`http://127.0.0.1:8000/docs`
- OpenAPI JSON：`http://127.0.0.1:8000/openapi.json`

> 本项目为全栈应用（含业务前端页面），按任务约束同时提供可运行的 OpenAPI 3.x 文档与 Swagger UI，
> 便于接口对接与验收。

### 3.3 前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 ，/api 代理到 http://127.0.0.1:8000
```

生产构建：`npm run build`（产物在 `frontend/dist`，可用任意静态服务器托管，`/api` 反代到后端）。

### 3.4 测试

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test' \
  .venv/bin/python -m pytest -q                 # 26 passed

cd ../frontend
npx vitest run                                  # 8 passed（地图几何 + 公开视图）
npx vue-tsc --noEmit                            # 0 error
npm run build                                  # 构建成功
```

测试会重建测试库的 `public` schema 并写入种子数据，**不会影响开发库**。
空间发布测试在独立 schema 上运行，因为提级/修订会改变激活的数据集版本。

### 3.5 公开交付的实地核对脚本

后端启动后，用真实公开接口校验地图几何、字段泄露、粒度下限与 CSV 一致性：

```bash
cd frontend
node scripts/check-map.mjs http://127.0.0.1:8000
```

### 3.6 完整发布演示（提级 + 修订生境 + 重新检查 + 共同范围比较）

种子只做一次基线发布；完整体验一次「发布 → 提级 → 修订生境 → 重检 → 比较」请运行：

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity' \
  PYTHONPATH=. .venv/bin/python scripts/spatial_demo.py
```

脚本会：春季/秋季基线发布 → 打印共同范围比较 → 激活敏感级别 v2（critical）与生境 v2 →
把旧发布标记为过期 → 重新检查并重新生成交付 → 再次比较 → 扫描公开载荷字段。

## 4. 测试账号

| 账号 | 密码 | 角色 | 权限 |
| --- | --- | --- | --- |
| `yq.li` | `Survey#2026` | 研究人员 researcher | 组装运行、登记/撤回趋势引用、修订记录与判读、处置冲突、重新签发证书；创建空间发布、激活数据集版本、生成/撤回交付 |
| `m.cao` | `Audit#2026` | 审核员 auditor | 同上（用于复核类操作与审计） |
| `l.lin` | `View#2026` | 只读访客 viewer | 只读查看所有数据 |

## 5. 业务模型与判定口径

### 5.1 调查单元

覆盖组装器按 `站点 × 样区 × 调查日`（规则册 `schedule.survey_dates`）生成调查单元。
每个单元固定引用：站点版本、规则册版本、概率模型版本，并把四类门槛的判定与证据记录 id 一并留存。

### 5.2 四类门槛

| 门槛 | 判定要点 | 典型结论 |
| --- | --- | --- |
| `device` 设备可用性 | 心跳间隔、连续图像帧序列（`frame_seq` 跳号）、停机日志覆盖；未解释缺口 → **待核** | 帧缺失且停机日志未覆盖 → 设备待核 |
| `time` 时间可信度 | 设备时间与可信源偏差 vs 容差（120s）；**存在可验证校时关系**才可回算；迟到送达只记录不改事件时间 | 偏移超容差且无校时 → 未满足 |
| `space` 空间覆盖 | 巡护轨迹对规则册路线段的覆盖、进入缓冲区深度、**定位精度半径 vs 缓冲区宽度**；按事件时间的站点版本取边界 | 仅擦过缓冲边缘且精度 > 缓冲宽度 → 空间待核 |
| `reading` 素材判读 | 达置信门槛的判读数、待判读占比、互斥判读冲突 | 互斥且均低于门槛 → 保留冲突、判读待核 |

### 5.3 证书状态与用途

| 状态 | 含义 | 正式未发现结论 | 趋势输入 |
| --- | --- | --- | --- |
| 有效调查 `valid` | 四类门槛均通过且无未决冲突 | ✅ | ✅ |
| 受限调查 `restricted` | 主要门槛通过但存在受限项 | ❌ | ✅ |
| 未有效调查 `invalid` | 存在未满足门槛 | ❌ | ❌ |
| 待核 `pending` | 存在待核实证据 | ❌ | ❌ |
| 复审中 `under_review` | 判读修订改变出现判定后签发的复审版本 | ❌ | ❌（暂停新引用） |

证书快照（门槛判定 + 结论）在签发时深拷贝保存；后续重算不会改写已签发证书。
复审会**新增一个证书版本**并把旧证书标记为被替代，旧运行仍可看到原证书原貌。

### 5.4 规则证书 vs 概率估计

- **规则证书**：逐项门槛，可向监管人员解释；接近门槛的部分路线会被排除，样本更少。
- **概率估计**：`DET-LOGIT` 检测概率模型（logistic，特征为设备可用比例、路线覆盖、达门槛判读数、待判读比例、
  缓冲区内素材比例、精度是否可信），时间可信与无冲突是**门控特征**（不满足则覆盖值为 0）。
  结果带 80% 敏感区间、校准集切换（pivot 偏移）与阈值上浮敏感性。
- 概率结果**仅用于趋势研究**，界面与接口均标注「模型推算，不是已确认的实地覆盖」，
  接口 `purpose=trend_only`，不会写入证书用途。

### 5.5 七个验收场景（种子数据）

| 站点 | 场景 | 期望结果 |
| --- | --- | --- |
| `QSL-01` 青松岭主监测站 | 完整调查 + 可验证校时 + 迟到送达 | 有效调查，可用于正式未发现结论 |
| `QSL-02` 青松岭北梁相机站 | 有心跳但帧连续缺失，停机日志未覆盖 | 设备可用性**待核**，不判未出现 |
| `QSL-03` 青松岭西沟站 | 两来源互斥判读且均低于 0.70 | 判读**待核**，保留冲突 |
| `QSL-04` 青松岭东南站 | 待判读占比过高（薄判读） | 判读待核；概率估计偏低 |
| `YXG-01` 云溪沟西站 | 轨迹只擦过缓冲边缘，精度 40m > 缓冲 30m | 空间**待核** |
| `YXG-02` 云溪沟东站 | 迟到判读修订：未出现 → 出现 | 当前证书**复审中**，旧引用暂停，旧证书保留 |
| `YXG-03` 云溪沟下坝站 | 路线覆盖 60%（接近门槛 50%/目标 80%） | 受限调查；概率估计仍过阈值 → 样本取舍分歧 |
| `BHP-01` 北坡高寒相机站 | 设备时钟偏移 900s 且无可验证校时 | 时间**未满足** → 未有效调查 |
| `BHP-02` 北坡垭口迁移站 | 4/11 迁移边界与路线段变更 | 4/11 用 v1、4/12 用 v2，各自判定 |

第 10 个站点 `QSL-05`（青松岭河岸辅站）用于空间发布样例：它坐落在狭窄溪岸带上，秋季有一天计划外停机，
并有跨发布分区定位误差的素材。

### 5.6 空间发布样例（种子数据）

| 对象 | 场景 | 期望结果 |
| --- | --- | --- |
| 溪岸带（窄，约 11–13 ha） | 大坡面大部分为山地，只有沿河窄带适生 | 按相邻网格合并后宽度达标 → 公开；密集林斑等更窄处仍不公开 |
| 河湾小片适生带（0.49 ha） | 面积与宽度都不达标，向外扩粗会吸收无关地形 | **不公开**，记录 `CANDIDATE_HABITAT_BELOW_MINIMUM` + `REGION_TOO_NARROW` + `SURVEY_EFFORT_UNKNOWN` |
| 秋季转载素材 `AUT-IMG-QSL-01-0002-REPOST` | 合作机构渠道重复提交同一图片 | **按素材排重**（1 条 `duplicate`），只计一次 |
| 秋季跨界素材 `AUT-IMG-BOUNDARY-0001` | 位置落在两块分区之间，误差半径 350 m 同时触及两块 | **未归属**（1 条），保留计数、不摊成多条 |
| 秋季 `QSL-05` 部分停机 | 设备仅 22 h 可用，且与其他单元的同日时段重叠 | 有效时段**先合并再汇入**：内部 139 h → 按重叠区间合并后 72 h → 公开分区合计 72 h |
| 秋季云溪沟溪岸带（64 ha） | 秋季种子只在青松岭布设了设备时段，该区域没有有效单元 | 调查强度未知 → **不公开为未出现** |
| 提级为 `critical` + 生境 v2 | 溪岸带被切分、新增林内适生斑 | 旧发布标记为 `stale`；重新检查后按 500 m 粒度发布；`PARTNER` 范围整体停止公开 |
| 两季共同范围 | 溪岸带混交林区在两季都公开 | 1 个共同 `extent_key` 逐块比较；仅一季存在的区域单独列示、不算涨跌 |

## 6. 验证方式（均已实际执行）

### 6.1 自动化测试

```bash
cd backend
DATABASE_URL='postgresql+psycopg://ccdev:ccdev@127.0.0.1:5432/survey_integrity_test' \
  .venv/bin/python -m pytest -q
# 26 passed
```
覆盖：

- `tests/test_scenarios.py`：7 个验收场景 + 校时放行 + 引用用途限制 + 回溯链完整性 + 无派工动作断言
- `tests/test_revisions.py`：判读修订触发复审与新证书版本、档案记录修订保留哈希链、
  单元证据全链可回溯、重算运行不改变旧证书
- `tests/test_spatial.py`（11 项）：公开生境推导与狭窄河岸不公开、观察/努力量/路线逐项对账、
  共同范围比较与分开展示、公开载荷无精确标识、公开接口拒绝细粒度筛选、CSV 下载与撤回后账本保留、
  提级 + 修订生境触发过期与重检、过期发布禁止交付、接收范围不允许极高敏感级别时停止公开、
  内部入口与公开入口分离、无派工动作

### 6.2 前端构建、类型检查与组件测试

```bash
cd frontend
npx vitest run           # 8 passed（地图几何 6 + 公开视图 2）
npx vue-tsc --noEmit     # 0 error
npm run build            # 构建成功
```

Vitest 用与页面相同的地图几何模块（`src/mapGeometry.ts`）验证：MultiPolygon 展开、边界框与视口、
SVG 路径生成（纬度翻转）、坐标数值化、敏感级别配色，以及公开视图只调用 `/api/public/*`、
渲染分区、生成 CSV 下载链接、显示未归属数量与未公开原因且不含 `latitude` / `station_code`。

### 6.3 公开交付实地核对（已实际执行）

`node scripts/check-map.mjs` 对运行中的后端逐条校验：

```text
ok PUB-2026-AUTUMN-PUBLIC-LOPHOP-001: 1 regions, granularity 500 m, 2 withheld, CSV 1 rows
ok PUB-2026-SPRING-PUBLIC-LOPHOP-001: 2 regions, granularity 500 m, 1 withheld, CSV 2 rows
checked 2 public deliveries
```

断言：地图特征非退化、未公开区域不带几何、公开粒度不低于敏感级别下限、不同分区的公开块不重叠、
公开载荷不含 `latitude` / `longitude` / `s3://` / `record_id` / `external_id` / `station_code`、
CSV 行数与观察数与地图统计逐项一致。

> 前端页面已用 vitest 组件测试覆盖。本容器缺少 Chromium 运行所需的系统库且没有 root 权限，
> 因此**未**执行真实浏览器截图流程；这一点如实记录，不以“已验证”代替。

### 6.4 API 冒烟

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"yq.li","password":"Survey#2026"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s http://127.0.0.1:8000/api/runs -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/runs/1/overview -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/units/1/trace -H "Authorization: Bearer $TOKEN"

# 空间发布：内部列表 / 逐项对账
curl -s http://127.0.0.1:8000/api/spatial/publications -H "Authorization: Bearer $TOKEN"
curl -s http://127.0.0.1:8000/api/spatial/publications/1/reconciliation -H "Authorization: Bearer $TOKEN"

# 公开接口（无需登录）：地图 + 统计、下载、细粒度参数被拒绝
curl -s http://127.0.0.1:8000/api/public/publications
curl -s http://127.0.0.1:8000/api/public/publications/PUB-2026-SPRING-PUBLIC-LOPHOP-001/map
curl -s -o /tmp/survey.csv -w '%{http_code}\n' \
  http://127.0.0.1:8000/api/public/publications/PUB-2026-SPRING-PUBLIC-LOPHOP-001/download.csv
curl -s -w '\n%{http_code}\n' \
  'http://127.0.0.1:8000/api/public/publications/PUB-2026-SPRING-PUBLIC-LOPHOP-001/map?bbox=103.6,31.0,103.7,31.1'
# 400：公开查询不接受细粒度筛选参数 bbox
```

## 7. 界面

| 页面 | 说明 |
| --- | --- |
| 站点覆盖总览 | 站点 × 调查日矩阵、门槛分布、需关注单元、最近数据活动 |
| 单元详情 | 规则证书 vs 概率估计并列；四门槛证据；原始证据表；证书历史；审计；引用登记 |
| 规则证书 vs 概率估计 | 逐单元并列对照、分歧分类、阈值敏感性、单单元敏感度复算 |
| 趋势输入清单 | 按用途/状态筛选引用，显示引用时证书快照与概率区间 |
| 单元回溯链 | 原始事件 → 覆盖条件 → 规则/模型/站点版本 → 证书 → 趋势引用 |
| 观测档案 | 类型/站点/编号检索，原始载荷、哈希、修订历史、载荷修订 |
| 判读冲突 | 未决冲突的来源、置信度、处置记录 |
| 样区规则册与模型 | 规则册四类门槛、路线段、海拔带；模型校准集、权重、阈值复核 |
| 空间泛化工作台（`/spatial`） | 发布批次与检查清单、公开粒度地图、未公开区域及原因、分区明细（连通键/生境面积/宽度/基础单元/观察/时段/路线）、对外材料账本与撤回、激活敏感级别/生境/适生条件版本 |
| 共同范围比较（`/spatial/compare`） | 仅按共同 extent_key 逐块比较；无共同范围单独列示、不计算涨跌 |
| 公开分布视图（`/public-map`，免登录） | 只读公开发布分区地图与分区统计，可下载 CSV；显示未归属观察数与未公开区域原因码 |

## 8. 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/login` | 登录，返回 HMAC 签名令牌 |
| GET | `/api/runs` | 运行列表（含状态计数、未决冲突数） |
| POST | `/api/runs` | 按规则册/模型/站点组装新运行 |
| GET | `/api/runs/{id}/overview` | 总览：矩阵、门槛分布、对照行、活动 |
| GET | `/api/runs/{id}/units` | 单元列表（可按站点/证书状态/门槛过滤） |
| GET | `/api/units/{id}` | 单元详情：门槛、证书、概率、证据、冲突、审计 |
| GET | `/api/units/{id}/trace` | 可回查的五段回溯链 |
| GET | `/api/units/{id}/records` | 单元引用的原始记录（含修订） |
| POST | `/api/units/{id}/trend-references` | 登记 `absence` / `trend` 引用（含用途校验） |
| POST | `/api/units/{id}/certificates/reissue` | 复审后重新签发证书 |
| GET/POST | `/api/records`、`/api/records/{id}/revisions` | 档案检索、载荷修订（哈希 + 修订号） |
| POST | `/api/determinations`、`/api/determinations/{id}/revisions` | 判读新增/修订（触发复审） |
| GET/POST | `/api/conflicts`、`/api/conflicts/{id}/resolve` | 冲突列表与处置 |
| GET/POST | `/api/trend-references`、`/api/trend-references/{id}/revoke` | 引用列表与撤回 |
| POST | `/api/probability/models/{id}/evaluate` | 概率复算（可选校准集偏移，只读不签署） |
| GET | `/api/probability/models/{id}/threshold-sensitivity` | 阈值敏感性 |
| GET | `/api/rulebooks`、`/api/models`、`/api/stations`、`/api/zones`、`/api/audit` | 规则册、模型、站点、样区、审计 |

### 8.1 空间发布（内部，需登录）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/spatial/datasets` | 样本文件数据集版本、接收范围阈值、敏感级别基线 |
| POST | `/api/spatial/datasets/{habitat\|suitability\|sensitivity\|scopes}/activate` | 激活新版本，并把使用旧版本的发布与材料标记为过期 |
| GET | `/api/spatial/publications` | 发布批次列表（状态、检查结果、公开/未公开区域数） |
| POST | `/api/spatial/publications` | 按物种 + 接收范围 + 调查运行计算分区、统计并执行检查 |
| GET | `/api/spatial/publications/{id}` | 分区（几何、基础单元、连通键、未公开原因、阈值快照）与全部检查 |
| GET | `/api/spatial/publications/{id}/reconciliation` | 观察/有效时段/路线逐条归属与合计对账 |
| POST | `/api/spatial/publications/{id}/publish` | 仅对通过检查的发布生成地图与 CSV；过期/停止的发布返回 409 |
| GET | `/api/spatial/materials` | 对外材料账本（含已撤回、已过期、被取代） |
| POST | `/api/spatial/materials/{code}/withdraw` | 撤回材料；账本保留其范围，后续发布仍需覆盖 |
| GET | `/api/spatial/compare?first=&second=` | 共同范围逐块比较 + 无共同范围分开展示 |

### 8.2 公开交付（无需登录，只读）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/public/publications` | 当前对外提供与已被新版本取代的发布批次 |
| GET | `/api/public/publications/{code}/map` | 合并后发布分区 GeoJSON + 分区统计 + 未公开区域原因码 |
| GET | `/api/public/publications/{code}/stats` | 仅分区统计 |
| GET | `/api/public/publications/{code}/download.csv` | 与地图统计一一对应的 CSV |
| GET | `/api/public/materials/{code}` | 按材料编号读取（撤回返回 410） |

公开接口只接受发布编号：任何 `bbox`、`lat`/`lon`、`cell`、`station_id`、`record_id`、`route` 等
细粒度参数都会返回 400，不能用筛选绕过发布边界。

## 9. 目录结构

```
backend/
  app/
    main.py            # FastAPI 应用与路由装配
    models.py          # 18 张表（档案、修订、判读、规则册、单元、门槛、证书、冲突、模型、引用、审计）
    coverage.py        # 覆盖组装器：四类门槛判定与证据留存
    certificates.py    # 有效性裁定：状态、用途、复审版本
    probability.py     # 检测概率模型（含门控特征与敏感区间）
    services.py        # 组装运行、记录/判读修订、引用、冲突处置、重签发
    spatial_datasets.py# 样本文件数据集（生境/适生条件/敏感级别/接收范围）与敏感级别下限
    spatial.py         # 基础网格、连通合并、阈值检查、观察/时段/路线归属、发布检查
    spatial_delivery.py# 交付生成（地图/CSV）、材料账本、过期失效、共同范围比较
    seed.py            # 确定性种子数据（7 个验收场景 + 秋季运行 + 基线发布）
    routers/           # auth / catalog / surveys / records / probability / spatial
  sample_data/         # 公开生境、巢区适生条件、敏感级别、接收范围（合成样本文件）
  scripts/spatial_demo.py  # 发布 → 提级 → 修订生境 → 重检 → 比较 的完整演示
  tests/               # 26 个 pytest 用例
frontend/
  src/views/           # 11 个页面（含空间工作台、共同范围比较、公开分布视图）
  src/components/      # 状态徽章、概率区间条、提示、发布分区 SVG 地图
  src/mapGeometry.ts   # 地图几何（纯函数，被组件与校验脚本共用）
  scripts/check-map.mjs# 对运行中后端的公开交付核对脚本
  tests/               # 8 个 Vitest 用例
docker-compose.yml     # db + backend + frontend 一键启动
```

## 10. 数据与合规说明

- 数据库中的 `ccdev` 账号仅用于隔离开发/测试，不作为生产配置。
- 所有时间以保护区当地时区（UTC+8）解释、UTC 存储；时间校准关系仅在有 `verified: true` 且落在
  有效区间内时才用于回算事件时间。
- 服务端不包含任何派工、预约、执法、捕捉或资源调度接口；测试 `test_service_emits_no_dispatch_actions`
  会校验 OpenAPI 路径与总览响应中不出现相关动作。

### 10.1 空间发布口径

- **输入全部来自样本文件**：`backend/sample_data/habitat_v*.json`（公开生境边界）、`suitability_v*.json`
  （巢区适生条件）、`sensitivity_v*.json`（物种敏感级别）、`scopes_v1.json`（接收范围阈值）。
  系统不连接真实鸟巢、巡护点或外部地图平台。
- **候选范围由公开生境 + 适生条件计算**：只取物种适生类别与海拔重叠的公开生境图斑，不用秘密巢点反推，
  也不以包围盒面积代替真实生境面积。
- **固定基础网格 + 相邻合并**：100 m 基础网格按真实生境连通关系合并；只有在宽度或面积不达标时才向外扩粗，
  且绝不吸收其他连通生境，因此不会跨无关山脊连接两块孤立河岸来“凑面积”。每块发布分区保留其基础单元
  （`candidate_cells`），供跨季归并到共同范围。
- **门槛 = 敏感级别下限与接收范围阈值的较大者**：例如公众（`PUBLIC`）对 `high` 级别为
  最小粒度 500 m、最小候选生境 15 ha、最小宽度 800 m；提级到 `critical` 后为 500 m / 10 ha / 600 m，
  而合作机构（`PARTNER`）不允许公开 `critical`，该物种结果整体不公开。
- **未公开区域**记录连通键、候选生境面积与原因码（`CANDIDATE_HABITAT_BELOW_MINIMUM`、
  `REGION_TOO_NARROW`、`DISPLAY_BLOCK_ABSORBED`、`SURVEY_EFFORT_UNKNOWN`、`SENSITIVITY_NOT_ALLOWED_FOR_SCOPE`）。
- **统计按最终分区重新汇入**：观察按单元证书可用性筛选后逐条归属；定位误差圆跨分区或溢出发布范围的观察
  保留为“未归属”，不平均摊成多条记录；有效设备时段先按重叠运行区间合并再汇入；巡护轨迹整条归属到覆盖最长的
  发布分区，重复上传按轨迹键排重；同一素材被转载按素材键排重。公开文件只导出分区级汇总。
- **对账可复核**：每次发布保存 13 项检查（候选来源、阈值、未公开原因、公开块不相交、观察对账、路线对账、
  分区统计一致、时段合并与合计、调查强度已知、路线仅分区汇总、公开载荷无精确标识、既有公开范围覆盖、
  至少一个可公开区域），内部 `reconciliation` 接口可逐条核对到原始记录 id 与单元 id。
- **版本变动即失效**：激活新的敏感级别/生境/适生条件/接收范围版本会把使用旧版本的发布与材料标记为 `stale`；
  过期发布不再对外提供（409）且禁止生成交付，必须重新检查，避免混用旧分区与新统计。
- **既有材料范围**：对外提供过的地图与 CSV 记录其公开几何与基础单元；撤回只停止提供，范围仍在账本中，
  新发布必须继续覆盖这些范围，否则继续扩大合并或停止公开。
- **内外部入口分离**：`/api/spatial/*` 需要登录并可能返回基础单元、原因与阈值快照；
  `/api/public/*` 只返回合并后分区、分区统计与未公开原因码。公开接口拒绝任何细粒度筛选参数。
