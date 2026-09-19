# Jev Ultrafast

## 回覆語言（最優先，凌駕本檔其餘所有規則）

**一律使用台灣繁體中文回覆使用者。**

- 不使用英文回覆，不使用簡體中文，不使用中國大陸用語。
- 大陸用語 → 台灣用語對照：
  - 軟件→軟體、硬件→硬體、數據→資料、數據庫→資料庫、網絡→網路、內存→記憶體、緩存→快取、屏幕→螢幕、視頻→影片
  - 程序→程式、代碼→程式碼、函數→函式、變量→變數、數組→陣列、對象→物件、字符串→字串、指針→指標、接口→介面、實例→實體
  - 線程→執行緒、進程→行程、調試→偵錯、報錯→出錯、登錄→登入、默認→預設、文件夾→資料夾、集成→整合、打印→列印、質量→品質
- 這條規則不隨對話內容的語言改變：使用者用英文提問、程式碼是英文、錯誤訊息是英文、README 是英文，回覆仍然一律台灣繁體中文。
- 技術名詞、指令、識別名、檔案路徑、環境變數、API 參數保持原文，不翻譯（例：`uv run pytest`、`TYPE_TEXT`、`TYPESAFE_ENDPOINT`、`validate_choice`）。
- 時間估算以 AI 執行的輪數（session）為單位，不用人類手工工時換算。

### 寫進 repo 的產出物維持英文

commit message、PR 標題與內文、程式碼識別名、code comment、README.md、docs/ 一律英文，與既有內容保持一致。例外：`.env.example` 的設定說明維持繁體中文。

## 工程規則

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- The policy chooses an operation and operation-specific target heads in one request, whether it runs on TypeSafe's choice API or on a chat model. Consume only the selected operation's target.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text LLM. Cache a stale retry's value only while its entire helper input is identical.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep examples, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check jev_ultrafast/static/app.js, uv build.

## Agent contract (cc-harness)

Signposts only; the rule text lives where it is used.

- **Round close** — `/cc-close`.
- **BACKLOG queue** — header of [BACKLOG.md](BACKLOG.md); evictions go to `docs/archive/ICEBERG.md`.
- **Hooks** — `.git/hooks/pre-commit` runs `scripts/check_docs.py`. Not version-controlled; reinstall with `/cc-harness`.
- **Decisions** — none yet; create `docs/decisions.md` with the first one.
- **Implementation notes** — `.claude/rules/implementation.md` (`jev_ultrafast/**`, `tests/**`, `scripts/**`).
