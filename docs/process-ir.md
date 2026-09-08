# 製程架構 IR 指南

這個 repo 用三份中間表達式（IR）描述製程。它們都是純文字、都可以被機器驗證、
也都設計成讓製程工程師填得下去。

這份文件講**怎麼填**和**怎麼讀**。

---

## 0. 我要填哪一份？

先回答這個問題，其他的才有意義。

| 你想做的事 | 填這份 | 格式 | 規模 |
|---|---|---|---|
| 讓 router / solver 認得一個新架構 | `tools/routing-ir/specs/<name>.json` | JSON | 7–12 層 |
| 讓立體圖畫得出一個新架構 | `tools/stack3d/techs/<name>.toml` | TOML | 16–34 層 |
| 說明兩個架構之間怎麼對應 | `tools/stack3d/migrations/<a>_to_<b>.json` | JSON | 依架構 |

**大部分情況下你只需要第一份。** 繞線視角是最小集合，也是最常變動的。
只有要做展示或教學時才需要第二份；第三份只在要講「A 怎麼變成 B」時才需要。

三份都有範本可以複製：

```
tools/routing-ir/specs/_TEMPLATE.json
tools/stack3d/techs/_TEMPLATE.toml
tools/stack3d/migrations/finfet_to_cfet.json   （拿這份當範本）
```

---

## 1. 繞線視角 IR — `routing-ir/specs/*.json`

> **一句話**：router 需要知道的最小集合。實體設計把製程抽象成
> OD · POLY · MD · VIA · M0 · M1，這份就是那六類東西加上各自的格點。

### 1.1 最重要的欄位是 `in_graph`

**不是抽象裡的每一層都是繞線圖上的層。** 這是整份 IR 最容易誤解的地方，
也是它跟「把六類平鋪成一張清單」的差別。

```jsonc
{ "id": "M0", "class": "metal", "in_graph": true,
  "direction": "H", "pitch": 24, "offset": 0, "width": 14 }

{ "id": "MD", "class": "md", "in_graph": false,
  "access_via": "PC", "enabled_by": "lisd_routing" }
```

| | `in_graph: true` | `in_graph: false` |
|---|---|---|
| 意思 | `LayeredGridGraph` 在這層上有節點，繞線是穿過這些節點的路徑 | 這層有幾何，但圖上沒有它 |
| 誰屬於這類 | `poly` 和所有 `metal` | `od` 和 `md` |
| 必填 | `direction` / `pitch` / `offset` / `width` | `access_via`（透過哪個圖層碰到它） |
| 選填 | `io_pin` | `enabled_by`（哪個 config 旗標開關這條路） |

### 1.2 為什麼 MD 不是圖層

這點值得單獨講，因為它違反直覺。

在這個 engine 裡，`ACTIVE` 和 `LISD` 在 `engine/input/layer/*.json` 裡都是
`"layer_type": "gds"` —— **solver 從頭到尾看不到它們**。

「在 MD 上繞線」實際上被模型化成**允許 `poly ↔ M0` 的 via 落在 source/drain 欄
上**（見 `engine/src/cellgen/core/routing.py` 的 `ban_middle_row_via_for_3T`）。
`lisd_routing` 控制 S/D 欄、`lig_routing` 控制 gate 欄。

所以如果 IR 給 MD 一個 `pitch`，那個數字不會有任何作用 —— router 不會沿著它走。
`in_graph: false` 存在就是為了讓填表的人不會浪費時間找那個不存在的數字。

### 1.3 六個 class

| class | 在圖上 | 意思 |
|---|---|---|
| `od` | 否 | 擴散區。router 只需要知道位置。 |
| `poly` | **是** | 閘極，同時是放置層（電晶體坐在上面）。垂直，走 CPP 格點。 |
| `md` | 否 | 區域互連。用 `contacts` 分是接 `source_drain` 還是 `gate`。 |
| `metal` | **是** | 真正的繞線層。M0 / M1 / BM0 / H0 都是這類。 |
| `via` | 是 | 連接兩層 —— 寫在 `connect`，不寫在 `layers`。 |
| `virtual` | 是 | 圖上的捷徑、沒有光罩 —— 寫在 `shortcuts`。 |

`class` 只表達**語意角色**；層在 `layers` 陣列裡的位置給堆疊順序。所以 QFET 的
`BM0` / `H0` / `M0` 全都是 `metal`，不需要為「背面金屬」「層間金屬」發明新 class。

