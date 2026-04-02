---
name: agent-creator-design
description: 規範 system prompt 的設計原則、metadata、四段式寫法與自檢清單。
always: true
version: "1.0"
scope: prompt-design
language: zh-TW
---

# System Prompt 撰寫規範與寫法

本 skill 規範 **system prompt**（agent 的指令主體）的設計原則與內文寫法。不涉及 Cursor Skill／Subagent 的建立或存放位置。

若將 system prompt 寫成檔案，請先在檔案最上方寫 **metadata**，再開始正文。

---

## 一、設計原則（Design Principles）

### 1.1 單一職責（Single Responsibility）

- 每份 system prompt **只**定義**一個角色**或**一類任務**／一個明確 **workflow**。
- 避免「什麼都能做」的通用型描述；職責越具體，行為越可預期。

**Example**  
❌ 「你是一個程式助手，可以協助開發與除錯」  
✅ 「你是 **code reviewer**。被呼叫時，針對已修改的程式做品質與安全檢視，並產出 **checklist**。」

### 1.2 命名規範（Naming Convention）

- 使用 **lowercase + hyphen**（如 `code-reviewer`、`api-doc-generator`）；僅供內部時可採繁體中文，同一專案內風格一致。
- 名稱長度 <= 64 字元、語意清楚；避免 `helper`、`utils`、`tools` 等籠統命名。
- 存成檔案或標題時，命名需與職責對應，利於辨識與維護。

### 1.3 Metadata（先寫在最上方）

- 將 system prompt 存成檔案或模組時，**先在最上方寫 metadata**，再開始正文，方便辨識、版本與維護。
- `metadata` 建議使用 **YAML frontmatter**，並固定放在檔案開頭。
- **必填**：`name`、`description`、`always`。
- `always` 使用布林值；本規範中的寫法固定為 `true`。
- **選填**：`version`、`scope`、`language` 等；同一專案內格式一致。

**Example（YAML frontmatter）**

```yaml
---
name: code-reviewer
description: 針對已修改的程式進行品質與安全檢視，產出檢查清單。於 code review 或提交前觸發。
always: true
version: "1.0"
scope: code-review
---
```

---

## 二、寫法規範（Writing Guidelines）

### 2.0 內文結構（四大項）

System prompt 內文**只分四大項**，依序為：

`metadata` 不算在這四大項內；它固定放在檔案最上方。

| 項目 | 說明 |
|------|------|
| **角色（Role）** | 一句話定義「你是誰」—模型扮演的身分或角色。 |
| **任務（Task）** | 要完成的具體工作、步驟或流程；可步驟化、可執行。 |
| **規範（Constraints）** | 必須遵守的規則、限制、格式或禁忌（術語一致、精簡、避免的寫法等）。 |
| **輸出（Output）** | 產出形式、格式、範例或 **template**（長什麼樣子、放在哪裡）。 |

依此四項分段撰寫，不額外擴充大類；細節放在各項之下即可。

### 2.1 指令與步驟（Instructions & Steps）

- 用**步驟化**方式撰寫流程，必要時加上簡短 **checklist**。
- 每一步須**可執行**：模型或使用者能依文意直接操作。
- 條件分支時使用「若……則……否則……」等明確句式。

### 2.2 精簡與脈絡（Brevity & Context）

- 只寫真正需要的資訊，避免冗長背景說明；**context** 與 **token** 皆有限。
- 內容較長時，核心放前面，次要細節往後或分段；必要時以「詳見……」引用外部文件（**單層引用**即可）。

### 2.3 術語一致（Terminology Consistency）

- 同一份 prompt 內，同一概念**只使用一個詞**（例如只用「API 端點」或只用 `route`，不混用）。
- 「欄位」、「參數」、「選項」等用語全文一致。

### 2.4 範例與模板（Examples & Templates）

- 需產出固定格式時，在 prompt 中**提供具體範例或 template**，優於抽象描述。
- 範例須可直接套用或微調，避免僅有「請參考類似範例」的模糊指引。

### 2.5 路徑與指令（Paths & Commands）

- 檔案或路徑使用**正斜線**（如 `scripts/helper.py`），避免 Windows 反斜線。
- 執行指令依使用者環境撰寫（例如 Windows 用 `./script.ps1` 或 `python scripts/xxx.py`）。

### 2.6 避免的寫法（Anti-patterns）

- **過多選項並列**：優先給一個建議做法，必要時再說明替代方案。
- **具時效的絕對時間**：如「2025 年 8 月前請用舊 API」易過期；改為「目前作法」與「舊版／deprecated」分開說明。
- **模糊的角色描述**：如「你是小幫手」「你是工具」；改為具體角色與任務（例如「你是 API 文件產出者，根據程式碼註解產出 OpenAPI 規格」）。

---

## 三、實作檢查清單（Checklist）

撰寫或修改 system prompt 時，依下列項目自檢：

- [ ] 職責單一，可一句話說明「這個 prompt 要模型做什麼」
- [ ] 命名符合規範（lowercase、hyphen 或一致風格，語意清楚）
- [ ] 存成檔案時已在**最上方**加上 **metadata**（至少含 `name`、`description`、`always`；選填 `version`／`scope` 等）
- [ ] 內文僅分四大項：角色、任務、規範、輸出
- [ ] 流程步驟化、可執行
- [ ] 術語一致、無冗長重複
- [ ] 必要處有範例或 template
- [ ] 路徑與指令符合使用環境（如 Windows）
- [ ] 無時效性絕對日期、無過多並列選項、無模糊角色描述
