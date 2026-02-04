# MCP ServerInstructions 延遲載入工具搜尋功能

本文件說明 Serena 如何實現 MCP ServerInstructions 的延遲載入工具搜尋功能，以減少初始載入的工具數量並提供工具搜尋機制讓客戶端按需發現工具。

## 目錄

- [背景與動機](#背景與動機)
- [功能概述](#功能概述)
- [架構設計](#架構設計)
- [實現細節](#實現細節)
- [使用方式](#使用方式)
- [配置選項](#配置選項)
- [API 參考](#api-參考)

## 背景與動機

在某些情境下，MCP 客戶端（如某些 LLM 提供者）對工具定義的 token 數量有限制。Serena 提供了超過 40 個工具，這可能導致：

1. 初始載入時間較長
2. 超出某些客戶端的 token 限制
3. LLM 在工具選擇時面臨過多選項

延遲載入功能解決了這些問題，透過：

- 初始僅載入核心工具（約 7 個）
- 提供 `search_tools` 工具讓 LLM 按需發現其他工具
- 在系統提示中告知 LLM 如何使用工具搜尋功能

## 功能概述

### 核心組件

1. **工具分類系統** (`ToolCategory`, `ToolCategoryRegistry`)
   - 將工具分為 8 個類別：file_operations, symbolic_read, symbolic_edit, memory, config, workflow, shell, jetbrains

2. **工具搜尋工具** (`SearchToolsTool`)
   - 支援按名稱模糊搜尋
   - 支援按類別篩選
   - 回傳工具元資料

3. **Context 延遲載入設定**
   - `deferred_loading`: 是否啟用延遲載入
   - `core_tools`: 核心工具列表

4. **MCP 工具載入邏輯**
   - 根據設定決定載入全部工具或僅核心工具

## 架構設計

```
┌─────────────────────────────────────────────────────────────┐
│                     SerenaAgentContext                       │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ deferred_loading │  │   core_tools    │                   │
│  │     (bool)       │  │   (tuple)       │                   │
│  └─────────────────┘  └─────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     SerenaMCPFactory                         │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ _set_mcp_tools()                                     │    │
│  │   if deferred_loading:                               │    │
│  │     載入 core_tools                                  │    │
│  │   else:                                              │    │
│  │     載入全部工具                                      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      MCP Server                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │search_tools │  │list_dir     │  │find_file    │  ...    │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

## 實現細節

### 1. 工具分類系統

**檔案**: `src/serena/tools/tool_categories.py`

```python
class ToolCategory(Enum):
    FILE_OPERATIONS = "file_operations"
    SYMBOLIC_READ = "symbolic_read"
    SYMBOLIC_EDIT = "symbolic_edit"
    MEMORY = "memory"
    CONFIG = "config"
    WORKFLOW = "workflow"
    SHELL = "shell"
    JETBRAINS = "jetbrains"

@singleton
class ToolCategoryRegistry:
    def get_category(self, tool_name: str) -> ToolCategory | None: ...
    def get_tools_by_category(self, category: ToolCategory) -> list[str]: ...
```

### 2. SearchToolsTool 工具

**檔案**: `src/serena/tools/config_tools.py`

```python
class SearchToolsTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """搜尋可用工具，支援按名稱、類別或關鍵字搜尋"""

    def apply(
        self,
        query: str = "",
        category: str | None = None,
        include_descriptions: bool = True,
        max_results: int = 20,
    ) -> str:
        ...
```

### 3. Context 配置擴展

**檔案**: `src/serena/config/context_mode.py`

```python
@dataclass(kw_only=True)
class SerenaAgentContext(ToolInclusionDefinition, ToStringMixin):
    # ... 現有欄位 ...

    deferred_loading: bool = False
    """是否啟用延遲載入"""

    core_tools: tuple[str, ...] = ()
    """延遲載入模式下的核心工具列表"""
```

### 4. 預設核心工具

**檔案**: `src/serena/constants.py`

```python
DEFAULT_CORE_TOOLS: tuple[str, ...] = (
    "search_tools",           # 工具搜尋
    "initial_instructions",   # 初始指令
    "activate_project",       # 專案啟動
    "get_current_config",     # 當前配置
    "check_onboarding_performed",  # 載入檢查
    "list_dir",               # 目錄列表
    "find_file",              # 檔案搜尋
)
```

### 5. MCP 工具載入邏輯

**檔案**: `src/serena/mcp.py`

```python
def _set_mcp_tools(self, mcp: FastMCP, openai_tool_compatible: bool = False) -> None:
    if self.context.deferred_loading:
        tools_to_load = list(self._iter_core_tools())
    else:
        tools_to_load = list(self._iter_tools())

    for tool in tools_to_load:
        mcp_tool = self.make_mcp_tool(tool, openai_tool_compatible=openai_tool_compatible)
        mcp._tool_manager._tools[tool.get_name()] = mcp_tool
```

### 6. 系統提示更新

**檔案**: `src/serena/resources/config/prompt_templates/system_prompt.yml`

當 `deferred_loading_enabled` 為 True 時，系統提示會包含：

```
**Tool Discovery (Deferred Loading)**
Only core tools are loaded initially to reduce overhead. Use `search_tools` to discover additional tools:
- `search_tools(query="symbol")` - Search for tools with names containing "symbol"
- `search_tools(category="file_operations")` - List all file operation tools
- `search_tools(category="symbolic_read")` - List symbol reading tools
- `search_tools(category="symbolic_edit")` - List symbol editing tools
- `search_tools(category="memory")` - List memory tools

Core tools available: search_tools, initial_instructions, activate_project, ...
```

## 使用方式

### 啟用延遲載入

使用 `deferred-loading` context 啟動 MCP 伺服器：

```bash
uv run serena-mcp-server --context deferred-loading --project /path/to/project
```

### 搜尋工具

LLM 可以使用 `search_tools` 工具來發現可用工具：

```python
# 按名稱搜尋
search_tools(query="symbol")

# 按類別篩選
search_tools(category="symbolic_edit")

# 組合搜尋
search_tools(query="find", category="file_operations")
```

### 搜尋結果範例

```
Found 4 tool(s):

- **find_symbol** [active] [symbolic_read]
  Retrieves information on all symbols/code entities based on the given name path pattern...

- **find_referencing_symbols** [active] [symbolic_read]
  Finds references to the symbol at the given name_path...

- **find_file** [active] [file_operations]
  Finds non-gitignored files matching the given file mask...

Available categories: file_operations, symbolic_read, symbolic_edit, memory, config, workflow, shell, jetbrains
```

## 配置選項

### Context YAML 配置

建立自訂的延遲載入 Context：

```yaml
# ~/.serena/contexts/my-deferred-context.yml
description: 自訂延遲載入 Context
prompt: |
  You are running with deferred tool loading enabled.
  Use `search_tools` to discover additional tools when needed.

excluded_tools: []
included_optional_tools:
  - switch_modes

tool_description_overrides: {}

deferred_loading: true
core_tools:
  - search_tools
  - initial_instructions
  - activate_project
  - get_current_config
  - list_dir
  - find_file
  - get_symbols_overview  # 可以加入更多核心工具
```

### 工具類別

| 類別 | 說明 | 包含工具 |
|------|------|----------|
| `file_operations` | 檔案系統操作 | list_dir, find_file, search_for_pattern, read_file, write_file, replace_content |
| `symbolic_read` | 符號讀取分析 | get_symbols_overview, find_symbol, find_referencing_symbols |
| `symbolic_edit` | 符號編輯操作 | replace_symbol_body, insert_after_symbol, insert_before_symbol, rename_symbol |
| `memory` | 專案記憶管理 | read_memory, write_memory, list_memories, delete_memory, edit_memory |
| `config` | 配置和專案管理 | activate_project, get_current_config, switch_modes, search_tools |
| `workflow` | 工作流程操作 | initial_instructions, check_onboarding_performed, onboarding, think_about_* |
| `shell` | Shell 命令執行 | run_shell_command |
| `jetbrains` | JetBrains IDE 整合 | jetbrains_* |

## API 參考

### SearchToolsTool

```python
def apply(
    self,
    query: str = "",
    category: str | None = None,
    include_descriptions: bool = True,
    max_results: int = 20,
) -> str:
    """
    搜尋可用工具。

    :param query: 名稱搜尋關鍵字（大小寫不敏感的子字串比對）
    :param category: 類別篩選（file_operations, symbolic_read, symbolic_edit,
                     memory, config, workflow, shell, jetbrains）
    :param include_descriptions: 是否在結果中包含工具描述
    :param max_results: 最大回傳結果數量
    :return: 格式化的工具列表，包含名稱、狀態、類別和描述
    """
```

### ToolCategoryRegistry

```python
class ToolCategoryRegistry:
    def get_category(self, tool_name: str) -> ToolCategory | None:
        """取得工具的類別"""

    def get_tools_by_category(self, category: ToolCategory) -> list[str]:
        """取得某類別下的所有工具"""

    def get_all_categories(self) -> list[ToolCategory]:
        """取得所有可用類別"""

    def register_tool(self, tool_name: str, category: ToolCategory) -> None:
        """註冊工具到類別（用於擴展）"""
```

## 向後兼容性

- `deferred_loading` 預設為 `False`，現有配置無需修改
- 所有現有 Context 和 Mode 繼續正常運作
- MCP 客戶端 API 保持不變
- 未啟用延遲載入時，行為與之前完全相同

## 相關檔案

| 檔案 | 說明 |
|------|------|
| `src/serena/tools/tool_categories.py` | 工具分類系統 |
| `src/serena/tools/config_tools.py` | SearchToolsTool 實現 |
| `src/serena/config/context_mode.py` | SerenaAgentContext 擴展 |
| `src/serena/mcp.py` | MCP 延遲載入邏輯 |
| `src/serena/agent.py` | 系統提示生成 |
| `src/serena/constants.py` | DEFAULT_CORE_TOOLS 常數 |
| `src/serena/resources/config/prompt_templates/system_prompt.yml` | 提示模板 |
| `src/serena/resources/config/contexts/deferred-loading.yml` | 預設延遲載入 Context |
| `test/serena/test_deferred_loading.py` | 單元測試 |
