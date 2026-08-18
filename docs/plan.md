# CellGenerator Studio — 前後端開發計劃

## Context

`SMTCellUCSD-2.0-main/` 是 UCSD 的 standard-cell 同步 place & route 生成器，底層是 **OR-Tools CP-SAT**（名字裡的 "SMT" 是歷史遺留）。目前只有 Makefile CLI + 一個 PySide6 桌面 GUI，而且：

- **加一條 constraint 要手改 `src/cellgen/archit/*/main.py`** — 沒有註冊/開關機制，改完無法單獨保存、分享、比較。
- **沒有任何批次或結果儲存機制**，結果散在 `output/<LIBNAME>/<HEIGHT>/`，且**兩次 run 會互相覆蓋**。
- 求解時間數分鐘到數小時（附帶的 CFET `AOI21_X2`，六顆電晶體，跑了 **403.9 秒**）。
- 沒有 `requirements.txt`/`pyproject.toml`、沒有測試、沒有 git。

目標是做網頁工具，兩個 tab：**開發區**（E2B 沙盒內跑 opencode，用自然語言開發 constraint、跑小測資、導出到 MongoDB）與**實驗區**（撒批次實驗跑 CP-SAT）。前端 TypeScript、後端 Python。

**本計劃以 Tab 1（開發區）為 MVP 優先交付。**

---

## 已確認的決策

| 項目 | 決定 |
|---|---|
| **交付順序** | **Tab 1（opencode + E2B）先做成 MVP** |
| **沙盒狀態** | 沙盒可被回收，但下次開啟必須**完整還原歷史對話與狀態**；落地的狀態檔案**越精簡越好** |
| 求解執行 | 自架 worker + Celery + Redis（非 E2B） |
| MongoDB 粒度 | Constraint plugin 化 — 存 plugin 原始碼，實驗時動態組裝 |
| Plugin 重構幅度 | 分兩階段 — 先做掛點，之後再把既有 constraint 逐步搬進 registry |
| OpenCode 嵌入 | `opencode serve`/`web`，前端一個框裝 web UI；保留未來掛 MCP tool |
| 實驗維度 | 先做單點批次執行，不做笛卡爾積 |
| 結果呈現 | 指標表格 + 版面圖比對、即時 solver log 串流、跨實驗分析圖表 |
| 規模 | 單人／小團隊內部，不做完整 auth |

---

## 狀態持久化設計（本計劃的核心）

研究結論（決定了整個設計）：

1. **E2B paused sandbox 沒有 TTL，可無限期保存**，resume 約 1 秒，檔案系統完整還原。`keepMemory: false` 可只保留磁碟、冷啟動 resume。
2. **OpenCode 提供官方的 session `export` / `import` CLI** —— 不需要動 SQLite 檔。已實測驗證（見 `docs/opencode-state-spike.md`）：從一個 data dir 匯出，匯入到**全新空白** data dir，session id 完整保留。
   - ⚠️ 釘住儲存位置的環境變數是 **`XDG_DATA_HOME`**，不是計劃原本假設的 `OPENCODE_DATA_DIR`（後者完全無效，已實測）。
3. **OpenCode session 以 project 目錄為 scope**（project hash/slug）。→ 沙盒的工作目錄路徑**必須跨重建保持完全一致**（固定為 `/workspace/engine`），否則還原後找不到舊 session。

因此採用**兩層**設計：

### 第一層：Pause / Resume（日常路徑，狀態 = 一個字串）

使用者關掉分頁 → 背景 `sandbox.pause(keepMemory=False)`。再打開 → `resume()`，opencode 的 SQLite 與工作目錄原封不動。

**Mongo 只存 `sandbox_id`。** 這就是最精簡的落地狀態。

用 `keepMemory=False` 而非 `True`：opencode 的狀態本來就在磁碟上，不需要保存 RAM，落地體積更小、pause 更快（pause 成本約 4 秒/GB RAM）。

⚠️ Resume 後對外網路位址會變，且 E2B 明確指出「resume 不會自動重連 client」。→ resume 流程必須：重新取 `get_host(4096)` → 健康檢查 → 沒活著就重啟 opencode（因為 `keepMemory=False` 是冷啟動，程序本來就不在了）。

### 第二層：Session Snapshot（durable 保險，狀態 = 每個 session 幾百 bytes）

