"""
CSharp Language Server using Roslyn Language Server (Official Roslyn-based LSP server from NuGet.org)

This module supports Razor (.razor, .cshtml) files through the Razor extension when available.

## Configuration Options

The following options can be set in ls_specific_settings[Language.CSHARP]:

    enable_razor (bool):
        Enable Razor (.razor, .cshtml) file support through the Razor extension.
        Default: True
        Note: Requires matching .NET versions between language server and Razor extension.

    local_language_server_path (str):
        Path to a locally built Roslyn language server directory.
        The directory should contain Microsoft.CodeAnalysis.LanguageServer.dll.
        Example: "D:/GitHub/roslyn/artifacts/bin/Microsoft.CodeAnalysis.LanguageServer/Release/net10.0"

    local_razor_extension_path (str):
        Path to a locally built Razor extension directory.
        The directory should contain Microsoft.VisualStudioCode.RazorExtension.dll.
        Example: "D:/GitHub/razor/artifacts/bin/Microsoft.VisualStudioCode.RazorExtension/Release/net10.0"

    runtime_dependencies (list[dict]):
        Override default runtime dependency configurations.

## Razor Document Symbol Support

The Razor Language Server uses a delegation architecture for Document Symbols:
1. Razor LS sends `razor/updateCSharpBuffer` notifications with generated C# content
2. When Document Symbols are requested for a Razor file, Razor LS sends `razor/documentSymbol`
   request to the client (Serena)
3. Serena's CSharpLanguageServer receives this request, looks up the cached virtual C# document,
   and forwards the symbol request to Roslyn C# LS
4. The C# symbols are returned to Razor LS, which maps them back to Razor positions

This implementation enables Serena's symbol analysis tools (get_symbols_overview, find_symbol, etc.)
to work with Razor files by providing the underlying C# symbol information.
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import urllib.request
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from overrides import override

from solidlsp.ls import DocumentSymbols, LanguageServerDependencyProvider, SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_types import Hover, UnifiedSymbolInformation
from solidlsp.ls_utils import PathUtils
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams, InitializeResult
from solidlsp.settings import SolidLSPSettings
from solidlsp.util.zip import SafeZipExtractor

from .common import RuntimeDependency, RuntimeDependencyCollection

log = logging.getLogger(__name__)

# Path to bundled Razor extension files (relative to this module)
_BUNDLED_RAZOR_EXTENSION_DIR = Path(__file__).parent / "razor_extension"

_RUNTIME_DEPENDENCIES = [
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for Windows (x64)",
        package_name="roslyn-language-server.win-x64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.win-x64/5.5.0-2.26078.4",
        platform_id="win-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/win-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for Windows (ARM64)",
        package_name="roslyn-language-server.win-arm64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.win-arm64/5.5.0-2.26078.4",
        platform_id="win-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/win-arm64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for macOS (x64)",
        package_name="roslyn-language-server.osx-x64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.osx-x64/5.5.0-2.26078.4",
        platform_id="osx-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/osx-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for macOS (ARM64)",
        package_name="roslyn-language-server.osx-arm64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.osx-arm64/5.5.0-2.26078.4",
        platform_id="osx-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/osx-arm64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for Linux (x64)",
        package_name="roslyn-language-server.linux-x64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.linux-x64/5.5.0-2.26078.4",
        platform_id="linux-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/linux-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Roslyn Language Server for Linux (ARM64)",
        package_name="roslyn-language-server.linux-arm64",
        package_version="5.5.0-2.26078.4",
        url="https://www.nuget.org/api/v2/package/roslyn-language-server.linux-arm64/5.5.0-2.26078.4",
        platform_id="linux-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="tools/net10.0/linux-arm64",
    ),
]


def breadth_first_file_scan(root_dir: str) -> Iterable[str]:
    """
    Perform a breadth-first scan of files in the given directory.
    Yields file paths in breadth-first order.
    """
    queue = [root_dir]
    while queue:
        current_dir = queue.pop(0)
        try:
            for item in os.listdir(current_dir):
                if item.startswith("."):
                    continue
                item_path = os.path.join(current_dir, item)
                if os.path.isdir(item_path):
                    queue.append(item_path)
                elif os.path.isfile(item_path):
                    yield item_path
        except (PermissionError, OSError):
            # Skip directories we can't access
            pass


def find_solution_or_project_file(root_dir: str) -> str | None:
    """
    Find the first .sln file in breadth-first order.
    If no .sln file is found, look for a .csproj file.
    """
    sln_file = None
    csproj_file = None

    for filename in breadth_first_file_scan(root_dir):
        if filename.endswith(".sln") and sln_file is None:
            sln_file = filename
        elif filename.endswith(".csproj") and csproj_file is None:
            csproj_file = filename

        # If we found a .sln file, return it immediately
        if sln_file:
            return sln_file

    # If no .sln file was found, return the first .csproj file
    return csproj_file


class LRUCache(OrderedDict):
    """A simple LRU (Least Recently Used) cache based on OrderedDict.

    When the cache exceeds maxsize, the least recently used items are evicted.
    Accessing or setting an item moves it to the end (most recently used).
    """

    def __init__(self, maxsize: int = 100):
        super().__init__()
        self.maxsize = maxsize

    def __getitem__(self, key: Any) -> Any:
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self.maxsize:
            self.popitem(last=False)

    def get(self, key: Any, default: Any = None) -> Any:
        """Get an item without moving it to the end."""
        try:
            return super().__getitem__(key)
        except KeyError:
            return default


class CSharpLanguageServer(SolidLanguageServer):
    """
    Provides C# specific instantiation of the LanguageServer class using the official Roslyn-based
    language server from NuGet.org.

    ## Razor Support

    This language server supports Razor (.razor, .cshtml) files through the Razor extension.
    Razor support is enabled by default if the razor_extension files are available.

    To disable Razor support, set ls_specific_settings["csharp"]["enable_razor"] = False.

    The Razor extension provides:
    - IntelliSense for Razor syntax
    - Go to Definition for components and C# code
    - Hover information
    - Diagnostics for Razor files

    ## Local Development Support

    For local Roslyn/Razor development, you can specify local paths:
    - local_language_server_path: Path to locally built Roslyn language server
    - local_razor_extension_path: Path to locally built Razor extension

    When using local paths, files are copied to a cache directory to prevent .NET process
    locking issues, allowing you to rebuild while Serena is running.

    ## Runtime Dependency Overrides

    You can pass a list of runtime dependency overrides in ls_specific_settings["csharp"]["runtime_dependencies"].
    This is a list of dicts, each containing at least the "id" key, and optionally "platform_id" to uniquely
    identify the dependency to override.

    Example - Override Roslyn Language Server URL:
    ```
        {
            "id": "CSharpLanguageServer",
            "platform_id": "win-x64",
            "url": "https://example.com/custom-roslyn-server.nupkg"
        }
    ```

    See the `_RUNTIME_DEPENDENCIES` variable above for the available dependency ids and platform_ids.

    Note: .NET runtime (version 10+) is required and installed automatically via Microsoft's official install
    scripts. If you have a custom .NET installation, ensure 'dotnet' is available in PATH with version 10 or higher.
    """

    def __init__(self, config: LanguageServerConfig, repository_root_path: str, solidlsp_settings: SolidLSPSettings):
        """
        Creates a CSharpLanguageServer instance. This class is not meant to be instantiated directly.
        Use LanguageServer.create() instead.
        """
        super().__init__(config, repository_root_path, None, "csharp", solidlsp_settings)
        # Cache for original Roslyn symbol names with type annotations
        # Key: (relative_file_path, line, character) -> Value: original name
        self._original_symbol_names: dict[tuple[str, int, int], str] = {}

        # Razor virtual C# document cache with LRU eviction
        # Maps razor file URI -> (version, generated C# content, virtual C# URI)
        # Uses LRU cache to prevent unbounded memory growth
        self._razor_virtual_documents: LRUCache = LRUCache(maxsize=100)

        # Dynamic capability registration table
        # Maps registration id -> registration details (method, registerOptions, etc.)
        # This is used by Razor Cohosting to register handlers for .cshtml and .razor files
        self._registered_capabilities: dict[str, dict] = {}

    def get_registered_capabilities(self) -> dict[str, dict]:
        """Get a copy of all dynamically registered capabilities.

        Returns:
            A dictionary mapping registration id to capability details.
            Each capability contains: id, method, registerOptions

        """
        return dict(self._registered_capabilities)

    def get_capabilities_for_method(self, method: str) -> list[dict]:
        """Get all registered capabilities for a specific LSP method.

        Args:
            method: The LSP method name (e.g., 'textDocument/documentSymbol')

        Returns:
            A list of capability registrations for the given method.

        """
        return [cap for cap in self._registered_capabilities.values() if cap.get("method") == method]

    def is_capability_registered(self, method: str, pattern: str | None = None) -> bool:
        """Check if a capability is registered for a method and optional file pattern.

        Args:
            method: The LSP method name
            pattern: Optional file pattern to check (e.g., '**/*.cshtml')

        Returns:
            True if the capability is registered.

        """
        for cap in self._registered_capabilities.values():
            if cap.get("method") != method:
                continue
            if pattern is None:
                return True
            doc_selector = cap.get("registerOptions", {}).get("documentSelector", [])
            for selector in doc_selector:
                if selector.get("pattern") == pattern:
                    return True
        return False

    def _create_dependency_provider(self) -> LanguageServerDependencyProvider:
        return self.DependencyProvider(self._custom_settings, self._ls_resources_dir, self._solidlsp_settings, self.repository_root_path)

    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        return super().is_ignored_dirname(dirname) or dirname in ["bin", "obj", "packages", ".vs"]

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

    @override
    def request_document_symbols(self, relative_file_path: str, file_buffer: Any = None) -> DocumentSymbols:
        """
        Override to normalize Roslyn symbol names and cache originals.

        Roslyn 5.5.0+ returns symbol names with type annotations:
        - Properties: "Name : string"
        - Methods: "Add(int, int) : int"

        This method:
        1. Normalizes names to base form ("Name", "Add")
        2. Caches original names for rich information display
        3. Populates LSP spec's 'detail' field with type/signature info
        """
        symbols = super().request_document_symbols(relative_file_path, file_buffer)

        # Normalize all symbols recursively
        for symbol in symbols.iter_symbols():
            self._normalize_symbol_name(symbol, relative_file_path)

        return symbols

    @override
    def request_hover(self, relative_file_path: str, line: int, column: int) -> Hover | None:
        """
        Override to inject original Roslyn symbol names (with type annotations) into hover responses.

        When hovering over a symbol whose name was normalized, we prepend the original
        full name (e.g., 'Add(int, int) : int') to the hover content.
        """
        hover = super().request_hover(relative_file_path, line, column)

        if hover is None:
            return None

        # Check if we have an original name for this position
        original_name = self._original_symbol_names.get((relative_file_path, line, column))

        if original_name and "contents" in hover:
            contents = hover["contents"]
            if isinstance(contents, dict) and "value" in contents:
                # Prepend the original full name with type information to the hover content
                prefix = f"**{original_name}**\n\n---\n\n"
                contents["value"] = prefix + contents["value"]

        return hover

    def _normalize_symbol_name(self, symbol: UnifiedSymbolInformation, relative_file_path: str) -> None:
        """
        Normalize a single symbol's name and cache the original.
        Processes children recursively.
        """
        original_name = symbol.get("name", "")

        # Extract base name and type/signature info
        normalized_name, type_info = self._extract_base_name_and_type(original_name)

        # Store original name if it was normalized
        if original_name != normalized_name:
            sel_range = symbol.get("selectionRange")
            if sel_range:
                start = sel_range.get("start")
                if start and "line" in start and "character" in start:
                    line = start["line"]
                    char = start["character"]
                    cache_key = (relative_file_path, line, char)
                    self._original_symbol_names[cache_key] = original_name

            # Populate LSP spec's 'detail' field with type/signature information
            if type_info and "detail" not in symbol:
                symbol["detail"] = type_info

        # Update the symbol name
        symbol["name"] = normalized_name

        # Process children recursively
        children = symbol.get("children", [])
        for child in children:
            self._normalize_symbol_name(child, relative_file_path)

    @staticmethod
    def _extract_base_name_and_type(roslyn_name: str) -> tuple[str, str]:
        """
        Extract base name and type/signature information from Roslyn symbol names.

        Examples:
            "Name : string" -> ("Name", ": string")
            "Add(int, int) : int" -> ("Add", "(int, int) : int")
            "ToString()" -> ("ToString", "()")
            "SimpleMethod" -> ("SimpleMethod", "")

        Returns:
            Tuple of (base_name, type_info)

        """
        # Check for property pattern: "Name : Type"
        if " : " in roslyn_name and "(" not in roslyn_name:
            base_name, type_part = roslyn_name.split(" : ", 1)
            return base_name.strip(), f": {type_part.strip()}"

        # Check for method pattern: "MethodName(params) : ReturnType"
        if "(" in roslyn_name:
            paren_idx = roslyn_name.index("(")
            base_name = roslyn_name[:paren_idx].strip()
            signature = roslyn_name[paren_idx:].strip()
            return base_name, signature

        # No type annotation
        return roslyn_name, ""

    class DependencyProvider(LanguageServerDependencyProvider):
        def __init__(
            self,
            custom_settings: SolidLSPSettings.CustomLSSettings,
            ls_resources_dir: str,
            solidlsp_settings: SolidLSPSettings,
            repository_root_path: str,
        ):
            super().__init__(custom_settings, ls_resources_dir)
            self._solidlsp_settings = solidlsp_settings
            self._repository_root_path = repository_root_path
            self._dotnet_path, self._language_server_path = self._ensure_server_installed()

            # Check if Razor support is enabled
            self._enable_razor = cast(bool, custom_settings.get("enable_razor", True))
            self._razor_extension_dir: Path | None = None

            if self._enable_razor:
                self._razor_extension_dir = self._ensure_razor_extension_installed()

        @staticmethod
        def _load_local_cache_metadata(meta_file: Path) -> dict[str, Any] | None:
            """Load local cache metadata from JSON file."""
            if not meta_file.exists():
                return None
            try:
                with open(meta_file, encoding="utf-8") as f:
                    return cast(dict[str, Any], json.load(f))
            except (json.JSONDecodeError, OSError) as e:
                log.warning(f"Failed to load cache metadata from {meta_file}: {e}")
                return None

        @staticmethod
        def _save_local_cache_metadata(
            meta_file: Path,
            source_path: Path,
            source_mtime: float,
        ) -> None:
            """Save local cache metadata to JSON file."""
            import time

            metadata = {
                "source_path": str(source_path),
                "source_mtime": source_mtime,
                "copied_at": time.time(),
            }
            try:
                with open(meta_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
            except OSError as e:
                log.warning(f"Failed to save cache metadata to {meta_file}: {e}")

        def _is_local_cache_up_to_date(
            self,
            source_path: Path,
            cache_dir: Path,
            meta_file: Path,
            main_dll_name: str,
        ) -> bool:
            """
            Check if the local cache is up-to-date.

            Returns True if:
            - Cache directory exists
            - Metadata file exists and is valid
            - Source path matches
            - Main DLL modification time matches
            """
            if not cache_dir.exists():
                return False

            metadata = self._load_local_cache_metadata(meta_file)
            if metadata is None:
                return False

            # Check if source path matches
            if metadata.get("source_path") != str(source_path):
                log.debug(f"Cache source path mismatch: {metadata.get('source_path')} != {source_path}")
                return False

            # Check main DLL modification time
            main_dll = source_path / main_dll_name
            if not main_dll.exists():
                return False

            current_mtime = main_dll.stat().st_mtime
            cached_mtime = metadata.get("source_mtime")
            if cached_mtime is None or current_mtime != cached_mtime:
                log.debug(f"Cache mtime mismatch: {cached_mtime} != {current_mtime}")
                return False

            return True

        def _copy_local_to_cache(
            self,
            source_path: Path,
            cache_dir: Path,
            meta_file: Path,
            main_dll_name: str,
        ) -> Path | None:
            """
            Copy local directory to cache.

            Returns the cache directory path on success, None on failure.
            """
            main_dll = source_path / main_dll_name
            if not main_dll.exists():
                log.warning(f"Main DLL not found: {main_dll}")
                return None

            try:
                # Remove existing cache directory if it exists
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)

                # Copy the directory
                shutil.copytree(source_path, cache_dir)

                # Save metadata
                source_mtime = main_dll.stat().st_mtime
                self._save_local_cache_metadata(meta_file, source_path, source_mtime)

                return cache_dir
            except Exception as e:
                log.warning(f"Failed to copy local directory to cache: {e}")
                return None

        def create_launch_command(self) -> list[str]:
            # Find solution or project file
            solution_or_project = find_solution_or_project_file(self._repository_root_path)

            # Create log directory
            log_dir = Path(self._ls_resources_dir) / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)

            # Build command using dotnet directly
            cmd = [self._dotnet_path, self._language_server_path, "--logLevel=Information", f"--extensionLogDirectory={log_dir}", "--stdio"]

            # Add Razor extension parameters if available
            if self._razor_extension_dir and self._razor_extension_dir.exists():
                razor_extension_dll = self._razor_extension_dir / "Microsoft.VisualStudioCode.RazorExtension.dll"
                razor_compiler_dll = self._razor_extension_dir / "Microsoft.CodeAnalysis.Razor.Compiler.dll"
                razor_design_time_targets = self._razor_extension_dir / "Targets" / "Microsoft.NET.Sdk.Razor.DesignTime.targets"

                if razor_extension_dll.exists():
                    cmd.append(f"--extension={razor_extension_dll}")
                    log.info(f"Razor extension enabled: {razor_extension_dll}")

                if razor_compiler_dll.exists():
                    cmd.append(f"--razorSourceGenerator={razor_compiler_dll}")
                    log.debug(f"Razor source generator: {razor_compiler_dll}")

                if razor_design_time_targets.exists():
                    cmd.append(f"--razorDesignTimePath={razor_design_time_targets}")
                    log.debug(f"Razor design time targets: {razor_design_time_targets}")

            # The language server will discover the solution/project from the workspace root
            if solution_or_project:
                log.info(f"Found solution/project file: {solution_or_project}")
            else:
                log.warning("No .sln or .csproj file found, language server will attempt auto-discovery")

            log.debug(f"Language server command: {' '.join(cmd)}")

            return cmd

        def _ensure_server_installed(self) -> tuple[str, str]:
            """
            Ensure .NET runtime and Microsoft.CodeAnalysis.LanguageServer are available.
            Returns a tuple of (dotnet_path, language_server_dll_path).

            You can specify a local language server path in ls_specific_settings["csharp"]["local_language_server_path"]
            to use a locally built language server instead of downloading one.
            """
            # Check for local language server path override
            local_ls_path = self._custom_settings.get("local_language_server_path")
            if local_ls_path and isinstance(local_ls_path, str):
                source_path = Path(local_ls_path)
                main_dll = "Microsoft.CodeAnalysis.LanguageServer.dll"
                if (source_path / main_dll).exists():
                    cache_dir = Path(self._ls_resources_dir) / "local-roslyn"
                    meta_file = Path(self._ls_resources_dir) / "local-roslyn.meta.json"

                    if not self._is_local_cache_up_to_date(source_path, cache_dir, meta_file, main_dll):
                        log.info(f"Copying local language server to cache from {local_ls_path}")
                        cached_path = self._copy_local_to_cache(source_path, cache_dir, meta_file, main_dll)
                        if cached_path is None:
                            # Fallback to direct use if copy fails
                            log.warning("Falling back to direct use of local language server")
                            system_dotnet = shutil.which("dotnet")
                            if system_dotnet:
                                return system_dotnet, str(source_path / main_dll)
                            else:
                                log.warning("Local language server specified but dotnet not found in PATH")
                    else:
                        log.info(f"Using cached local language server (source: {local_ls_path})")

                    # Use system dotnet for local builds
                    system_dotnet = shutil.which("dotnet")
                    if system_dotnet:
                        return system_dotnet, str(cache_dir / main_dll)
                    else:
                        log.warning("Local language server specified but dotnet not found in PATH")
                else:
                    log.warning(f"Local language server path specified but DLL not found: {source_path / main_dll}")

            runtime_dependency_overrides = cast(list[dict[str, Any]], self._custom_settings.get("runtime_dependencies", []))

            # Filter out deprecated DotNetRuntime overrides and warn users
            filtered_overrides = []
            for dep_override in runtime_dependency_overrides:
                if dep_override.get("id") == "DotNetRuntime":
                    log.warning(
                        "The 'DotNetRuntime' runtime_dependencies override is no longer supported. "
                        ".NET is now installed automatically via Microsoft's official install scripts. "
                        "Please remove this override from your configuration."
                    )
                else:
                    filtered_overrides.append(dep_override)

            log.debug("Resolving runtime dependencies")

            runtime_dependencies = RuntimeDependencyCollection(
                _RUNTIME_DEPENDENCIES,
                overrides=filtered_overrides,
            )

            log.debug(
                f"Available runtime dependencies: {runtime_dependencies.get_dependencies_for_current_platform}",
            )

            # Find the dependencies for our platform
            lang_server_dep = runtime_dependencies.get_single_dep_for_current_platform("CSharpLanguageServer")
            dotnet_path = self._ensure_dotnet_runtime()
            server_dll_path = self._ensure_language_server(lang_server_dep)

            return dotnet_path, server_dll_path

        def _ensure_dotnet_runtime(self) -> str:
            """Ensure .NET runtime is available and return the dotnet executable path."""
            # Check if dotnet is already available on the system
            system_dotnet = shutil.which("dotnet")
            if system_dotnet:
                # Check if it's .NET 10 or compatible
                try:
                    result = subprocess.run([system_dotnet, "--list-runtimes"], capture_output=True, text=True, check=True)
                    # Accept .NET 10 or higher (10.x, 11.x, etc.)
                    if any(f"Microsoft.NETCore.App {v}." in result.stdout for v in range(10, 20)):
                        log.info("Found system .NET 10+ runtime")
                        return system_dotnet
                except subprocess.CalledProcessError:
                    pass

            # Install .NET 10 runtime using Microsoft's install script
            return self._install_dotnet_with_script()

        def _ensure_language_server(self, lang_server_dep: RuntimeDependency) -> str:
            """Ensure language server is available and return the DLL path."""
            package_name = lang_server_dep.package_name
            package_version = lang_server_dep.package_version

            server_dir = Path(self._ls_resources_dir) / f"{package_name}.{package_version}"
            assert lang_server_dep.binary_name is not None
            server_dll = server_dir / lang_server_dep.binary_name

            if server_dll.exists():
                log.info(f"Using cached Roslyn Language Server from {server_dll}")
                return str(server_dll)

            # Download and install the language server
            log.info(f"Downloading {package_name} version {package_version} from NuGet.org...")
            package_path = self._download_nuget_package(lang_server_dep)

            # Extract and install
            self._extract_language_server(lang_server_dep, package_path, server_dir)

            if not server_dll.exists():
                raise SolidLSPException("Roslyn Language Server DLL not found after extraction")

            # Make executable on Unix systems
            if platform.system().lower() != "windows":
                server_dll.chmod(0o755)

            log.info(f"Successfully installed Roslyn Language Server to {server_dll}")
            return str(server_dll)

        @staticmethod
        def _extract_language_server(lang_server_dep: RuntimeDependency, package_path: Path, server_dir: Path) -> None:
            """Extract language server files from downloaded package."""
            extract_path = lang_server_dep.extract_path or "lib/net9.0"
            source_dir = package_path / extract_path

            if not source_dir.exists():
                # Try alternative locations
                for possible_dir in [
                    package_path / "tools" / "net9.0" / "any",
                    package_path / "lib" / "net9.0",
                    package_path / "contentFiles" / "any" / "net9.0",
                ]:
                    if possible_dir.exists():
                        source_dir = possible_dir
                        break
                else:
                    raise SolidLSPException(f"Could not find language server files in package. Searched in {package_path}")

            # Copy files to cache directory
            server_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, server_dir, dirs_exist_ok=True)

        def _download_nuget_package(self, dependency: RuntimeDependency) -> Path:
            """
            Download a NuGet package from NuGet.org and extract it.
            Returns the path to the extracted package directory.
            """
            package_name = dependency.package_name
            package_version = dependency.package_version
            url = dependency.url

            if url is None:
                raise SolidLSPException(f"No URL specified for package {package_name} version {package_version}")

            # Create temporary directory for package download
            temp_dir = Path(self._ls_resources_dir) / "temp_downloads"
            temp_dir.mkdir(parents=True, exist_ok=True)

            try:
                log.debug(f"Downloading package from: {url}")

                # Download the .nupkg file
                nupkg_file = temp_dir / f"{package_name}.{package_version}.nupkg"
                urllib.request.urlretrieve(url, nupkg_file)

                # Extract the .nupkg file (it's just a zip file)
                package_extract_dir = temp_dir / f"{package_name}.{package_version}"
                package_extract_dir.mkdir(exist_ok=True)

                # Use SafeZipExtractor to handle long paths and skip errors
                extractor = SafeZipExtractor(archive_path=nupkg_file, extract_dir=package_extract_dir, verbose=False)
                extractor.extract_all()

                # Clean up the nupkg file
                nupkg_file.unlink()

                log.info(f"Successfully downloaded and extracted {package_name} version {package_version} from NuGet.org")
                return package_extract_dir

            except Exception as e:
                raise SolidLSPException(f"Failed to download package {package_name} version {package_version} from NuGet.org: {e}") from e

        def _install_dotnet_with_script(self, version: str = "10.0") -> str:
            """
            Install .NET runtime using Microsoft's official install script.
            Returns the path to the dotnet executable.
            """
            dotnet_dir = Path(self._ls_resources_dir) / f"dotnet-runtime-{version}"

            # Determine binary name based on platform
            is_windows = platform.system().lower() == "windows"
            dotnet_exe = dotnet_dir / ("dotnet.exe" if is_windows else "dotnet")

            if dotnet_exe.exists():
                log.info(f"Using cached .NET {version} runtime from {dotnet_exe}")
                return str(dotnet_exe)

            # Download and run install script
            log.info(f"Installing .NET {version} runtime using official Microsoft install script...")
            dotnet_dir.mkdir(parents=True, exist_ok=True)

            try:
                if is_windows:
                    # PowerShell script for Windows
                    script_url = "https://dot.net/v1/dotnet-install.ps1"
                    script_path = dotnet_dir / "dotnet-install.ps1"
                    urllib.request.urlretrieve(script_url, script_path)

                    cmd = [
                        "pwsh",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script_path),
                        "-Version",
                        version,
                        "-InstallDir",
                        str(dotnet_dir),
                        "-Runtime",
                        "dotnet",
                        "-NoPath",
                    ]
                else:
                    # Bash script for Linux/macOS
                    script_url = "https://dot.net/v1/dotnet-install.sh"
                    script_path = dotnet_dir / "dotnet-install.sh"
                    urllib.request.urlretrieve(script_url, script_path)
                    script_path.chmod(0o755)

                    cmd = [
                        "bash",
                        str(script_path),
                        "--version",
                        version,
                        "--install-dir",
                        str(dotnet_dir),
                        "--runtime",
                        "dotnet",
                        "--no-path",
                    ]

                # Run the install script
                log.info("Running .NET install script: %s", cmd)
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                log.debug(f"Install script output: {result.stdout}")

                if not dotnet_exe.exists():
                    raise SolidLSPException(f"dotnet executable not found at {dotnet_exe} after installation")

                log.info(f"Successfully installed .NET {version} runtime to {dotnet_exe}")
                return str(dotnet_exe)

            except subprocess.CalledProcessError as e:
                raise SolidLSPException(
                    f"Failed to install .NET {version} runtime using install script: {e.stderr if e.stderr else e}"
                ) from e
            except Exception as e:
                message = f"Failed to install .NET {version} runtime: {e}"
                if is_windows and isinstance(e, FileNotFoundError):
                    message += (
                        "; pwsh, i.e. PowerShell 7+, is required to install .NET runtime. Make sure pwsh is available on your system."
                    )
                raise SolidLSPException(message) from e

        def _ensure_razor_extension_installed(self) -> Path | None:
            """
            Ensure Razor extension files are available in the language server resources directory.
            Returns the path to the Razor extension directory, or None if not available.

            You can specify a local Razor extension path in ls_specific_settings["csharp"]["local_razor_extension_path"]
            to use a locally built Razor extension instead of the bundled one.
            """
            # Check for local Razor extension path override
            local_razor_path = self._custom_settings.get("local_razor_extension_path")
            if local_razor_path and isinstance(local_razor_path, str):
                source_path = Path(local_razor_path)
                main_dll = "Microsoft.VisualStudioCode.RazorExtension.dll"
                if (source_path / main_dll).exists():
                    cache_dir = Path(self._ls_resources_dir) / "local-razor"
                    meta_file = Path(self._ls_resources_dir) / "local-razor.meta.json"

                    if not self._is_local_cache_up_to_date(source_path, cache_dir, meta_file, main_dll):
                        log.info(f"Copying local Razor extension to cache from {local_razor_path}")
                        cached_path = self._copy_local_to_cache(source_path, cache_dir, meta_file, main_dll)
                        if cached_path is None:
                            # Fallback to direct use if copy fails
                            log.warning("Falling back to direct use of local Razor extension")
                            return source_path
                    else:
                        log.info(f"Using cached local Razor extension (source: {local_razor_path})")

                    return cache_dir
                else:
                    log.warning(f"Local Razor extension path specified but DLL not found: {source_path / main_dll}")

            razor_dir = Path(self._ls_resources_dir) / "RazorExtension"
            razor_extension_dll = razor_dir / "Microsoft.VisualStudioCode.RazorExtension.dll"

            # Check if already installed
            if razor_extension_dll.exists():
                log.info(f"Using cached Razor extension from {razor_dir}")
                return razor_dir

            # Check if bundled Razor extension is available
            if not _BUNDLED_RAZOR_EXTENSION_DIR.exists():
                log.warning(
                    f"Razor extension not found at {_BUNDLED_RAZOR_EXTENSION_DIR}. "
                    "Razor support will be disabled. To enable Razor support, ensure the "
                    "razor_extension directory exists with the required DLLs."
                )
                return None

            bundled_razor_dll = _BUNDLED_RAZOR_EXTENSION_DIR / "Microsoft.VisualStudioCode.RazorExtension.dll"
            if not bundled_razor_dll.exists():
                log.warning(f"Bundled Razor extension DLL not found at {bundled_razor_dll}. Razor support will be disabled.")
                return None

            # Copy bundled Razor extension to resources directory
            log.info(f"Installing Razor extension from {_BUNDLED_RAZOR_EXTENSION_DIR} to {razor_dir}")
            try:
                razor_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(_BUNDLED_RAZOR_EXTENSION_DIR, razor_dir, dirs_exist_ok=True)
                log.info(f"Successfully installed Razor extension to {razor_dir}")
                return razor_dir
            except Exception as e:
                log.warning(f"Failed to install Razor extension: {e}. Razor support will be disabled.")
                return None

    def _get_initialize_params(self) -> InitializeParams:
        """
        Returns the initialize params for the Microsoft.CodeAnalysis.LanguageServer.
        """
        root_uri = PathUtils.path_to_uri(self.repository_root_path)
        root_name = os.path.basename(self.repository_root_path)
        return cast(
            InitializeParams,
            {
                "workspaceFolders": [{"uri": root_uri, "name": root_name}],
                "processId": os.getpid(),
                "rootPath": self.repository_root_path,
                "rootUri": root_uri,
                "capabilities": {
                    "window": {
                        "workDoneProgress": True,
                        "showMessage": {"messageActionItem": {"additionalPropertiesSupport": True}},
                        "showDocument": {"support": True},
                    },
                    "workspace": {
                        "applyEdit": True,
                        "workspaceEdit": {"documentChanges": True},
                        "didChangeConfiguration": {"dynamicRegistration": True},
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                        "symbol": {
                            "dynamicRegistration": True,
                            "symbolKind": {"valueSet": list(range(1, 27))},
                        },
                        "executeCommand": {"dynamicRegistration": True},
                        "configuration": True,
                        "workspaceFolders": True,
                        "workDoneProgress": True,
                    },
                    "textDocument": {
                        "synchronization": {"dynamicRegistration": True, "willSave": True, "willSaveWaitUntil": True, "didSave": True},
                        "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
                        "signatureHelp": {
                            "dynamicRegistration": True,
                            "signatureInformation": {
                                "documentationFormat": ["markdown", "plaintext"],
                                "parameterInformation": {"labelOffsetSupport": True},
                            },
                        },
                        "definition": {"dynamicRegistration": True},
                        "references": {"dynamicRegistration": True},
                        "documentSymbol": {
                            "dynamicRegistration": True,
                            "symbolKind": {"valueSet": list(range(1, 27))},
                            "hierarchicalDocumentSymbolSupport": True,
                        },
                    },
                },
            },
        )

    def _start_server(self) -> None:
        indexing_complete = threading.Event()

        def do_nothing(params: dict) -> None:
            return

        def window_log_message(msg: dict) -> None:
            """Log messages from the language server."""
            message_text = msg.get("message", "")
            level = msg.get("type", 4)  # Default to Log level

            # Map LSP message types to Python logging levels
            level_map = {1: logging.ERROR, 2: logging.WARNING, 3: logging.INFO, 4: logging.DEBUG}  # Error  # Warning  # Info  # Log

            log.log(level_map.get(level, logging.DEBUG), f"LSP: {message_text}")

        def handle_progress(params: dict) -> None:
            """Handle progress notifications from the language server."""
            token = params.get("token", "")
            value = params.get("value", {})

            # Log raw progress for debugging
            log.debug(f"Progress notification received: {params}")

            # Handle different progress notification types
            kind = value.get("kind")

            if kind == "begin":
                title = value.get("title", "Operation in progress")
                message = value.get("message", "")
                percentage = value.get("percentage")

                if percentage is not None:
                    log.debug(f"Progress [{token}]: {title} - {message} ({percentage}%)")
                else:
                    log.info(f"Progress [{token}]: {title} - {message}")

            elif kind == "report":
                message = value.get("message", "")
                percentage = value.get("percentage")

                if percentage is not None:
                    log.info(f"Progress [{token}]: {message} ({percentage}%)")
                elif message:
                    log.info(f"Progress [{token}]: {message}")

            elif kind == "end":
                message = value.get("message", "Operation completed")
                log.info(f"Progress [{token}]: {message}")

        def handle_workspace_configuration(params: dict) -> list:
            """Handle workspace/configuration requests from the server."""
            items = params.get("items", [])
            result: list[Any] = []

            for item in items:
                section = item.get("section", "")

                # Provide default values based on the configuration section
                if section.startswith(("dotnet", "csharp")):
                    # Default configuration for C# settings
                    if "enable" in section or "show" in section or "suppress" in section or "navigate" in section:
                        # Boolean settings
                        result.append(False)
                    elif "scope" in section:
                        # Scope settings - use appropriate enum values
                        if "analyzer_diagnostics_scope" in section:
                            result.append("openFiles")  # BackgroundAnalysisScope
                        elif "compiler_diagnostics_scope" in section:
                            result.append("openFiles")  # CompilerDiagnosticsScope
                        else:
                            result.append("openFiles")
                    elif section == "dotnet_member_insertion_location":
                        # ImplementTypeInsertionBehavior enum
                        result.append("with_other_members_of_the_same_kind")
                    elif section == "dotnet_property_generation_behavior":
                        # ImplementTypePropertyGenerationBehavior enum
                        result.append("prefer_throwing_properties")
                    elif "location" in section or "behavior" in section:
                        # Other enum settings - return null to avoid parsing errors
                        result.append(None)
                    else:
                        # Default for other dotnet/csharp settings
                        result.append(None)
                elif section == "tab_width" or section == "indent_size":
                    # Tab and indent settings
                    result.append(4)
                elif section == "insert_final_newline":
                    # Editor settings
                    result.append(True)
                else:
                    # Unknown configuration - return null
                    result.append(None)

            return result

        def handle_work_done_progress_create(params: dict) -> None:
            """Handle work done progress create requests."""
            # Just acknowledge the request
            return

        def handle_register_capability(params: dict) -> dict:
            """Handle client/registerCapability requests from the language server.

            This is called when the server wants to dynamically register capabilities
            for handling specific document types. For Razor, this includes registering
            handlers for .cshtml and .razor files.

            The registrations are stored in self._registered_capabilities for later use
            by Razor Cohosting services.
            """
            registrations = params.get("registrations", [])
            for reg in registrations:
                method = reg.get("method", "unknown")
                reg_id = reg.get("id", "no-id")
                reg_options = reg.get("registerOptions", {})

                # Store the registration in the capability table
                self._registered_capabilities[reg_id] = {
                    "id": reg_id,
                    "method": method,
                    "registerOptions": reg_options,
                }

                # Log the registration details
                log.info(f"[DynamicRegistration] Registered: method={method}, id={reg_id}")

                # Log document selector if present
                doc_selector = reg_options.get("documentSelector", [])
                if doc_selector:
                    for selector in doc_selector:
                        pattern = selector.get("pattern", "")
                        language = selector.get("language", "")
                        log.info(f"[DynamicRegistration]   - pattern={pattern}, language={language}")

            # Must return empty dict (not None) per LSP spec
            return {}

        def handle_unregister_capability(params: dict) -> dict:
            """Handle client/unregisterCapability requests from the language server.

            This is called when the server wants to unregister previously registered capabilities.
            """
            unregistrations = params.get("unregisterations", [])  # Note: LSP spec uses "unregisterations" (typo in spec)
            for unreg in unregistrations:
                unreg_id = unreg.get("id", "no-id")
                method = unreg.get("method", "unknown")

                # Remove from capability table
                removed = self._registered_capabilities.pop(unreg_id, None)
                if removed:
                    log.info(f"[DynamicRegistration] Unregistered: method={method}, id={unreg_id}")
                else:
                    log.warning(f"[DynamicRegistration] Attempted to unregister unknown capability: id={unreg_id}")

            # Must return empty dict per LSP spec
            return {}

        def handle_project_needs_restore(params: dict) -> None:
            return

        def handle_workspace_indexing_complete(params: dict) -> None:
            indexing_complete.set()

        # Set up notification handlers
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_notification("$/progress", handle_progress)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)
        self.server.on_notification("workspace/projectInitializationComplete", handle_workspace_indexing_complete)
        self.server.on_request("workspace/configuration", handle_workspace_configuration)
        self.server.on_request("window/workDoneProgress/create", handle_work_done_progress_create)
        self.server.on_request("client/registerCapability", handle_register_capability)
        self.server.on_request("client/unregisterCapability", handle_unregister_capability)
        self.server.on_request("workspace/_roslyn_projectNeedsRestore", handle_project_needs_restore)

        log.info("Starting Microsoft.CodeAnalysis.LanguageServer process")

        try:
            self.server.start()
        except Exception as e:
            log.info(f"Failed to start language server process: {e}", logging.ERROR)
            raise SolidLSPException(f"Failed to start C# language server: {e}")

        # Send initialization
        initialize_params = self._get_initialize_params()

        log.info("Sending initialize request to language server")
        try:
            init_response = self.server.send.initialize(initialize_params)
            log.info(f"Received initialize response: {init_response}")
        except Exception as e:
            raise SolidLSPException(f"Failed to initialize C# language server for {self.repository_root_path}: {e}") from e

        # Apply diagnostic capabilities
        self._force_pull_diagnostics(init_response)

        # Verify required capabilities
        capabilities = init_response.get("capabilities", {})
        required_capabilities = [
            "textDocumentSync",
            "definitionProvider",
            "referencesProvider",
            "documentSymbolProvider",
        ]
        missing = [cap for cap in required_capabilities if cap not in capabilities]
        if missing:
            raise RuntimeError(
                f"Language server is missing required capabilities: {', '.join(missing)}. "
                "Initialization failed. Please ensure the correct version of Microsoft.CodeAnalysis.LanguageServer is installed and the .NET runtime is working."
            )

        # Complete initialization
        self.server.notify.initialized({})

        # Open solution and project files
        self._open_solution_and_projects()

        log.info(
            "Microsoft.CodeAnalysis.LanguageServer initialized and ready\n"
            "Waiting for language server to index project files...\n"
            "This may take a while for large projects"
        )

        if indexing_complete.wait(30):  # Wait up to 30 seconds for indexing
            log.info("Indexing complete")
        else:
            log.warning("Timeout waiting for indexing to complete, proceeding anyway")

    def _force_pull_diagnostics(self, init_response: dict | InitializeResult) -> None:
        """
        Apply the diagnostic capabilities hack.
        Forces the server to support pull diagnostics.
        """
        capabilities = init_response.get("capabilities", {})
        diagnostic_provider: Any = capabilities.get("diagnosticProvider", {})

        # Add the diagnostic capabilities hack
        if isinstance(diagnostic_provider, dict):
            diagnostic_provider.update(
                {
                    "interFileDependencies": True,
                    "workDoneProgress": True,
                    "workspaceDiagnostics": True,
                }
            )
            log.debug("Applied diagnostic capabilities hack for better C# diagnostics")

    def _open_solution_and_projects(self) -> None:
        """
        Open solution and project files using notifications.
        """
        # Find solution file
        solution_file = None
        for filename in breadth_first_file_scan(self.repository_root_path):
            if filename.endswith(".sln"):
                solution_file = filename
                break

        # Send solution/open notification if solution file found
        if solution_file:
            solution_uri = PathUtils.path_to_uri(solution_file)
            self.server.notify.send_notification("solution/open", {"solution": solution_uri})
            log.debug(f"Opened solution file: {solution_file}")

        # Find and open project files
        project_files = []
        for filename in breadth_first_file_scan(self.repository_root_path):
            if filename.endswith(".csproj"):
                project_files.append(filename)

        # Send project/open notifications for each project file
        if project_files:
            project_uris = [PathUtils.path_to_uri(project_file) for project_file in project_files]
            self.server.notify.send_notification("project/open", {"projects": project_uris})
            log.debug(f"Opened project files: {project_files}")

    @override
    def _get_wait_time_for_cross_file_referencing(self) -> float:
        return 2