### 1.4 怎麼讀一份現成的

```bash
python tools/routing-ir/routing_ir.py --show FinFET
```

```
FinFET  (finfet.json)   單層平面。P/N 在同一平面的兩條 row band，靠 row 分開。

  id       class    graph  tier   dir   pitch  offset  width  gds
  OD       od       no     front  -         -       -      -  11/0
  MD       md       no     front  -         -       -      -  17/0
  MG       md       no     front  -         -       -      -  16/0
  PC       poly     yes    front  V      45.0     0.0   16.0  7/0
  M0       metal    yes    -      H        24     0.0   14.0  15/0
  M1       metal    yes    -      V      30.0     0.0   14.0  19/0
  M2       metal    yes    -      H      24.0     0.0   14.0  20/0

  via chain
    CA          PC -> M0      gds 14/0
    V0          M0 -> M1      gds 18/0
    V1          M1 -> M2      gds 21/0
```

讀法：`graph` 欄是 `no` 的三層是元件端，router 透過 `PC` 碰到它們；
`graph` 是 `yes` 的四層才是真正可以走的軌道。via 鏈告訴你哪兩層之間可以打洞。

### 1.5 怎麼填一份新的

```bash
# 已經有 engine 檔案的話，先反推草稿
python tools/routing-ir/routing_ir.py --derive MYTECH > tools/routing-ir/specs/mytech.json

# 不知道 GDS 層號填什麼，先解一顆 cell 再問
python tools/stack3d/inspect_tech.py --name MYTECH --gds <解好的.gds>

# 填完驗證
python tools/routing-ir/routing_ir.py --check
```

`--derive` **只會產出圖上的層**（poly 和 metal），因為 od / md 在 engine 的
layer JSON 裡根本不存在。那兩類要自己加 —— 這也正好是需要人判斷的部分。

---

## 2. 光罩疊構 IR — `stack3d/techs/*.toml`

> **一句話**：完整的光罩疊構加上 z 模型，給立體圖用。

### 2.1 核心觀念：只講順序和角色，z 由系統算

原本這份資料是手寫的 Python tuple：

```python
("11/2", "N_ACTIVE", 0, 46, "bot", "#10b981", "...", 1)
```

六個欄位裡有兩個是 z 座標，而**那兩個數字是編的** —— repo 裡沒有任何一層帶厚度
資訊。跟工程師要座標只會把假的精確變得更有權威。

所以格式反過來：**你只講順序和角色，`techspec.py` 從 role 的厚度表算 z。**

```toml
[[layer]]
name = "N_ACTIVE"
role = "diffusion"     # 這層「是什麼」→ 決定預設厚度、顏色、分組、透明度
gds  = "11/2"
align = "FIN"          # 跟 FIN 從同一個高度起算
thickness = 46         # 選填；不填就用 role 的預設
```

**層在檔案裡由下而上列，順序就是 z 模型。**

### 2.2 四種擺放方式

| 寫法 | 意思 | 什麼時候用 |
|---|---|---|
| 什麼都不寫 | 接在上一層正上方 | 預設，BEOL 幾乎都是 |
| `gap = 26` | 往上空 26nm 再開始 | tier 之間的隔離 |
| `align = "FIN"` | 跟 FIN 同高度起算，可加 `offset` | 同一製程階段的層（fin 和包住它的擴散區） |
| `span = ["A","B"]` | 不佔自己的高度，從 A 底貫穿到 B 頂 | 貫穿多個 tier 的 gate |

`align` 存在的唯一理由是**不要讓人寫負的 gap**。`gap = -42` 是要讀者自己算的
減法，`align = "FIN"` 是一句關於製程的話。

同理，**背面 tier 不需要鏡像旗標** —— 把它的互連寫在擴散區*之前*，順序本身就
說明了接觸往下走。

### 2.3 role 一覽

```
substrate  implant  channel  diffusion  sd_trench  gate  cut
mol_sd  mol_gate  via  metal  virtual  model_only  boundary
```

每個 role 帶預設的厚度、分組、顏色、是否預設顯示、以及透明度。
`model_only` 給「solver 認得但 writer 從不繪製」的層 —— 它會在側欄以空列出現，
讓「這個 tier 存在於模型但沒有光罩」這件事看得見，而不只是寫在文件裡。