Paused sandbox 仍可能消失（誤刪、template 改版、E2B 帳號/配額問題、upstream 對重複 pause/resume 的已知 bug #884）。因此另外定期 + 在 pause 前做一次快照。

**不是 tar 整個檔案系統，而是逐 session 匯出 JSON：**

1. `GET /session` 列出所有 session
2. 對每個 id 跑 `opencode export <id>` → JSON
3. JSON 直接存進 MongoDB

還原：新沙盒 → 每個 JSON 跑一次 `opencode import` → 起 opencode。

實測數字：空 session 的匯出 JSON = **634 bytes**；相較之下整個 data dir = 308 KB。JSON 大小只隨對話長度成長，與 engine、依賴完全無關。這就是「越精簡越好」的具體答案。

⚠️ `opencode export` 不給 session id 會卡在互動式選單，必須逐一指定 id。

Plugin 原始碼不放進這份快照 —— 它們本來就以 constraint 文件的形式各自版本化在 Mongo。

### 抽象介面

`SandboxStateStore.snapshot(sandbox) -> list[dict]` / `.restore(sandbox, sessions)`，內部走 `export`/`import` 這個受支援的介面，而不是碰 SQLite schema。

---

## 現況關鍵事實（規劃依據）

### Constraint 目前長什麼樣

**FinFET / QFET** 呼叫 `src/cellgen/core/*.py` 的自由函式，形式高度一致：

```python
def prohibit_routing_to_left_cell_boundaries(instance):
    instance.opt.log_comment("Prohibiting routing to left cell boundaries ...")
    gathered = [instance.edge_vars[(u, v)]
                for u, v in instance.lgg.edges()
                if u[2] == 0 or v[2] == 0]
    instance.opt.Add(sum(gathered) == 0)
```

`instance` 就是 orchestrator 本身（context object），提供 `opt`（CP-SAT model）、`lgg`（LayeredGridGraph）、`circuit`、`tech`、`cell_config`，以及一整組變數字典（`edge_vars`、`net_arc_vars`、`transistor_vars`、`db_vars`…，全在 `_init_state_containers` 建立且有註解說明 key 形狀）。Constraint 可以**新建變數並掛回 instance** 供後續使用。

註冊點是扁平呼叫清單，例如 [FinFET/main.py:1543](SMTCellUCSD-2.0-main/src/cellgen/archit/FinFET/main.py:1543)。

**⚠️ CFET 是分叉的** — [CFET/main.py](SMTCellUCSD-2.0-main/src/cellgen/archit/CFET/main.py)（2532 行）把大部分 constraint 複製成自己的 private method，還有 core 沒有的 `_only_one_long_via_per_col`、`_only_one_miv_per_col`。寫在 `core/` 的新 constraint **只影響 FinFET + QFET**。

### 三個必須先處理的地雷

1. **`exit(1)` in library code** — INFEASIBLE/UNKNOWN 時 [FinFET/main.py:672](SMTCellUCSD-2.0-main/src/cellgen/archit/FinFET/main.py:672) 直接 `exit(1)`。→ 必須 subprocess-per-solve。
2. **cwd 相依** — `config.init()` 硬讀 `./input/pin_input_collection.json`。→ subprocess cwd 必須是 engine root。
3. **輸出路徑碰撞** — `OUT_DIR` 只由 `LIBNAME`/`HEIGHT_CONFIG` 決定，兩次只差 seed 的 run 會蓋掉彼此；`make config` 沒 `FORCE=1` 還會沉默重用舊 config。→ 不走 Makefile，直接呼叫，帶 per-run `--output_dir`。

### 可直接搬用的既有邏輯（不要重寫）

| 檔案 | 用途 |
|---|---|
| [gui/smtcell_gui/runner.py](SMTCellUCSD-2.0-main/src/gui/smtcell_gui/runner.py) | `os.setsid()` + SIGTERM→SIGKILL process group 的 cancel 模式 |
| [gui/smtcell_gui/logparse.py](SMTCellUCSD-2.0-main/src/gui/smtcell_gui/logparse.py) | `^status:`、`Elapsed time:`、`Obj#1 ... = N` 三條 regex |
| [gui/smtcell_gui/preset_parser.py](SMTCellUCSD-2.0-main/src/gui/smtcell_gui/preset_parser.py) | `.mk` preset 解析 |
| [gui/smtcell_gui/cdl_parser.py](SMTCellUCSD-2.0-main/src/gui/smtcell_gui/cdl_parser.py) | `scan_cdl()` 抓 subckt 名單 |
| `cell_config_editor.py` 的 `_TAG_ORDER` | config 依 `[TECH]`/`[SOLVER]`/… 標籤分組 → 前端表單沿用 |
| [solver/cpsat_wrapper.py](SMTCellUCSD-2.0-main/src/cellgen/solver/cpsat_wrapper.py) | 已把每個 `Add*` 記成可讀 log → 做「NL 請求實際產生了什麼」的 diff |

