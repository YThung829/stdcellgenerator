# stack3d — 三種製程架構的立體剖析

一個獨立的教學用工具：把 engine 實際解出來的 cell layout 拆成 3D 堆疊，用來看懂
**FinFET / CFET / QFET** 三種架構在 z 軸上到底差在哪。

輸出是一個單檔 HTML（`smtcell-stack.html`），可以直接用瀏覽器開，或發布成 artifact。

```
engine/input/layer/*.json  ─┐
engine/input/presets/*.mk  ─┤
data/solved/<tech>/*.res     ─┼─> payload ─> template/{head,body,app.js} ─> smtcell-stack.html
data/solved/<tech>/*.gdstxt  ─┘      ▲
                              layers.py（z 模型）
```

## 為什麼需要這個

engine 是 2D 的。`input/layer/*.json` 只描述平面上的 `direction` / `pitch` /
`offset` / `width`，GDS 也是平面格式 —— 整個 repo 裡**沒有任何一層帶厚度資訊**。

這在 CFET 上特別要命：`N_ACTIVE` (11/2) 和 `P_ACTIVE` (11/1) 的 x/y 邊界一模一樣
（`x[15.5, 74.5] y[19, 125]`），因為 CFET 的兩顆元件本來就佔同一塊面積、只差在 z。
GDS 只能用 datatype 區分，在任何 2D layout viewer 裡它們是完全疊死的。這個工具補上
z 軸，把差別攤開。

## 三種製程架構

以下每一欄都可以從 repo 的檔案重新推導出來，`audit.py` 會逐項驗證（見下節）。
**唯一的例外是立體圖裡的 z 厚度，那是示意值。**

| | **FinFET** | **CFET** | **QFET** |
|---|---|---|---|
| 幾何本質 | 單層平面，1 個放置層 | 垂直堆疊，2 個放置層共用同一塊平面面積 | 晶圓正面 + 背面，2 個放置層 |
| `placement_layer_names` | `{PC}` | `{PC, BPC}` | `{PC1, BPC1}` |
| `default_placement_layer` | `PC` | `PC` | `PC1` |
| `pin_access_layer_names` | `{M0}` | `{BPC, M0}` | `{BM0, M0}` |
| **P / N 怎麼分** | 靠 **row**：NMOS row 0、PMOS row 2 | 靠 **tier**：`stacking_config` = `P_on_N`（預設）或 `N_on_P` | 靠 **row**（同 FinFET）。tier 是正/背兩個晶圓面，每面各有 PMOS 和 NMOS |
| LGG z 順序<br>（= metal 依 `layer_number` 排序，也是 `.res` 的 `MET` 欄） | `PC · M0 · M1 · M2` | `BPC · PC · M0 · M1 · M2` | `BM1 · BM0 · BPC1 · H0 · H1 · PC1 · M0 · M1` |
| Via 鏈 | `CA` PC→M0<br>`V0` M0→M1<br>`V1` M1→M2 | `BCA` BPC→M0<br>`CA` PC→M0<br>`V0`、`V1` | `BV0` BM1→BM0<br>`BCA1` BM0→BPC1<br>`MIV1` BPC1→H0<br>`MIV2` H0→H1<br>`MIV3` H1→PC1<br>`CA1` PC1→M0<br>`V0` M0→M1 |
| 虛擬邊 | 無 | `BPC ↔ M0`，method `boundary`，寫死在 `_init_graph` | `VL1: BPC1 ↔ PC1`，method `overlap`，由 layer JSON 的 `"layer_type":"virtual"` 宣告 |
| MOL 當繞線資源 | 可選（`lig_routing`/`lisd_routing` 預設 false） | **強制開啟**（`config.py` 見到 `tech=="CFET"` 就設 true） | 由 preset 的 `CONFIG_OVERRIDES` 打開 |
| 有 LIG 層？ | 有 | 有 | **沒有** —— `CA1` 直接落在 `PC1` 上 |
| 最上層金屬 | M2 | M2 | M1（另有背面 BM0/BM1） |
| IO pin 層（JSON `io_pin`） | 未標記 | 未標記 | `BM0`、`M0` |
| 支援 track 數 | 3, 4 | 2, 3, 4 | 2, 3, 4 |
| 支援 height config | 只有 `SH` 有實作<br>（`PNNP`/`NPPN` 會 raise） | 只有 `SH` | 只有 `SH` |
| 內附 preset | `FinFET_4T_SH`<br>CPP 45 / M1P 30 / OF 0 | `CFET_4T_SH`<br>CPP 45 / M1P 30 / OF 0 | `QFET_4T_SH`<br>CPP 42 / M1P 42 / OF 21 |
| INV_X1 解出來的 cell | 90 × 144 nm，obj 1021 | 90 × 144 nm，obj 1034 | 84 × 144 nm，obj 999 |

