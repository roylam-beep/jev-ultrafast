# handovers/

跨 session 交接單，**一 session 一份**：`handovers/<session-slug>.md`。

- 第一行固定 `基準：<HEAD 短 SHA> @ <ISO 時間>`——落差靠它算。
- 同名已存在就加 `-2`／`-3` 後綴，**不覆寫別人那份**。
- 只寫自己那份，接手完成**只刪自己接的那份**。
- 三節：①狀態一句話＋未完成與下一步（卡在哪、從哪個檔案接）②工作區（分支、未提交檔、
  push 狀態）③下一輪 kickoff prompt（code fence）。本輪完成了什麼屬歷史，
  寫 `docs/archive/rounds.md`，不寫這裡。
- **禁寫易變斷言**：會過期的事實一律寫成查詢指令，不寫成結論。

三節骨架的權威源是 `/cc-close`；不一致時以該 skill 為準。
