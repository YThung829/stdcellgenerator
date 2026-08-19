# E2B bring-up:在私有環境把 e2b backend 接上去

這份文件是給**執行者**（人或代理）照做的。它假設你已經能在同一台機器上用
`CELLGEN_BACKEND=local` 把 Tab 1 跑起來——如果還不行，先照 [`deploy.md`](deploy.md)
把本機模式弄通，否則接下來每一個失敗都會有兩種可能的原因。

> **這條路徑從沒對真的 E2B 服務執行過。** `services/api/cellgen_api/backends/e2b.py`
> 逐一對照過已安裝的 SDK，並有契約測試（`tests/test_e2b_backend.py`，抓到過
> `AsyncSandbox.resume` 根本不存在等五個缺陷），但那證明的是「呼叫是真的」，不是
> 「沙盒起得來」。**把第一次接上去當成 bring-up，不是回歸測試**：預期會踩到東西，
> 每一節都寫了踩到時該看哪裡。

E2B 解決的問題只有一個：**隔離**。`local` backend 裡 agent 寫出來的 plugin 是以跑
API 的那個使用者的權限直接執行的。單人在自己機器上開發不需要 E2B。

---

## 0. 前置檢查（不連 E2B）

```bash
pip install -e "services/api[dev,e2b]"
python -c "import e2b; print(e2b.__version__)"
cd services/api && python -m pytest tests/test_e2b_backend.py -q
```

這組測試**不連網**，它讀後端原始碼並確認每個 SDK 呼叫在已安裝的版本裡真的存在。
它紅了代表 SDK 版本漂移了，**先修這個再往下**，否則後面的失敗會被誤判成環境問題。

網路需求：

| 目標 | 用途 |
|---|---|
| `api.e2b.app` | 建立／暫停／刪除沙盒的控制面 |
| `*.e2b.app` | 沙盒本身。主機名含隨機 id，**且每次 resume 都會變**，所以萬用字元是必要的，不能只放行一個固定 host |

`E2B_API_KEY` 從 E2B dashboard 取得。

---

## 1. Build template

```bash
# 一定要在 repository root 執行：e2b.Dockerfile 裡有 COPY engine/
e2b template build -c infra/e2b/e2b.toml -n cellgen-engine
```

template 內容在 [`infra/e2b/e2b.Dockerfile`](../infra/e2b/e2b.Dockerfile)。它固定三件事，
每一件都是**狀態還原**的前提，不是為了解題：

1. engine 在 `/workspace/engine`，**不能改**。opencode 會記錄 session 所屬的絕對工作
   目錄，而 E2B 的「重建」永遠是一個全新沙盒——這個固定路徑是唯一把還原後的 session
   接回原專案的東西。
2. `/workspace/engine` 是一個**有 commit 的 git repo**。opencode 以 git worktree 認
   project；沒有 repo 的話所有 session 會掉進 catch-all 的 `global` project，UI 上看不到
   工作區。那個 commit 同時是 artifact 匯出時 diff 的 baseline。
3. `XDG_DATA_HOME=/workspace/.local/share`。這是唯一真的能移動 opencode 儲存位置的
   環境變數（`OPENCODE_DATA_DIR` 無效，實測見 [`opencode-state-spike.md`](opencode-state-spike.md)）。

Dockerfile 末端有五道自我檢查，build 過了就代表這三件事成立：

```
test -d /workspace/engine/.git
git -C /workspace/engine rev-parse HEAD
test "$XDG_DATA_HOME" = /workspace/.local/share
opencode --version
python -c "import src.cellgen.run"
```

**build 失敗時**：看是哪一道。`import src.cellgen.run` 失敗多半是 engine 依賴沒裝好；
`opencode --version` 失敗是 npm 安裝那層的問題。

> opencode 的版本在 Dockerfile 裡**釘死**（`opencode-ai@1.18.18`）。狀態還原走的是
> opencode 自己的 `export`/`import`，那組介面與版本耦合。要升級請先重跑
> [`opencode-state-spike.md`](opencode-state-spike.md) 的 round-trip 檢查。

---

## 2. 最小連通性測試（繞過本專案的服務）

**先做這一步。** 目的是把「E2B 帳號／網路／template」和「我們的服務程式碼」分開驗；
這一步過不了，後面每一個失敗都無法歸因。

```python
# scratch/e2b_ping.py
import asyncio, os
from e2b import AsyncSandbox

async def main():
    sbx = await AsyncSandbox.create(
        template="cellgen-engine", timeout=300,
        api_key=os.environ["E2B_API_KEY"],
    )
    try:
        print("sandbox:", sbx.sandbox_id)
        r = await sbx.commands.run("git rev-parse HEAD", cwd="/workspace/engine")
        print("baseline:", r.exit_code, r.stdout.strip())
        r = await sbx.commands.run("opencode --version")
        print("opencode:", r.stdout.strip())
        print("host:", sbx.get_host(4096))
    finally:
        await AsyncSandbox.kill(sandbox_id=sbx.sandbox_id, api_key=os.environ["E2B_API_KEY"])

asyncio.run(main())
```

