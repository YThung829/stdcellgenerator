# 在公司環境把整套跑起來

**你不需要 E2B。** 只要機器上有 opencode,Tab 1 就走本機的 `opencode serve`,那是預設
行為(`CELLGEN_BACKEND=local`)。E2B 是給需要隔離或多人共用時才換的後端。

分三層,可以只裝你要的那層:

| 你要什麼 | 需要 |
|---|---|
| 只跑引擎解 cell | Python 3.11+ |
| **Tab 1 開發區** | 上面 + Node 20+、opencode、一把 model provider key |
| **Tab 2 實驗區** | 上面 + Redis |
| 多人共用 / 隔離 | 上面 + E2B(見最後一節) |

---

## 1. 裝起來

```bash
git clone <repo> && cd stdcellgenerator

python -m venv .venv && . .venv/bin/activate    # 建議用 venv
pip install -e "engine[dev,mcp]"                 # CP-SAT 引擎 + 沙盒用的 MCP server
pip install -e "services/api[dev]"               # 後端
pip install -e "services/worker[dev]"            # Tab 2 才需要

cd apps/web && npm install && cd ..
```

`pip install -e engine` 之後,`src.cellgen` 在任何目錄都 import 得到,**不需要再設
`PYTHONPATH`**。

opencode 請鎖版本——狀態還原走的是它的 `export`/`import`,那組介面與版本耦合:

```bash
npm i -g opencode-ai@1.18.18
which opencode      # 記下路徑,下面會用到
```

先確認環境沒問題:

```bash
cd engine && python -m pytest -q          # 應該 68 passed
cd ../services/api && python -m pytest -q # 應該 101 passed
```

---

## 2. 只跑 Tab 1(最小組合)

兩個行程。

```bash
# ── 終端 A:後端 ─────────────────────────────────────────
export ANTHROPIC_API_KEY=sk-...        # 或 OPENAI_API_KEY 等,見下方說明
export CELLGEN_OPENCODE_BIN=$(which opencode)
uvicorn cellgen_api.main:app --port 8000 --app-dir services/api

# ── 終端 B:前端 ─────────────────────────────────────────
cd apps/web && npm run dev
```

開 <http://localhost:5173>。

### provider key 是必要的

**沒有 key,沙盒會健康地啟動、UI 也會顯示,然後一條 constraint 都寫不出來**——這是
唯一一種從外面看起來像成功的失敗。

API 行程裡設好的 provider 變數會轉發進沙盒,走白名單:`ANTHROPIC_API_KEY`、
`OPENAI_API_KEY`、`OPENROUTER_API_KEY`、`GEMINI_API_KEY`、`AWS_*` 等。要加別的名稱:

```bash
export CELLGEN_SANDBOX_ENV=MY_COMPANY_LLM_TOKEN
```

如果公司走自架 gateway,設對應的 base URL(例如 `ANTHROPIC_BASE_URL` /
`OPENAI_BASE_URL`)一起轉發即可。

### 前端連不到後端時

前端固定在 5173 埠且不自動換埠,因為那是 API 預設允許的 CORS 來源。要改:

```bash
export CELLGEN_CORS_ORIGINS=http://your-host:5173   # 後端
export VITE_API_BASE=http://your-host:8000          # 前端
```

---

## 3. 加上 Tab 2(批次實驗)

多一個 Redis 和一個 worker。

```bash
# ── 終端 C:Redis ────────────────────────────────────────
redis-server --port 6379 --save "" --appendonly no

# ── 終端 D:worker ───────────────────────────────────────
cd services/worker
celery -A cellgen_worker.tasks worker --loglevel=INFO
```

後端和 worker 都要看到同一個 Redis 與同一個資料目錄:

```bash
export CELLGEN_REDIS_URL=redis://127.0.0.1:6379/0
export CELLGEN_DATA_ROOT=/srv/cellgen        # 兩邊要一致
```

沒有 Redis 時 Tab 2 仍然打得開,實驗會建立但 run 停在 `pending` 並附上原因——不會
默默消失。

**worker 的並行度是自動算的**:CPU 核數 ÷ `CELLGEN_SOLVER_WORKERS`(預設 8,因為
OR-Tools 每次 solve 會自己開 8 個 search worker)。8 核的機器 → 1 個並行 solve。要調:

```bash
export CELLGEN_SOLVER_WORKERS=4    # 8 核 → 2 個並行 solve
```

---

