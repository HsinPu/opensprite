---
name: memory
description: 雙層記憶系統，長期記憶會自動載入上下文。
always: true
---

# 記憶

## 結構

- `memory/{chat_id}/MEMORY.md` — 長期事實（偏好、專案關係）。每次對話會自動載入。
- `memory/{chat_id}/HISTORY.md` — 流水帳，不會載入。

## 什麼時候更新 MEMORY.md

用 `edit_file` 或 `write_file` 立即寫入重要事實：
- 用户偏好（「我偏好深色模式」）
- 專案上下文（「API 使用 OAuth2」）
- 重要資訊（記住用户告訴你的事情）

## 自動整合

當對話超過 30 則訊息時，會自動摘要舊對話並寫入 MEMORY.md。
