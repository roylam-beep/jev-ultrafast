---
description: 本 repo 的實作慣例與已驗證事實
paths: jev_ultrafast/**, tests/**, scripts/**
---

<!-- paths 由 /cc-harness 偵測填入：本 repo 是 pyproject.toml 型，但套件目錄是
     jev_ultrafast/（非樣板預設的 src/ 或 lib/），故填實際存在的三個目錄。 -->

# 實作慣例

<這個 repo 寫 code 的慣例。只寫「不知道的人會踩到」的那幾條。
 硬性契約在 AGENTS.md，本檔只放「寫 code 時才會碰到」的細節。>

## 已驗證事實

### 記錄容器偵測（2026-09-19 實測 momoshop 與 591）

用本專案 `Browser` 對真實頁面量測，0 次 model call。三種判準擇一：

| 判準 | momo（137 段） | 591（133 段） |
|---|---|---|
| 同 tag、最內層 | 碎片化，名稱與價格分屬 `DIV.prdNameTitle` 與 `DIV.money` | 碎片化 |
| 同 tag ＋同 class、最內層 | 80 → `LI.listAreaLi` ✓ | 36 → `DIV.ware-item` ✓ |
| 同 tag ＋同 class、最外層 | 121 → `LI.listAreaLi` | **133 段全塌進一個無 class `DIV`** ✗ |

結論：只有「同 tag ＋同 class、取最內層」兩站都成立，最外層在 591 完全失效。

單段容器必須退回鏈上下一個祖先，不能只是丟棄：momo 的 `SPAN.mu-inline-block` 是 41 容器 /
41 段（平均 1.0），丟棄會讓 17 個 `LI.listAreaLi` 中的 4 個掉到門檻下被誤殺。

`identity()` 的 WeakMap id 跨 observe 穩定：同一頁連續讀兩次，210 個容器 id 全同、
`cache.next` 增量 0；捲動後只有新進視野的元素配新號。這是把 id 寫進 `text`（而 `text`
在 freshness marker 內）的前提。

標記字元 `⟦⟧`（U+27E6/27E7）兩站實測出現 0 次；`【】` 在 momo 出現 12 次、
`｜` 在 591 出現 3 次，都不可用。

`text` 的 6000 字元上限離用滿很遠（momo 1448、591 1417），因為只收集 viewport 內的文字節點，
所以標記開銷不需要調整預算。

<其他實測過才寫進來的東西：API 行為、欄位語意、設定鍵。每條帶一句怎麼測出來的。
推測與文件轉述不寫這裡。>