## 4. 用 docker compose 一次起完

```bash
docker compose -f infra/docker-compose.yml up --build
```

會起 mongo、redis、api、worker、web 五個服務。`engine/` 是掛載進去的,所以解的是你
當下的 checkout。記得把 provider key 傳進 `api` 和 `worker`:

```yaml
environment:
  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
```

> compose 檔語法驗證過,但**整套 build 沒有實際跑過**——這個開發環境沒有 docker。
> 第一次起請當成 bring-up。

---

## 5. 設定一覽

| 變數 | 預設 | 意義 |
|---|---|---|
| `CELLGEN_BACKEND` | `local` | `local`(本機 opencode)或 `e2b` |
| `CELLGEN_OPENCODE_BIN` | PATH 上的 `opencode` | opencode 執行檔 |
| `CELLGEN_ENGINE_SRC` | `./engine` | 複製進沙盒的 engine checkout |
| `CELLGEN_DATA_ROOT` | `./.cellgen` | 沙盒、store、run 產出 |
| `CELLGEN_REDIS_URL` | `redis://127.0.0.1:6379/0` | Tab 2 的 broker |
| `CELLGEN_MONGO_URL` | 未設 | 設了用 MongoDB,連不上退回檔案儲存 |
| `CELLGEN_CORS_ORIGINS` | `http://localhost:5173` | 允許的前端來源 |
| `CELLGEN_SANDBOX_ENV` | 未設 | 額外轉發進沙盒的變數名,逗號分隔 |
| `CELLGEN_IDLE_PAUSE_SECONDS` | `1800` | 閒置多久自動暫停沙盒;`0` 關閉 |
| `CELLGEN_SOLVER_WORKERS` | `8` | OR-Tools 每次 solve 的 search worker 數 |
| `E2B_API_KEY` / `CELLGEN_E2B_TEMPLATE` | 未設 / `cellgen-engine` | 只有 `e2b` backend 用 |

MongoDB 是選用的。沒設就用檔案儲存,對單人／小團隊完全夠——存的都是小 JSON 文件。

---

## 6. 什麼時候才需要 E2B

`local` backend **沒有隔離**:plugin 是以跑 API 的那個使用者權限直接執行的。單人在自己
機器上開發沒問題;要多人共用、或不信任 agent 寫出來的程式碼,就換 E2B。

那需要:

1. `E2B_API_KEY` 與 `CELLGEN_BACKEND=e2b`
2. **先 build template**(還沒 build 過):
   ```bash
   e2b template build -c infra/e2b/e2b.toml -n cellgen-engine
   ```
3. 網路能到 `api.e2b.app` 與 `*.e2b.app`(沙盒主機名含隨機 id 且每次 resume 會變,
   萬用字元是必要的)

> **E2B 那條路徑從沒對真的服務執行過。** 程式碼逐一對照過已安裝的 SDK 並有契約測試
> (抓到 `AsyncSandbox.resume` 根本不存在等五個缺陷),但那證明的是「呼叫是真的」,
> 不是「沙盒起得來」。第一次接上去請當成 bring-up,不是回歸測試。
>
> 另一個未知數:opencode 的 UI 走我們的反向代理,那個代理只處理 HTTP(含串流回應)。
> 若中間的網路層把串流 buffer 住,UI 會載得出來但訊息不會逐字出現——本機模式不受影響。

**逐步驟的 bring-up 清單(含每一關的判準與失敗時該看哪裡)在
[`e2b-bringup.md`](e2b-bringup.md)。**

---

## 7. 出問題先看哪裡

| 症狀 | 看這裡 |
|---|---|
| 沙盒 `failed` | `$CELLGEN_DATA_ROOT/sandboxes/<id>/opencode.log`;多半是 `CELLGEN_OPENCODE_BIN` 指錯或埠被佔 |
| UI 一直「連不上 API」 | 後端沒起,或 `CELLGEN_CORS_ORIGINS` 沒包含前端網址 |
| 沙盒起來但 agent 不回應 | 沒有 provider key,或 key 沒被轉發(`CELLGEN_SANDBOX_ENV`) |
| 實驗建立了但 run 一直 `pending` | Redis 連不到,或 worker 沒起;run 記錄裡有 `dispatch_error` |
| solve 跑不完 | 引擎預設**沒有時間上限**;實驗表單的 `max_time` 一定要設 |
| `/api/builtins` 回 503 | engine 沒裝進同一個 Python 環境 |