---

## 目標架構

```
CellGenerator/
├── engine/                     # SMTCellUCSD-2.0-main 就地演進
│   └── src/cellgen/plugins/    # ★ constraint registry + loader
├── services/api/               # FastAPI：REST + WS + opencode 反向代理
├── services/worker/            # Celery worker（Tab 2 才需要）
├── apps/web/                   # Vite + React + TypeScript SPA
└── infra/docker-compose.yml    # mongo, redis, api, web
```

前端不用 Next.js — 內部工具、後端獨立、需要的 SSR 為零；opencode 同源代理由 FastAPI 做。

---

# MVP：Tab 1 開發區

**MVP 完成定義**：能開沙盒 → iframe 內用 opencode 自然語言新增一條 constraint → 在沙盒內跑 smoke test 驗證 → 導出到 Mongo → **關掉分頁再打開，歷史對話與工作狀態完整還原**。

## Phase 0 — Engine 整備 + 狀態層 spike

1. `git init` engine，建 baseline commit。
2. `engine/pyproject.toml`：Python `>=3.11`，鎖 `ortools>=9.14.6206`、`loguru`、`numpy`、`networkx`、`scikit-learn`、`matplotlib`、`klayout`。PySide6 移到 optional extra。
3. **移除 library 內的 `exit(1)`** — `_interpret_solve_result` 改 raise `SolveFailed(status)`；`src/main.py` 的 `main()` 捕捉後 `sys.exit(1)`。CLI 行為不變但可被 import。
4. **Smoke test** — `engine/tests/test_smoke.py`：FinFET `INV_X1`，override `max_time.value=true, max_time.time=60`，斷言 status ∈ {OPTIMAL, FEASIBLE} 且 `.res` 產出。這是所有後續改動的安全網，也是沙盒內給 opencode 跑的測資。
5. `engine/cellgen_run.py` — 單一入口取代 Makefile 兩步驟，內部用搬來的 `preset_parser` 解析 preset，再依序跑 config 與 `src.main`，全部指向 per-run `--output_dir`。**worker 與沙盒共用的唯一執行介面。**
6. ~~**狀態層 spike**~~ ✅ **已完成** —— 結論寫在 `docs/opencode-state-spike.md`。三項計劃假設被推翻：`OPENCODE_DATA_DIR` 無效（要用 `XDG_DATA_HOME`）、不需要複製 SQLite（有官方 `export`/`import`）、快照量級是 bytes 而非 MB。

## Phase 1 — Constraint Plugin 層（階段一：只做掛點）

`engine/src/cellgen/plugins/`：

- `registry.py` — 裝飾器
  ```python
  @constraint(id="max_vias_per_col", stage="post_routing",
              tech=["FinFET", "QFET", "CFET"],
              params={"max_vias": 2}, description="...")
  def max_vias_per_col(inst, params): ...
  ```
  提供 `iter_constraints(stage, tech)`，依 manifest 順序回傳。
- `loader.py` — 從 `--plugin-dir` 掃 `*.py` 以 `importlib` 載入；只執行 manifest 中 `enabled` 的項目並注入 `params`。
- `hooks.py` — `run_stage(inst, stage)`：包在 `inst.opt.log_comment(f"[plugin:{id}] ...")` 之間，記錄前後 `len(proto.constraints)` 差值（前端顯示「這條加了幾條 CP-SAT constraint」）。

在三個 orchestrator 的 `_build_constraints` 插入五個掛點（`pre_placement` / `post_placement` / `pre_routing` / `post_routing` / `pre_objective`）。CFET 版本中間夾了 `inject_placement` 迴圈，掛點要對齊該結構，**不動既有順序**。此階段完全不動既有 constraint，回歸風險最低。

## Phase 2 — E2B 沙盒生命週期 + FastAPI 代理

### E2B template