### 三個最容易講錯的地方

**1. CFET 的兩顆元件在 GDS 裡是完全疊死的。**
`P_ACTIVE` (11/1) 和 `N_ACTIVE` (11/2) 的座標一模一樣 —— 都是
`x[15.5, 74.5] y[19, 125]`。這不是 bug，這就是 CFET：兩顆元件共用同一塊平面
面積，只差在 z。GDS 是平面格式，只能用 datatype 記錄 tier。所以在任何 2D
layout viewer 裡它們是重疊的，立體圖才把差別攤開。

**2. CFET 的 gate 只有一個圖層，不是上下各一層。**
GDS 裡只有 `7/0`（這顆 INV 上是 3 根柱子，每個 CPP 一根）。實體上 CFET 的
gate stack 本來就穿過上下兩顆元件，所以 writer 不能把它拆成兩層。立體圖裡
它的 z 區間橫跨兩個 tier，並設成半透明，否則會把它要解釋的 tier 整個擋住。

**3. QFET 的 tier 跟 CFET 的 tier 不是同一回事。**
CFET 的 tier *就是* P/N 之分；QFET 的 tier 是晶圓的正面和背面，**每一面都各自
擺得下 PMOS 和 NMOS**（`_compute_placement_row_indices` 給的是 NMOS row 0 /
PMOS row 2，跟 FinFET 一樣）。所以 QFET 裡 `11/1` vs `11/2` 差在 Y，
`11/*` vs `511/*` 才是差在 z。

由此推出背面 tier 是正面的**鏡像**：`BCA1` 宣告是 `BM0 → BPC1`，而 `BM0` 的
`layer_number` 比 `BPC1` 小（在下面），所以背面元件的接觸往**下**走 ——
`BLISD1` 在背面 diffusion 底下，正面的 `LISD1` 則在 diffusion 上面。

### 兩個已查證的限制

**CFET 上下 tier 之間在 GDS 裡沒有任何幾何。**
`gds_CFET_SH.py` 對 `BPC → PC` 的線段明確 `pass`（註解：*Handled by CFET
stacking, no physical via needed*）—— 上下兩顆元件的 source/drain 靠堆疊本身
相連，不需要實體 via。所以繞線表裡的 `MET 0 => 1` 畫出來是零個多邊形，立體圖上
下層的 `N_LISD` 和上層的 `P_LISD` 看起來不相連。**這是 GDS 的實情，不是畫錯。**

對照組：QFET 的層間路徑 `MIV1/2/3` 有自己的 GDS 層（5000-5002），有用到就畫得
出來。這是兩種架構在「層間連接怎麼記錄」上的真實差異，值得在說明時點出來。

**CFET 的 `14/0` 同時承載兩種高度的接觸。**
layer JSON 裡 `BCA`（BPC→M0）**沒有 `gds_layer`**，writer 用跟 `CA`（PC→M0）
同一支 `__ca__()` 畫在 `14/0`。內附的 `INV_X1` 沒有任何 `MET 0 => 2` 線段
（可在 `.res` 的繞線表確認），所以 `14/0` 全部是上層 tier 的接觸加電源軌接觸，
目前的 z 區間正確。但換一顆用到 BPC→M0 的 cell，那個接觸實體上高得多卻落在同一
GDS 層，立體圖會把它畫在上層 tier 的高度。要處理的話得改成從 `.res` 分辨，而不是
只看 GDS。

### 附註

- 內附的 `INV_X1` 太小，QFET 的兩顆電晶體都落在正面 `PC1`（`.res` 的 `Z` 欄可以
  看到），所以背面元件層在立體圖裡是空的，側欄以灰字斜體列出。這是真實結果，
  不是漏畫。解一顆 `NAND2_X1` 就會看到兩面都被用到。