四行輸出都要有值。`git rev-parse` 的 exit code 必須是 0——**沒有 baseline commit 的話，
之後的匯出會直接 400**。

---

## 3. 切到 e2b backend 起服務

```bash
export CELLGEN_BACKEND=e2b
export E2B_API_KEY=...
export CELLGEN_E2B_TEMPLATE=cellgen-engine
export ANTHROPIC_API_KEY=...        # 或其他 provider；會被轉發進沙盒
uvicorn cellgen_api.main:app --port 8000 --app-dir services/api

curl -s localhost:8000/api/health   # 期待 {"ok":true,"backend":"e2b"}
```

**provider key 是必要的。** 沙盒是另一台機器，什麼都不會繼承，所以 key 必須明確轉發。
轉發走白名單（`config.py:Settings.DEFAULT_SANDBOX_ENV`）：`ANTHROPIC_API_KEY`、
`OPENAI_API_KEY`、`OPENROUTER_API_KEY`、`GEMINI_API_KEY`、`AWS_*`、對應的 `*_BASE_URL`
等。名稱不在清單上時：

```bash
export CELLGEN_SANDBOX_ENV=MY_COMPANY_LLM_TOKEN
```

沒有 key 時的症狀很難認：**沙盒會健康地啟動、UI 也會顯示，然後一條 constraint 都寫不
出來**——唯一一種從外面看起來像成功的失敗。

---

## 4. 逐項驗收

每一項都有客觀判準。**照順序做**，前一項沒過不要往下。

### 4.1 建立沙盒

```bash
curl -sX POST localhost:8000/api/sandboxes \
     -H 'content-type: application/json' -d '{"sandbox_id":"e2btest"}' | jq
```

通過：`state = "running"`、`workdir = "/workspace/engine"`、`proxy_url` 有值。

失敗（`state = "failed"`）：`_start_opencode` 的 `wait_healthy` 逾時了（預設 60 秒，
`backends/base.py:136`）。沙盒本身多半是活的，去 E2B dashboard 用 metadata
`cellgen_sandbox_id=e2btest` 找到它，進去看 `opencode serve` 有沒有在跑、`4096` 埠有沒有
在聽。**注意 health check 打的是 `GET /session`，需要 basic auth**，手動測要帶
`-u opencode:<password>`。

### 4.2 opencode UI（最可能卡住的一關）

瀏覽器開 `proxy_url`。通過：UI 載得出來**而且能互動**（送得出訊息、看得到回應逐字出現）。

這一關卡住時要知道 proxy 的能力邊界：[`proxy.py`](../services/api/cellgen_api/proxy.py)
只代理 **HTTP，含串流回應（SSE）**——它是 Starlette 的 `Route`，**沒有 `WebSocketRoute`**。
本機模式能用代表目前版本的 opencode UI 走的是 SSE。若公司的網路層或反向代理在中間
buffer 住串流回應，UI 會載得出來但訊息不會逐字出現；若未來 opencode 改用 WebSocket，
這裡要補 `WebSocketRoute`。

另外 `content-encoding` 是**刻意保留**的（body 用 `aiter_raw()` 原樣轉發）。若你在中間
加了任何會改寫壓縮的層，UI 會載得進來卻解不開自己的 API 回應。

### 4.3 沙盒位址沒外洩

```bash
curl -si https://<沙盒host>/session | head -1     # 期待 401
```

通過：401。密碼只在 API 行程裡，瀏覽器拿不到。

### 4.4 agent 有沒有腦

在 UI 裡問任何一句話。通過：有回應。

沒回應 → provider key 沒轉發進去。在沙盒內 `env | grep -i api_key` 確認。

### 4.5 MCP tool 有沒有接上

在 UI 裡問「這個 engine 有哪些變數可以用？」。

通過：它去呼叫 `describe_context`，而不是自己猜。template 內建了
`/workspace/engine/opencode.json`（與 `backends/base.py` 的 `OPENCODE_CONFIG` 逐位元組
相同）。沒接上就檢查那個檔案在沙盒裡是否存在且可解析。

### 4.6 寫一個 plugin 並驗證

在 UI 裡：

> 讀 AGENTS.md。在 `plugins/` 新增一條 constraint，限制 `inst.edge_vars` 中為 true 的
> edge 總數不超過參數 `max_edges`（預設 10000），然後用 `run_smoke` 驗證 INV_X1。

通過：smoke 回 OPTIMAL 或 FEASIBLE。再把 `max_edges` 收緊到明顯不可行（例如 5），
應該回 INFEASIBLE **而且服務沒有掛**——engine 內的 `exit(1)` 早在 Phase 0 就換成了
`SolveFailed`。