### 2.4 一個要注意的事：現成的三份不是手寫風格

`finfet.toml` / `cfet.toml` / `qfet.toml` 是用 `--export` 從舊的 Python 表**機器
反推**出來的，所以它們**只用 `align` + `offset` + `thickness`，一個 `gap` 和
`span` 都沒有**。匯出器只能還原絕對 z，還原不了意圖。

例如 CFET 那根貫穿兩個 tier 的 gate，匯出成：

```toml
name = "GATE (PC+BPC)"
role = "gate"
align = "FIN"
thickness = 150
```

手寫的話會是 `span = ["FIN", "P_LISD"]` —— 更易讀，而且上下層改動時會自動跟著
變。（兩者不完全等價：`span` 到 `P_LISD` 的頂是 158，手調的值是 150。）

**要看慣用寫法，看 `_TEMPLATE.toml`，不要看那三份匯出的。**

### 2.5 怎麼填

```bash
cp tools/stack3d/techs/_TEMPLATE.toml tools/stack3d/techs/mytech.toml
python tools/stack3d/techspec.py --check         # 驗證
python tools/stack3d/techspec.py --show MYTECH   # 看編譯出來的堆疊表
```

`layers.py` 和 `build.py` 都不用改。如果你發現自己在改它們，多半是找錯擴充點了。

---

## 3. 製程遷移 IR — `stack3d/migrations/*.json`

> **一句話**：兩個架構之間哪一層變成哪一層。

### 3.1 分兩層記，這個區分是重點

| 區段 | 記什麼 | 例子 |
|---|---|---|
| `masks` | GDS 上看得到的層對應 | `11/0 ACTIVE` → `11/1 P_ACTIVE` + `11/2 N_ACTIVE` |
| `model` | solver 拿到、但**不產生任何幾何**的 | `BPC` placement tier、`BCA` via、`BPC↔M0` 虛擬邊 |

`model` 那三項**全都畫不出東西**。CFET 相對 FinFET 的核心改變（多一個放置層）
在 GDS 上是完全看不到的 —— 只寫光罩對應會漏掉整個故事。

### 3.2 每個對應標一個 `kind`

| kind | 意思 |
|---|---|
| `split_by_tier` | 一層拆成上下兩層（`ACTIVE` → `P_ACTIVE` + `N_ACTIVE`） |
| `reinterpreted` | 圖層號沒變，但語意變了（CFET 的 `7/0` gate 變成貫穿兩個 tier） |
| `unchanged` | 沒變 |

### 3.3 怎麼填

```bash
python tools/stack3d/migrate.py --report   # 印出每一層的配對表
python tools/stack3d/migrate.py --check    # 只驗證
```

兩端的架構都要先存在於 `techs/` 才能寫遷移。

---

## 4. 機器驗證什麼、不驗證什麼

**這一節比前面都重要。** 知道哪些欄位有守衛、哪些是人為斷言，才知道 review 時
該盯哪裡。

### 4.1 繞線視角 IR

| 欄位 | 驗證方式 |
|---|---|
| `in_graph: true` 的層的 `direction` / `pitch` / `offset` / `width` | ✅ **跟 `source.layer_json` 指向的真實 engine 檔案逐項比對**，不符就 exit 非零 |
| `connect` 的 via 鏈 | ✅ 雙向比對：spec 有而 engine 沒有、engine 有而 spec 沒有，都會報 |
| `class` 是不是認得的 | ⚠️ 不認得只警告，會退化成不透明幾何處理 |
| `tier` / `access_via` 指到的名字存不存在 | ✅ 驗證引用完整性 |
| `access_via` 的**語意**對不對 | ❌ 不驗 —— 說 MD 透過 PC 接，機器不會去查真的是不是 |
| `enabled_by` | ❌ 不驗，純標註 |
| `pins.access` / `pins.io` | ❌ 不驗。FinFET / CFET 的 layer JSON 根本沒標 `io_pin`，所以那兩份的 `pins.io` 是**人為判斷**（spec 裡有註明） |
| `od` / `md` 的 `gds` 層號 | ❌ 不驗 —— 那兩類在 engine layer JSON 裡不存在，無從比對 |

### 4.2 光罩疊構 IR

