# ICEBERG — 從 BACKLOG 逐出的項目

BACKLOG 滿載時整行搬進來，**不改寫**，加一字理由：
`done`（已做）／`stale`（過期）／`absorbed`（被別的改動吸收）／`deferred`（還想做，沒排到）。

**撈回規則**：只有 `deferred` 與 `stale` 可以撈回 BACKLOG，且要重寫成當下仍成立的一行。
`done` 與 `absorbed` 不撈回。撈回率的分母是 `deferred` ＋ `stale`，不是全部條目。

| # | 一行 | 理由 | 逐出日 |
|---|---|---|---|
