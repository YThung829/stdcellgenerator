# routing-ir — 繞線視角的製程描述

繞線只需要知道製程的一小部分。這份 IR 就是那一小部分。

實體設計上把製程抽象成大約六類東西 —— **OD · POLY · MD · VIA · M0 · M1** ——
加上每一類坐在什麼格點上，這就是繞線視角的全部。

| | 完整光罩疊構<br>`tools/stack3d/techs/*.toml` | 繞線視角<br>`tools/routing-ir/specs/*.json` |
|---|---|---|
| FinFET | 16 層 | **7 層**（4 個在繞線圖上） |
| CFET | 21 層 | **10 層**（5 個在圖上） |
| QFET | 34 層 | **12 層**（8 個在圖上） |
| 給誰看 | 畫立體圖 | 給 router / solver |
| 包含 well / implant / fin / cut | 是 | 否 —— 跟繞線無關 |

```bash
python tools/routing-ir/routing_ir.py --check          # 驗證所有 spec
python tools/routing-ir/routing_ir.py --show CFET      # 看繞線視角
python tools/routing-ir/routing_ir.py --derive FinFET  # 從 engine 檔案反推草稿
python tools/routing-ir/routing_ir.py --emit MYTECH    # 產生 engine layer JSON 的繞線部分
```

## 最關鍵的一個欄位：`in_graph`

**不是抽象裡的每一層都是繞線圖上的層。** 這是這份 IR 最重要、也最容易被誤解的
一件事：

```jsonc
{ "id": "M0",   "class": "metal", "in_graph": true,
  "direction": "H", "pitch": 24, "offset": 0, "width": 14 }

{ "id": "MD_N", "class": "md", "in_graph": false,
  "access_via": "BPC", "enabled_by": "lisd_routing" }
```

- **`in_graph: true`** —— `LayeredGridGraph` 在這層上有節點，一條繞線就是穿過這些
  節點的路徑。**poly 和所有 metal 屬於這類**，必須給 direction / pitch / offset /
  width，因為那就是 router 用的軌道格點。

- **`in_graph: false`** —— 這層有幾何，但圖上沒有它。**od 和 md 屬於這類。**
  要改寫 `access_via`（router 透過哪個圖層碰到它）和 `enabled_by`（哪個 config
  旗標開關這條路）。

### 為什麼 MD 不是圖層

在這個 engine 裡查證過：`ACTIVE` 和 `LISD` 在 `engine/input/layer/*.json` 裡都是
`layer_type: "gds"`，solver 從頭到尾看不到它們。

「在 MD 上繞線」實際上被模型化成**允許 `poly <-> M0` 的 via 落在 source/drain 欄
上**（見 `core/routing.py` 的 `ban_middle_row_via_for_3T`）。`lisd_routing` 控制
S/D 欄、`lig_routing` 控制 gate 欄。

所以如果 IR 只是把六類東西平鋪成一張清單，會暗示 MD 跟 M0 一樣可以沿著繞 ——
填表的製程工程師就會合理地想給它一個 track pitch，然後整份表就錯了。
`in_graph` 存在就是為了讓這件事講清楚。

## 不會跟 engine 走鐘

`--check` 會拿 spec 裡**每一個** pitch / offset / direction / width / via 鏈，
去跟 `source.layer_json` 指向的真實 engine 檔案對。對不上就 exit 非零：

```
FAIL  QFET: M1.pitch is 30 here but 42.0 in PROBE3_QFET_2F_4T_4242OF21.json
FAIL  QFET: H1.direction is 'H' here but 'V' in PROBE3_QFET_2F_4T_4242OF21.json
FAIL  QFET: PROBE3_QFET_2F_4T_4242OF21.json declares via MIV2 H0->H1 that this spec does not list
```

這樣這份 IR 才是「engine 的一個視角」，而不是另一份會各自演化的平行文件。

## 六個 class

| class | 在圖上 | 意思 |
|---|---|---|
| `od` | 否 | 擴散區。router 只需要知道位置，不會沿著它繞。 |
| `poly` | **是** | 閘極，同時是放置層（電晶體坐在上面）。垂直，走 CPP 格點。 |
| `md` | 否 | 區域互連。`contacts` 分是接 `source_drain` 還是接 `gate`。 |
| `metal` | **是** | 真正的繞線層。M0 / M1 / BM0 / H0 都是這類。 |
| `via` | 是 | 連接兩層 —— 寫在 `connect` 區段。 |
| `virtual` | 是 | 圖上的捷徑、沒有光罩 —— 寫在 `shortcuts` 區段。 |

`class` 只講**語意角色**；層在 `layers` 陣列裡的位置給堆疊順序。所以 QFET 的
`BM0` / `H0` / `M0` 都是 `metal`，不需要為「背面金屬」「層間金屬」各發明一個 class。

## 擴充性

這是明確設計進去的，寫成規則而不是期望：

1. **`schema` 帶主版本號。** 讀取端遇到不認得的主版本必須拒絕；次版本的新增是
   附加式的，忽略掉是安全的。
2. **任何 `x_` 開頭的鍵是廠商自訂欄位。** 讀取端保留但忽略。要加東西先放這裡，
   不用等格式改版。
3. **不認得的 `class` 是警告不是錯誤**，會退化成「不透明幾何」處理。新製程可以
   命名這個檔案沒聽過的東西，不必先等這個檔案更新。
4. **`tiers` 是任意長度的清單。** 沒有任何地方假設只有一層或兩層。
5. 除了 `name` / `schema` / `layers`，**每個區段都是選填**，且有記錄在案的預設值。

## 怎麼填一份新的

複製 `specs/_TEMPLATE.json`。裡面有完整的填寫說明、class 一覽、和一個可以直接
通過驗證的範例。

不知道 GDS 層號填什麼的話，先解一顆 cell，然後：

```bash
python tools/stack3d/inspect_tech.py --name MYTECH --gds <解好的.gds>
```

已經有 engine 檔案的話，可以直接反推草稿再手動補上 od / md：

```bash
python tools/routing-ir/routing_ir.py --derive MYTECH > specs/mytech.json
```

`--derive` 只會產出**圖上的層**（poly 和 metal），因為 od / md 在 engine 的
layer JSON 裡根本不存在。那兩類要自己加，這也正好是需要人判斷的部分。

## 反過來：從 IR 產生 engine 檔案

```bash
python tools/routing-ir/routing_ir.py --emit MYTECH > new_layer.json
```

產生 layer JSON 的 **metal / via / virtual** 部分。`gds`-only 的層
（well / implant / fin / cut）依定義不在繞線視角裡，要另外補上 GDS writer 才能跑。
指令會把這件事印在 stderr 提醒你，而不是給你一個看起來完整、其實不完整的檔案。

## 跟其他兩個 IR 的關係

這個 repo 現在有三份製程相關的描述，各自有明確分工：

| 檔案 | 描述什麼 | 給誰 |
|---|---|---|
| `tools/routing-ir/specs/*.json` | **繞線需要知道的最小集合** | router / solver |
| `tools/stack3d/techs/*.toml` | 完整光罩疊構與 z 模型 | 立體圖 |
| `tools/stack3d/migrations/*.json` | 兩個製程之間的層對應 | 遷移動畫 |

三份怎麼填、怎麼讀，以及哪些欄位機器會驗、哪些是人為斷言，見 [`docs/process-ir.md`](../../docs/process-ir.md)。

三份都可以被機器驗證，也都由製程工程師填得起來。長遠來看繞線視角這份可以成為
上游（`--emit` 已經是那個方向的第一步），但目前三份各自對著 engine 的真實檔案
驗證，不會互相矛盾。