| 欄位 | 驗證方式 |
|---|---|
| 堆疊順序 vs layer JSON 的 `layer_number` 排序 | ✅ `audit.py` 逐項比對 |
| 每個 via 是不是真的夾在它宣告的兩層之間 | ✅ |
| 註解裡引用的 pitch / offset / direction | ✅ 跟 layer JSON 比對 |
| 「畫了卻沒列進表」的層 | ✅ build 時警告 |
| 「layer JSON 宣告、writer 畫得出來、但表沒列」的層 | ✅ `audit.py` 檢查宣告而不是這顆 cell 的樣本 |
| **z 的厚度數值** | ❌ **無從驗證 —— repo 裡沒有厚度資料。這是唯一編出來的東西。** |

### 4.3 對外說明時務必講清楚的一句話

> 多邊形座標、層的先後順序、CPP、cell 尺寸、objective 全部是從 repo 檔案推導的，
> `audit.py` 可以當場重跑驗證。**唯獨 z 方向的厚度是示意值** —— repo 沒有厚度
> 資料，只保證順序與「誰跨越誰」正確，不可當 PDK 數值使用。

`audit.py` 的輸出本身就會主動印出這三行警告，不會讓人誤讀。

---

## 5. 新架構從零到完成

```bash
# 1. 把事實挖出來：LGG z 順序、via 鏈、虛擬邊、writer 用到哪些 layer/datatype
python tools/stack3d/inspect_tech.py --name MYTECH

# 2. 繞線視角（最重要，router 要用）
python tools/routing-ir/routing_ir.py --derive MYTECH > tools/routing-ir/specs/mytech.json
#    手動補上 od / md 兩類（--derive 產不出來，因為它們不在 engine layer JSON 裡）
python tools/routing-ir/routing_ir.py --check

# 3. 解一顆小 cell，產生 GDS
python tools/stack3d/build.py --tech MYTECH --solve --gds

# 4. 拿實際產出的 GDS 對一次涵蓋率
python tools/stack3d/inspect_tech.py --name MYTECH --gds tools/stack3d/data/solved/mytech/INV_X1.gds

# 5. 光罩疊構（要畫立體圖才需要）
cp tools/stack3d/techs/_TEMPLATE.toml tools/stack3d/techs/mytech.toml
python tools/stack3d/techspec.py --check

# 6. 重建並實際打開來看
python tools/stack3d/build.py
```

第 3 步需要 engine 的完整相依套件（ortools 等）；其餘步驟只要 Python 標準
函式庫，或（產 GDS 時）klayout。

---

## 6. 常見誤解

**「六類都是繞線層，所以 MD 也要給 pitch」**
不是。`od` 和 `md` 的 `in_graph` 是 `false`，給了 pitch 也沒有作用。
見 §1.2。

**「CFET 的 P/N ACTIVE 座標一樣，一定是哪裡錯了」**
不是。CFET 的兩顆元件共用同一塊平面面積、只差在 z，GDS 是平面格式只能用
datatype 記 tier。在任何 2D layout viewer 裡它們本來就是疊死的。

**「QFET 的 tier 跟 CFET 的 tier 是同一件事」**
不是。CFET 的 tier *就是* P/N 之分；QFET 的 tier 是晶圓的正面和背面，
**每一面都各自有自己的 PMOS 和 NMOS**。所以 QFET 裡 `11/1` vs `11/2` 差在 Y，
`11/*` vs `511/*` 才是差在 z。

**「立體圖的 z 值是真的」**
不是。見 §4.3。

**「`techs/*.toml` 裡三份現成的就是慣用寫法」**
不是。那三份是機器反推出來的，不用 `gap` 也不用 `span`。慣用寫法看
`_TEMPLATE.toml`。見 §2.4。

**「改 `layers.py` 就能加架構」**
不是。`layers.py` 現在只是載入 `techs/*.toml` 的薄殼，加架構是放一個新的
`.toml`。

---

## 相關文件

| | 內容 |
|---|---|
| `tools/routing-ir/README.md` | 繞線視角 IR 的完整說明與擴充性規則 |
| `tools/stack3d/README.md` | 三種架構對照表、立體圖、遷移動畫 |
| `.claude/skills/stack3d-onboard-tech/` | 帶 agent 走完 onboard 流程的 skill |
| `.claude/skills/domain-handover-ir/` | 設計這類交接格式的通用方法 |
