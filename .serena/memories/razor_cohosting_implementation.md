# Razor Cohosting Implementation for Serena

## Overview
This document describes the implementation of Razor Cohosting support in Serena MCP, enabling `.cshtml` and `.razor` file symbol resolution through the C# Language Server.

## Problem Statement
Prior to this implementation, `.cshtml` files were opened with `languageId="csharp"`, which caused the LSP server to not route requests to Razor Cohost handlers. Despite Razor Cohosting being successfully initialized (verified via logs), symbol resolution returned empty results.

## Solution Implemented

### Key Change: `_get_language_id_for_file` Override (Phase 3)
**File**: `src/solidlsp/language_servers/csharp_language_server.py`
**Location**: Lines 301-315 (after `is_capability_registered` method)

```python
@override
def _get_language_id_for_file(self, relative_file_path: str) -> str:
    """Return the correct language ID for files.

    Razor (.razor, .cshtml) files must be opened with language ID "aspnetcorerazor"
    for the Razor Cohost extension to process them correctly. The Razor Cohost
    dynamically registers handlers for document selectors with language="aspnetcorerazor".

    This is critical for Razor Cohosting support - without the correct languageId,
    requests to .cshtml files will not be routed to the Razor handlers.
    """
    ext = os.path.splitext(relative_file_path)[1].lower()
    if ext in (".razor", ".cshtml"):
        return "aspnetcorerazor"
    return "csharp"
```

### Previous Changes (Phase 2)
**File**: `src/solidlsp/language_servers/csharp_language_server.py`

1. **Dynamic Capability Registration Storage** (Lines 256-259):
   - Added `_registered_capabilities` dictionary to track dynamically registered capabilities

2. **Helper Methods** (Lines 261-299):
   - `get_registered_capabilities()` - Get all registered capabilities
   - `get_capabilities_for_method(method)` - Get capabilities for specific LSP method
   - `is_capability_registered(method, pattern)` - Check if capability is registered

3. **Updated Handlers**:
   - `handle_register_capability` - Now stores registrations and returns `{}` (LSP spec compliance)
   - `handle_unregister_capability` - New handler for capability unregistration

## How It Works

### LSP Flow
1. **Initialization**: C# LSP server starts with Razor extension
2. **Dynamic Registration**: Razor Cohost sends `client/registerCapability` with:
   - `method: "textDocument/documentSymbol"`
   - `documentSelector: [{ pattern: "**/*.{razor,cshtml}", language: "aspnetcorerazor" }]`
3. **File Open**: When opening a `.cshtml` file:
   - Serena calls `_get_language_id_for_file("path/to/file.cshtml")`
   - Returns `"aspnetcorerazor"` (previously returned `"csharp"`)
   - `textDocument/didOpen` is sent with correct `languageId`
4. **Symbol Request**: `textDocument/documentSymbol` request is routed to Razor Cohost handler

### Verification from Logs
Razor Cohost successfully registers capabilities:
```
[Razor.LanguageClient.Cohost.RazorCohostDynamicRegistrationService] Requesting 9 Razor cohost registrations.
[DynamicRegistration] Registered: method=textDocument/documentSymbol, id=b77890e4-b1ec-4417-9b44-9b51ba4a122f
[DynamicRegistration]   - pattern=**/*.{razor,cshtml}, language=aspnetcorerazor
```

## Testing Plan

### Manual Verification Steps
1. **Restart Serena MCP Server** to load the updated code
2. **Activate a project** containing `.cshtml` files (e.g., `Project-agent1`)
3. **Run symbol query**:
   ```
   get_symbols_overview: relative_path='Project/Pages/Management/Dashboard/Index.cshtml', depth=1
   ```
4. **Expected Result**: Should return symbols (not empty `{}`)

### Test Cases
1. `.cshtml` file symbol resolution
2. `.razor` file symbol resolution (if available)
3. Mixed project with both `.cs` and `.cshtml` files
4. Verify capability registration tracking via `get_registered_capabilities()`

## Related Files
- `src/solidlsp/language_servers/csharp_language_server.py` - Main implementation
- `src/solidlsp/ls.py` - Base class with `_get_language_id_for_file` and `open_file`
- `src/solidlsp/language_servers/vue_language_server.py` - Reference implementation

## Date
2026-01-26
