# AiDigital 數位學伴教學站

這是一個給國小數位學伴課程使用的本機互動教學網站，包含英文、數學與活動遊戲頁面。部分活動支援 SQLite 紀錄，例如「認識小數」可讓學生端作答、老師端即時查看完成率與錯題。

## 本機啟動

```bash
python3 scripts/travel_planner_server.py
```

啟動後可開啟：

```text
http://127.0.0.1:4174/
```

若要讓同一個 Wi-Fi 內的學生裝置連線，請使用老師電腦的區網 IP，例如：

```text
http://192.168.x.x:4174/
```

## 專案結構

- `index.html`：活動總覽入口
- `src/`：各活動頁面的互動邏輯與資料
- `styles/`：網站整體樣式
- `scripts/travel_planner_server.py`：本機伺服器與 SQLite API
- `data/`：本機資料庫位置，正式上傳 GitHub 時不包含實際資料庫

## GitHub 上傳提醒

`.gitignore` 已排除本機資料庫、暫存檔、輸出文件與教材來源，避免把學生紀錄或教材檔案誤上傳到公開倉庫。
