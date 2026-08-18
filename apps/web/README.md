# Tab 1 — 開發區前端

Vite + React + TypeScript + TanStack Query + Tailwind v4。

```bash
npm install
npm run dev      # http://localhost:5173
```

需要後端在 <http://localhost:8000>(見 `services/api/README.md`)。
API 位址可用 `VITE_API_BASE` 覆蓋。

連接埠固定在 5173 且 `strictPort`:那是 API 預設允許的 CORS 來源,讓 Vite 自動換
埠只會讓每一支 API 呼叫靜靜地失敗。

## 這個頁面在做什麼

單一工作區,sandbox id 固定是 `workspace`。這不是圖方便——opencode 以絕對工作目錄
界定 project,而後端由 id 推導出該路徑,所以固定 id 正是重建後還能接回舊對話的原因。

`POST /api/sandboxes` 是唯一的「把它準備好」呼叫:沒有就建立、已經在跑就原樣回傳、
掉了就從最近一份快照重建。只有手動暫停之後才需要 resume。

**開機時不能相信 `state`。** 儲存的紀錄活得比服務行程久,所以 API 重啟後它仍寫著
`running`、仍帶著上一個 proxy 埠——一個沒有人在聽的埠。只有 `GET /api/sandboxes/{id}`
回報的 `healthy` 能區分這兩者,所以那才是開機判斷的依據。

iframe 永遠指向 `proxy_url`,不指向沙盒本身:代理在伺服器端注入憑證,而 opencode 的
UI 用 root-absolute 路徑載資產,無法掛在本 app 的子路徑下。因此 iframe 與外殼是不同
來源——嵌入不需要 CORS,frame 內部的請求仍是同源。