### 4.7 匯出

```bash
curl -sX POST localhost:8000/api/sandboxes/e2btest/export \
     -H 'content-type: application/json' -d '{"name":"e2b bring-up"}' | jq '{plugins:[.artifact.plugins[].path], patch_bytes}'
```

通過：`plugins[]` 含新檔案。

400 `sandbox has no baseline commit` → `_record_baseline` 沒拿到 commit，回頭看第 2 節。

這一步也順帶驗了 `E2BBackend.shell` 的 quoting：E2B 的 `commands.run` 走 shell，而 artifact
capture 會傳 `:(exclude)plugins/*` 這種 git pathspec，沒 quote 的話 shell 會把括號當語法錯誤。

### 4.8 pause / resume（帶真實對話）

```bash
curl -sX POST localhost:8000/api/sandboxes/e2btest/pause  | jq .state   # paused
curl -sX POST localhost:8000/api/sandboxes/e2btest/resume | jq '{state, proxy_url}'
```

通過三件事同時成立：
- `state = "running"`；
- **UI 裡 4.6 的對話還在**；
- 沙盒位址與 pause 前**不同**（E2B 明確不會替 client 重連，所以 resume 一定重新解析）。

實作細節（失敗時對照）：pause 走 `AsyncSandbox.pause(sandbox_id=...)` 而不是連線物件，
因為**連線本身會先把沙盒喚醒**；resume 沒有對應的 API，是靠 `connect` 的副作用喚醒，
而且因為 `keep_memory=False` 是冷啟動，opencode 一定不在跑，所以 resume 之後必然要
重起它。

### 4.9 沙盒真的沒了以後（狀態持久化的真正考驗）

```bash
curl -sX DELETE "localhost:8000/api/sandboxes/e2btest?keep_snapshots=true"
curl -sX POST localhost:8000/api/sandboxes \
     -H 'content-type: application/json' -d '{"sandbox_id":"e2btest"}' | jq .restored_sessions
```

通過：`restored_sessions > 0`，且 UI 裡看得到舊 session 與**對話內容**。

這是 E2B 與 local 差最多的地方：E2B 沒有「同一個沙盒 id 再來一次」這種東西，重建是全新
沙盒。把 session 接回去的**只有** `/workspace/engine` 這個固定路徑，加上 snapshot 裡逐
session 匯出的 JSON。

> ⚠️ **這一關是整個系統目前唯一沒被真實資料驗證過的核心宣稱。** 當初的 spike 只驗過
> `messages: []` 的空 session：機制證實了，但帶真實對話內容的 round trip 沒測過。
> 這裡失敗的話，那是 **opencode 版本耦合問題，不是沙盒層的問題** —— 處置是回到
> [`opencode-state-spike.md`](opencode-state-spike.md) 重跑 round-trip，再調整 template
> 裡釘住的版本，而不是去改 `service.py`。

### 4.10 閒置回收

把沙盒放著不動超過 `CELLGEN_IDLE_PAUSE_SECONDS`（預設 1800）。

通過：自動變成 `paused`，而且**pause 前先做了 snapshot**（`GET /api/sandboxes/{id}/snapshots`
會多一筆）。proxy 的流量算活動訊號，所以測的時候不要開著 UI。`=0` 可關閉。

E2B 上這件事有錢的意義：沙盒跑著就在計費。

---

## 5. 已知風險與旋鈕

| 事項 | 位置 | 說明 |
|---|---|---|
| 沙盒逾時 | `e2b.py:DEFAULT_SANDBOX_TIMEOUT = 3600` | E2B SDK 自己的預設是**幾分鐘**，會在使用者思考到一半時砍掉工作區。長時間 solve 要調大 |
| pause 成本 | — | 約 4 秒/GB RAM。template 開 4 CPU / 4 GB（`infra/e2b/e2b.toml`） |
| 相對路徑 | `e2b.py:_abs` | E2B 的 `files.read/write` 把相對路徑解析成相對 home，不是 workdir |
| shell quoting | `e2b.py:shell` | E2B 的 `commands.run` 走 shell，argv 一律 `shlex.quote` |
| 連線副作用 | `e2b.py:_sandbox` | `connect` 會喚醒 paused 沙盒，所以 pause / kill 都用 id 而非連線物件 |

## 6. 完成後

把 [`plan.md`](plan.md) 進度區的「項目 1（E2B 實測）⛔ 受阻」與「項目 2（真實對話
round-trip）⛔ 受阻」改成實際結果，**包含失敗的部分**——這兩項就是為了記錄「什麼還沒被
真的驗證過」而存在的，把它們改成 ✅ 卻沒真的跑過，比留著 ⛔ 更糟。