- QFET 的 `VL1` 是**圖上的邊，不是光罩** —— 沒有幾何。GDS 只有加
  `--draw-virtual` 才會把它畫在 debug layer 700 上，走它要付
  `virtual_edge_cost`（預設 5，比 metal 的 1 和 via 的 3 貴）。

## 檢查正確性

```bash
python tools/stack3d/audit.py
```

逐項比對立體圖宣稱的每件事跟 engine 的來源檔案：堆疊順序是否符合 layer JSON 的
`layer_number` 排序、每個 via 是否真的夾在它宣告的兩層之間、註解裡引用的
pitch/offset/direction 是否跟 JSON 一致、有沒有畫了卻沒列進表的層、
CPP/cell 尺寸/objective 是否能從 preset 和 `.res` 重新推導。
有任何一項不符就 exit 非零。

**它也會提醒你：z 厚度是唯一無法推導的東西。** 這點在對外說明時要講清楚。

## 怎麼用

```bash
# 1) 重建頁面 —— 只用 Python 標準函式庫，不需要任何套件
python tools/stack3d/build.py

# 2) 從已 commit 的 .res 重新產生 GDS 和它的文字檔（需要 klayout）
python tools/stack3d/build.py --gds

# 3) 從頭重解三個 cell 再重建（需要 engine 全部相依套件）
python tools/stack3d/build.py --solve --gds
```

`data/solved/<tech>/INV_X1.{res,gdstxt}` 是 commit 進來的，**都是純文字**，所以在
一個乾淨的 checkout 上不用裝任何東西就能重建頁面。

相依套件：

| 指令 | 需要 |
|---|---|
| `build.py` | **無**（Python 標準函式庫） |
| `audit.py` | **無** |
| `gdstext.py encode` / `decode` | `klayout` |
| `build.py --gds` | `klayout` |
| `build.py --solve` | `ortools` `klayout` `networkx` `loguru` `matplotlib` `scikit-learn` |

### 沒有二進位檔

GDS 是二進位格式，有些環境只讓純文字進出。所以 commit 進 repo 的是
`.gdstxt` —— 一個行導向的文字檔，座標已經正規化成 nm：

```
VERSION 1
DBU 0.00025
CELL INV_X1
BOX  15/0 0 -18 90 18
TEXT 15/0 45 0 VSS
```

`build.py` 直接讀它，所以整條重建路徑沒有二進位輸入、也不需要 klayout。
要在 KLayout 裡打開時再轉回去：

```bash
python tools/stack3d/gdstext.py decode data/solved/cfet/INV_X1.gdstxt out.gds
python tools/stack3d/gdstext.py verify out.gds data/solved/cfet/INV_X1.gdstxt   # 確認無損
```

`.gds` 本身被 `.gitignore` 排除（是建構產物）。`build.py --gds` 重產 GDS 後會
自動重新編碼 `.gdstxt`，兩者不會走鐘。

頁面本身在執行期從 cdnjs 載 three.js r128（UMD），字型從 Google Fonts 載。
離線看的話把那兩個 `<link>` / `<script>` 換成本地檔即可。

## 檔案

| 檔案 | 職責 |
|---|---|
| `build.py` | 主流程：（可選）重解 → （可選）重產 GDS → 組 payload → 組 HTML |
| `layers.py` | **z 模型**：每個 tech 的層堆疊表、顏色、群組、透明度 |
| `dump_gds.py` | 讀 GDS，把每個 shape 依 `(layer, datatype)` dump 成 nm 座標 |
| `gdstext.py` | GDS ↔ 純文字的雙向轉換（`encode` / `decode` / `verify`） |
| `audit.py` | 逐項比對立體圖的宣稱與 engine 來源檔案 |
| `inspect_tech.py` | 幫你把一個架構的事實挖出來：LGG z 順序、via 鏈、虛擬邊、GDS-only 層、writer 用到的 layer/datatype、以及跟 `layers.py` 的涵蓋率 diff |
| `template/head.html` | `<title>` + 全部 CSS |
| `template/body.html` | 頁面骨架與說明文字 |
| `template/app.js` | three.js 場景、圖層側欄、互動 |
| `data/solved/<tech>/` | 已 commit 的 `.res` / `.gdstxt`（純文字），讓重建不必重解也不必裝套件 |
| `data/stack3d.json` | 產生出來的 payload（內嵌進 HTML，這裡另存一份方便 diff） |
| `smtcell-stack.html` | 建好的單檔頁面 |