自建 template：Python 3.11 + engine 依賴 + opencode CLI（**版本鎖定**，因為 `export`/`import` 實務上與版本耦合），engine repo 預先 clone 到 **固定路徑 `/workspace/engine`**（已實測確認 session 的 `projectID` 由絕對路徑推導，路徑一致性是還原的前提），並 `ENV XDG_DATA_HOME=/workspace/.local/share`。預裝依賴是必要的 —— 現場裝 ortools 要數分鐘。

啟動：
```
OPENCODE_SERVER_PASSWORD=<token> opencode web --port 4096 --hostname 0.0.0.0
```

### 生命週期 API

```
POST   /api/sandboxes              建立或還原（帶 bundle_id 時從 bundle 重建）
GET    /api/sandboxes/{id}         狀態 + 剩餘時間
POST   /api/sandboxes/{id}/pause   pause(keepMemory=False) + 先做 snapshot
POST   /api/sandboxes/{id}/resume  resume → 重取 host → 健康檢查 → 必要時重啟 opencode
DELETE /api/sandboxes/{id}
ANY    /api/sandboxes/{id}/oc/{path}   ← opencode 同源反向代理
```

- Mongo `sandboxes` collection：`{ _id, e2b_sandbox_id, state, workdir, last_snapshot_id, updated_at }`。
- Snapshot 存 GridFS；保留最近 N 份。
- 前端閒置 / 關閉分頁 → `sendBeacon` 觸發 pause；背景 job 對超時未活動的沙盒也 pause。

### 同源反向代理（避開 iframe + basic auth + CORS 三重坑）

前端 iframe 指向 `/api/sandboxes/{id}/oc/`，FastAPI 代理到 `https://<e2b-host>/` 並注入 `Authorization` header，WebSocket 一併代理。iframe 同源、token 不外洩到瀏覽器、不必開 `--cors`。

### AGENTS.md（決定 NL 開發能不能用）

沙盒內放一份文件：`@constraint` 介面、`instance` 上所有可用字典及 key 形狀（**從 `_init_state_containers` 的既有註解自動生成**）、`lgg` API 清單、`CPSAT` wrapper 支援的呼叫、以及 `pytest tests/test_smoke.py` 驗證指令。**這份文件的品質直接決定 opencode 寫不寫得出正確 constraint。**

## Phase 3 — Tab 1 前端

Vite + React + TS、TanStack Query、Tailwind + shadcn/ui。

- 沙盒狀態列：running / paused / restoring、剩餘時間、手動 pause / 重建。
- 主體：iframe 裝 opencode web UI（指向同源代理）。
- 側欄：目前 `plugins/` 檔案清單、最近一次 smoke test 結果、「導出這條 constraint」/「導出整個版本」按鈕。
- 首次進入若有既有 sandbox → 顯示「還原中」→ resume → iframe 掛載。

## Phase 4 — 導出到 MongoDB

```
constraints  { _id, name, description, tech[], stage, params_schema,
               source_code, version, parent_id, tags, smoke_status, created_at }
bundles      { _id, name, description, engine_commit,
               items: [{ constraint_id, version, order, enabled, params }],
               config_overrides, created_at }
```

Bundle 因為 plugin 化，存的是**參照清單 + 參數 + config overrides**，不是程式碼壓縮包。

沙盒內提供 `cellgen export constraint plugins/my_rule.py` 與 `cellgen export bundle`，POST 回 API → **AST 驗證**（必須恰有一個 `@constraint`；禁止 `import os/sys/subprocess/socket`、`open()` 寫入、`eval`/`exec`）→ 存 Mongo。前端按鈕觸發同一 API。

## Phase 5 — cellgen MCP server

沙盒內起 stdio MCP server，透過 `opencode.json` 註冊，暴露：

- `run_smoke(cell, tech, max_time)` — 跑小測資，回 status / objective / walltime
- `describe_context()` — 回傳 instance 可用變數字典與 lgg API
- `validate_constraint(path)` — AST + import 載入檢查
- `diff_constraint_log(before, after)` — 用 `FLAG_LOG_CONSTR` 輸出，顯示新 plugin 實際加了哪些 CP-SAT constraint
- `export_constraint(path, name, description)` — 直接導出到 Mongo

這就是「保留未來操作 MCP tool 能力」的落地形式。

---

# Tab 2：實驗區（MVP 之後）

