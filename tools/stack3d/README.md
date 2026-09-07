# stack3d — 三種製程架構的立體剖析

一個獨立的教學用工具：把 engine 實際解出來的 cell layout 拆成 3D 堆疊，用來看懂
**FinFET / CFET / QFET** 三種架構在 z 軸上到底差在哪。

輸出是一個單檔 HTML（`smtcell-stack.html`），可以直接用瀏覽器開，或發布成 artifact。

```
engine/input/layer/*.json  ─┐
engine/input/presets/*.mk  ─┤
data/solved/<tech>/*.res     ─┼─> payload ─> template/{head,body,app.js} ─> smtcell-stack.html
data/solved/<tech>/*.gds     ─┘      ▲
                              layers.py（z 模型）
```

## 為什麼需要這個

engine 是 2D 的。`input/layer/*.json` 只描述平面上的 `direction` / `pitch` /
`offset` / `width`，GDS 也是平面格式 —— 整個 repo 裡**沒有任何一層帶厚度資訊**。

這在 CFET 上特別要命：`N_ACTIVE` (11/2) 和 `P_ACTIVE` (11/1) 的 x/y 邊界一模一樣
（`x[15.5, 74.5] y[19, 125]`），因為 CFET 的兩顆元件本來就佔同一塊面積、只差在 z。
GDS 只能用 datatype 區分，在任何 2D layout viewer 裡它們是完全疊死的。這個工具補上
z 軸，把差別攤開。

## 怎麼用

```bash
# 1) 重建頁面（只需要 klayout；不會重跑 solver）
python tools/stack3d/build.py

# 2) 從已 commit 的 .res 重新產生 GDS（需要 klayout）
python tools/stack3d/build.py --gds

# 3) 從頭重解三個 cell 再重建（需要 engine 全部相依套件）
python tools/stack3d/build.py --solve --gds
```

`data/solved/<tech>/INV_X1.{res,gds}` 是 commit 進來的，所以在一個乾淨的 checkout 上
只要有 `klayout` 就能重建頁面，不必裝 ortools、不必等 solver。

相依套件：

| 指令 | 需要 |
|---|---|
| `build.py` | `klayout` |
| `build.py --gds` | `klayout` |
| `build.py --solve` | `ortools` `klayout` `networkx` `loguru` `matplotlib` `scikit-learn` |

頁面本身在執行期從 cdnjs 載 three.js r128（UMD），字型從 Google Fonts 載。
離線看的話把那兩個 `<link>` / `<script>` 換成本地檔即可。

## 檔案

| 檔案 | 職責 |
|---|---|
| `build.py` | 主流程：（可選）重解 → （可選）重產 GDS → 組 payload → 組 HTML |
| `layers.py` | **z 模型**：每個 tech 的層堆疊表、顏色、群組、透明度 |
| `dump_gds.py` | 讀 GDS，把每個 shape 依 `(layer, datatype)` dump 成 nm 座標 |
| `inspect_tech.py` | 幫你把一個架構的事實挖出來：LGG z 順序、via 鏈、虛擬邊、GDS-only 層、writer 用到的 layer/datatype、以及跟 `layers.py` 的涵蓋率 diff |
| `template/head.html` | `<title>` + 全部 CSS |
| `template/body.html` | 頁面骨架與說明文字 |
| `template/app.js` | three.js 場景、圖層側欄、互動 |
| `data/solved/<tech>/` | 已 commit 的 `.res` / `.gds`，讓重建不必重解 |
| `data/stack3d.json` | 產生出來的 payload（內嵌進 HTML，這裡另存一份方便 diff） |
| `smtcell-stack.html` | 建好的單檔頁面 |

## 哪裡是真的、哪裡是補的

| | 來源 |
|---|---|
| 多邊形 x / y 邊界 | **真實** — 由 `dump_gds.py` 從 `.gds` 抽出，只做 dbu → nm 換算 |
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