## 哪裡是真的、哪裡是補的

| | 來源 |
|---|---|
| 多邊形 x / y 邊界 | **真實** — 由 `dump_gds.py` 從 `.gds` 抽出（只做 dbu → nm 換算），再由 `gdstext.py` 無損編碼成 `.gdstxt` |
| 層與層的先後順序 | **真實** — 依 layer JSON 的 `layer_number`（即 `LayerStack` 排序後的 metal 順序，也就是 LGG 的 z-index）與 via 的 `lower_layer → upper_layer` 鏈 |
| 目標值、cell 尺寸、CPP/M1P/OF、LGG z 順序 | **真實** — `build.py` 分別從 `.res` 首行、`100/0` BOUNDARY 多邊形、preset `.mk`、layer JSON 讀回來 |
| 每層的 z 起點與厚度 | **示意值，在 `layers.py` 裡手設。** repo 沒有厚度資料，只保證*順序*與*誰跨越誰*正確，不可當 PDK 數值使用 |

`dump_gds.py` 為什麼要正規化：三個 writer 用了不同的 `SCALE` / `dbu` 組合，但乘出來一樣，
所以除以 `dbu * 1000` 之後三份 GDS 會落在同一個 nm 空間，才能並排比較。

| writer | SCALE | dbu | 每單位 |
|---|---|---|---|
| `gds_FinFET_SH.py` | 4 | 0.00025 | 0.001 µm |
| `gds_CFET_SH.py` | 10 | 0.0001 | 0.001 µm |
| `gds_QFET_SH.py` | 4 | 0.00025 | 0.001 µm |

## 可移植性

engine 位置和 cell 名稱都沒有寫死，所以這個工具可以搬到 engine 在別處的 fork：

```bash
python tools/stack3d/build.py --engine /path/to/engine --cell NAND2_X1
# 或用環境變數
export STACK3D_ENGINE=/path/to/engine
export STACK3D_CELL=NAND2_X1
```

`--tech NAME` 可以只重建單一架構（onboard 新架構時迭代很有用）。

## 加一個新的製程架構

有一個 skill 專門帶這件事：`.claude/skills/stack3d-onboard-tech/`。
跟 agent 說「把 <架構名> 加到 stack3d」它就會走完整流程。手動的話：

```bash
# 1) 把事實挖出來（LGG z 順序、via 鏈、writer 用到哪些 layer/datatype）
python tools/stack3d/inspect_tech.py --name MYCFET

# 2) 在 layers.py 加一張堆疊表 + 一筆 TECHS，然後解一顆小 cell
python tools/stack3d/build.py --tech MYCFET --solve --gds

# 3) 拿實際產出的 GDS 對一次涵蓋率，補完 z 值
python tools/stack3d/inspect_tech.py --name MYCFET --gds data/solved/mycfet/INV_X1.gds

# 4) 重建並實際打開來看
python tools/stack3d/build.py
```

`build.py` 完全不用改 —— 如果你發現自己在改它，多半是找錯擴充點了。
它也會在有幾何、但 `layers.py` 沒有列到的層出現時警告你（那是最容易做出誤導圖的失誤）。

`layers.py` 的 `TECHS` 每筆欄位的意義寫在該檔案的註解裡。

## 已知限制

- 每個 polygon 是用 bounding box 畫成長方體。三個 writer 目前只 insert `pya.Box`，
  所以現在等價；哪天有非矩形的 shape 就會失真。
- QFET 的背面元件層（`ACTIVE_BACK_*` / `BSDT1` / `BLISD1`）在 `INV_X1` 是空的 ——
  這顆反相器兩顆電晶體都被放到正面 `PC1`。側欄仍會列出，以灰字斜體標示。
- 頁面沒有 light theme，刻意單一深色（layout viewer 的慣例）。