## Phase 6 — 後端 Celery

```
experiments  { _id, name, bundle_id, preset, cells[], config_overrides, status, created_at }
runs         { _id, experiment_id, cell, status, celery_task_id, pid,
               metrics { solver_status, objective, cpp_cost, walltime,
                         conflicts, booleans, integers },
               artifacts { res, log, png, gds, var },   # GridFS
               started_at, finished_at }
```

- Broker/backend：Redis。產物存 GridFS（`.var` 2.6 MB 級，預設 gzip 且可關閉）。
- **不能設全域 time limit**（求解可能數小時）。`task_acks_late=True`、`task_reject_on_worker_lost=True`；`soft_time_limit` 由該實驗的 `max_time` + margin 逐 task 給。
- **concurrency = CPU 核數 ÷ `num_search_workers`（預設 8）**，否則 solver 互搶核心。
- 每個 task：建 `/runs/<run_id>/`，寫入 bundle 的 plugin 原始碼 + manifest → `subprocess.Popen(..., preexec_fn=os.setsid)` 跑 `cellgen_run.py`，cwd = engine root。
- stdout 逐行：① append 到 log 檔 ② `redis.publish(f"run:{id}:log", line)` ③ 過 `logparse` regex 與 CP-SAT 進度行，更新 metrics 與 bound 收斂序列。
- Cancel：`os.killpg(SIGTERM)` → 逾時 `SIGKILL`（照抄 `runner.py`）。

## Phase 7 — Tab 2 前端

- 建立實驗：選 bundle → preset → 從 CDL 勾選 cell → config overrides 表單（沿用 `[TAG]` 分組）→ 一個 cell 一個 run。
- run 表格：可排序 `cell, status, objective, cpp_cost, walltime, conflicts, booleans, integers`，即時更新。
- 即時 log：WebSocket 串 CP-SAT log + objective/best_bound 收斂曲線（Recharts），可中途 cancel。
- 單一 run 詳情：版面 `.png`（zoom/pan）、`.res` 全文、log 全文、下載 `.gds`。
- 兩兩比對：並排版面圖 + 指標 diff。
- 跨實驗圖表：不同 bundle 的 objective / walltime 分布、哪些 cell 常 timeout、objective-vs-time pareto 圖。

## Phase 8 — Plugin 化階段二

把 `core/placement.py`、`routing.py`、`rule.py`、`pin.py` 的既有自由函式逐一加 `@constraint` 註冊（**函式本體不改**），`_placement_constraints` / `_routing_constraints` 改為 ordered manifest 驅動的 `run_stage()`。CFET 的 private method 包薄 wrapper 註冊為 `tech=["CFET"]`。

**每搬一批就跑 regression 比對 objective 值必須完全相同。** 完成後使用者能在 UI 上把任一條既有 constraint 關掉再跑實驗——這是這個系統真正的價值點。

---

## 驗證方式

| 階段 | 怎麼驗 |
|---|---|
| Phase 0 | `cd engine && pytest tests/test_smoke.py` 通過；`cellgen_run.py` 連跑兩次不同 `--output-dir` 互不覆蓋；spike 產出一份「DB 檔名 + 實際 snapshot 大小」的結論 |
| Phase 1 | ✅ 已完成：no-op plugin 前後 objective 相同（不能比版面，見上）；過嚴 plugin → INFEASIBLE 且呼叫端存活 |
| Phase 2 | 建沙盒 → curl 代理端點拿到 opencode 首頁 → **pause → resume → 對話歷史仍在**；再測「kill 掉沙盒 → 從 snapshot 重建 → 歷史仍在」 |
| Phase 3 | 瀏覽器開開發區，用 browser tool 截圖確認 iframe 內 opencode 可互動；關分頁再開，確認自動 resume |
| **MVP 驗收** | 在 iframe 內用自然語言加一條 constraint → 跑 smoke → 導出 → 關掉重開，對話與檔案都在 |
| Phase 5 | 在 opencode 內問「有哪些變數可以用」，確認它呼叫 `describe_context` 而不是自己亂猜 |
| Phase 6 | POST 一個 2-cell 實驗，看到兩個 subprocess；cancel 其一，`ps` 確認 process group 無殘留 |
| Phase 8 | 每搬一批跑 3 個 cell（INV_X1 / NAND2_X1 / AOI21_X2）regression，**objective** 逐字相同（版面不可比） |

---

## 風險與取捨（明講）

1. **狀態還原依賴 opencode 的 `export`/`import`** — 這是受支援的公開介面（比碰 SQLite schema 安全得多），但仍與版本耦合。緩解：**E2B template 鎖定 opencode 版本**，升級時重跑一次 round-trip 測試。另外，spike 驗證的是 `messages: []` 的空 session —— 機制已證實，但**帶真實對話內容的 round trip 尚未驗證**，等沙盒有 provider key 後要第一個補測。
2. **paused sandbox 不是備份** — 所以才有第二層 snapshot。兩層都要有，只做 pause 是不夠的。
3. **CFET 分叉** — 短期新 constraint 若只寫在 core，CFET 吃不到。registry 用 `tech=[...]` 標註讓使用者清楚涵蓋範圍；Phase 8 才真正補齊。
4. **Plugin 是任意程式碼執行** — 內部工具可接受，但 worker 必須容器隔離、無外網、非 root、有資源上限。導出時的 AST 檢查是防呆不是防惡意。
5. **求解時間不可預測** — 6 顆電晶體跑 403 秒，DFF 可能數小時。UI 必須把 `max_time` 和 `use_relative_gap` 放最顯眼處（唯二能換迭代速度的旋鈕），且預設就開 `max_time`（engine 預設是無限制）。
6. **沒有既有測試** — Phase 0 的 smoke test 是所有後續改動的唯一安全網，不能省。

---

## 進度（最後更新：2026-08-18）

這份計劃**跟著程式碼一起版控**，每完成一個階段就更新。實作過程中推翻的假設會直接改在上面的內文，並在下面記錄原因。

| Phase | 狀態 | 產出 |
|---|---|---|
| 0 — Engine 整備 | ✅ 完成 | `engine/src/cellgen/run.py`、`errors.py`、`io/`、`pyproject.toml`、`tests/test_smoke.py` |
| 0 — 狀態層 spike | ✅ 完成 | `docs/opencode-state-spike.md` |
| 1 — Plugin 掛點 | ✅ 完成 | `engine/src/cellgen/plugins/`、`tests/test_plugins.py`、`plugins/examples/` |
| 1.5 — Context 文件產生器 | ✅ 完成 | `engine/src/cellgen/plugins/context_doc.py` → `engine/AGENTS.md` |
| 2 — 沙盒層（後端） | ✅ 完成 | `services/api/`（backends / state / proxy / service / main） |
| 3 — Tab 1 前端 | ⬜ 未開始 | `apps/web/` |
| 4 — 導出到 Mongo | ⬜ 未開始 | |
| 5 — cellgen MCP server | ⬜ 未開始 | |
| 6–8 — Tab 2 實驗區 | ⬜ 未開始 | |

測試現況：engine 43 passed、api 14 passed。

### 實作中推翻的計劃假設

1. **`OPENCODE_DATA_DIR` 不存在** — 實際要用 `XDG_DATA_HOME`。實測三個候選變數皆無效。
2. **不需要複製 SQLite** — opencode 有官方 `export` / `import`。空 session 匯出 = 634 bytes，data dir = 308 KB。
3. **subpath 代理行不通** — opencode UI 用 root-absolute 路徑載 `/assets/*.js`，且自己會打 `/api/*`（與本服務路由衝突）。改成**每個沙盒一個 root proxy port**。
4. **沙盒 workdir 必須是 git repo** — opencode 以 git worktree 認 project；不是 repo 的話所有 session 會落到 catch-all 的 `global` project，UI 上看不到工作區。
5. **版面幾何不可重現** — 同設定連跑 4 次，objective 恆為 1021.0，但版面有 3 種。`num_search_workers=1` 會被 `model_preset` 2 蓋掉、`deterministic_solve=true` 也不管用。→ 實驗比對只比指標。見 `docs/solve-reproducibility.md`。

### Commit 歷程

```
ffff38c Baseline: import SMTCellUCSD 2.0 as engine/
a04faf9 Phase 0: make the engine safe to drive from a service
9c984bc Phase 0 spike: settle how sandbox state is persisted and restored
36cf10d Phase 1: constraint plugin layer
488831c Generate the constraint-authoring reference from source
f15ef66 Phase 2: sandbox lifecycle, state capture and the opencode proxy
67e1d70 Serve each sandbox's opencode UI from its own root proxy
```
