"""
CSharp Language Server using Microsoft.CodeAnalysis.LanguageServer (Official Roslyn-based LSP server)

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
        Example: "D:/GitHub/razor/artifacts/bin/Microsoft.AspNetCore.Razor.LanguageServer/Release/net10.0"

    runtime_dependencies (list[dict]):
        Override default runtime dependency configurations.

    dotnet_runtime_major_version (str):
        Override the .NET runtime major version used for cache directories and fallback paths.
        Default: Auto-detected from system, falls back to "9" if not found.
        The auto-detection finds the highest installed .NET version (9+) on the system.
        Set this explicitly when overriding runtime_dependencies to use a specific version.

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
import tarfile
import threading
import urllib.request
import zipfile
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from overrides import override

from solidlsp.ls import LanguageServerDependencyProvider, SolidLanguageServer
from solidlsp.ls_config import LanguageServerConfig
from solidlsp.ls_exceptions import SolidLSPException
from solidlsp.ls_utils import PathUtils
from solidlsp.lsp_protocol_handler.lsp_types import InitializeParams, InitializeResult
from solidlsp.settings import SolidLSPSettings
from solidlsp.util.zip import SafeZipExtractor

from .common import RuntimeDependency, RuntimeDependencyCollection

log = logging.getLogger(__name__)

# Path to bundled Razor extension files (relative to this module)
_BUNDLED_RAZOR_EXTENSION_DIR = Path(__file__).parent / "razor_extension"

# Version constants - update these when upgrading
_CSHARP_LANGUAGE_SERVER_VERSION = "5.0.0-1.25329.6"
_DOTNET_RUNTIME_VERSION = "9.0.6"
_DOTNET_RUNTIME_MAJOR_VERSION = "9"

_RUNTIME_DEPENDENCIES = [
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for Windows (x64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.win-x64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="win-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/win-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for Windows (ARM64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.win-arm64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="win-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/win-arm64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for macOS (x64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.osx-x64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="osx-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/osx-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for macOS (ARM64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.osx-arm64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="osx-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/osx-arm64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for Linux (x64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.linux-x64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="linux-x64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/linux-x64",
    ),
    RuntimeDependency(
        id="CSharpLanguageServer",
        description="Microsoft.CodeAnalysis.LanguageServer for Linux (ARM64)",
        package_name="Microsoft.CodeAnalysis.LanguageServer.linux-arm64",
        package_version=_CSHARP_LANGUAGE_SERVER_VERSION,
        platform_id="linux-arm64",
        archive_type="nupkg",
        binary_name="Microsoft.CodeAnalysis.LanguageServer.dll",
        extract_path="content/LanguageServer/linux-arm64",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for Windows (x64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-win-x64.zip",
        platform_id="win-x64",
        archive_type="zip",
        binary_name="dotnet.exe",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for Linux (x64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-linux-x64.tar.gz",
        platform_id="linux-x64",
        archive_type="tar.gz",
        binary_name="dotnet",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for Linux (ARM64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-linux-arm64.tar.gz",
        platform_id="linux-arm64",
        archive_type="tar.gz",
        binary_name="dotnet",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for macOS (x64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-osx-x64.tar.gz",
        platform_id="osx-x64",
        archive_type="tar.gz",
        binary_name="dotnet",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for macOS (ARM64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-osx-arm64.tar.gz",
        platform_id="osx-arm64",
        archive_type="tar.gz",
        binary_name="dotnet",
    ),
    RuntimeDependency(
        id="DotNetRuntime",
        description=f".NET {_DOTNET_RUNTIME_MAJOR_VERSION} Runtime for Windows (ARM64)",
        url=f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{_DOTNET_RUNTIME_VERSION}/dotnet-runtime-{_DOTNET_RUNTIME_VERSION}-win-arm64.zip",
        platform_id="win-arm64",
        archive_type="zip",
        binary_name="dotnet.exe",
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
    Provides C# specific instantiation of the LanguageServer class using `Microsoft.CodeAnalysis.LanguageServer`,
    the official Roslyn-based language server from Microsoft.

    ## Razor Support

    This language server supports Razor (.razor, .cshtml) files through the Razor extension.
    Razor support is enabled by default if the razor_extension files are available.

    To disable Razor support, set ls_specific_settings["csharp"]["enable_razor"] = False.

    The Razor extension provides:
    - IntelliSense for Razor syntax
    - Go to Definition for components and C# code
    - Hover information
    - Diagnostics for Razor files

    ## Runtime Dependency Overrides

    You can pass a list of runtime dependency overrides in ls_specific_settings["csharp"]["runtime_dependencies"]. This is a list of
    dicts, each containing at least the "id" key, and optionally "platform_id" to uniquely identify the dependency to override.
    For example, to override the URL of the .NET runtime on windows-x64, add the entry:

    ```
        {
            "id": "DotNetRuntime",
            "platform_id": "win-x64",
            "url": "https://example.com/custom-dotnet-runtime.zip"
        }
    ```

    See the `_RUNTIME_DEPENDENCIES` variable above for the available dependency ids and platform_ids.
    """

    def __init__(self, config: LanguageServerConfig, repository_root_path: str, solidlsp_settings: SolidLSPSettings):
        """
        Creates a CSharpLanguageServer instance. This class is not meant to be instantiated directly.
        Use LanguageServer.create() instead.
        """
        super().__init__(config, repository_root_path, None, "csharp", solidlsp_settings)

        self.initialization_complete = threading.Event()

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

    def _create_dependency_provider(self) -> LanguageServerDependencyProvider:
        return self.DependencyProvider(self._custom_settings, self._ls_resources_dir, self._solidlsp_settings, self.repository_root_path)

    @override
    def is_ignored_dirname(self, dirname: str) -> bool:
        return super().is_ignored_dirname(dirname) or dirname in ["bin", "obj", "packages", ".vs"]

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

            # Determine .NET runtime major version:
            # 1. Use explicit setting if provided
            # 2. Auto-detect from system if available
            # 3. Fall back to default constant
            explicit_version = custom_settings.get("dotnet_runtime_major_version")
            if explicit_version is not None:
                self._dotnet_runtime_major_version = cast(str, explicit_version)
                log.debug(f"Using explicitly configured .NET major version: {self._dotnet_runtime_major_version}")
            else:
                detected_version = self._detect_system_dotnet_major_version()
                if detected_version is not None:
                    self._dotnet_runtime_major_version = detected_version
                    log.info(f"Auto-detected system .NET major version: {self._dotnet_runtime_major_version}")
                else:
                    self._dotnet_runtime_major_version = _DOTNET_RUNTIME_MAJOR_VERSION
                    log.debug(f"Using default .NET major version: {self._dotnet_runtime_major_version}")

            self._dotnet_path, self._language_server_path = self._ensure_server_installed()

            # Check if Razor support is enabled
            self._enable_razor = cast(bool, custom_settings.get("enable_razor", True))
            self._razor_extension_dir: Path | None = None

            if self._enable_razor:
                self._razor_extension_dir = self._ensure_razor_extension_installed()

        @staticmethod
        def _detect_system_dotnet_major_version() -> str | None:
            """
            Detect the highest .NET runtime major version installed on the system.
            Returns the major version as a string (e.g., "10", "9") or None if not found.

            Only considers .NET 9+ as earlier versions are not supported by the language server.
            """
            system_dotnet = shutil.which("dotnet")
            if not system_dotnet:
                return None

            try:
                result = subprocess.run(
                    [system_dotnet, "--list-runtimes"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )

                # Parse runtime versions from output like:
                # Microsoft.NETCore.App 9.0.6 [/usr/share/dotnet/shared/Microsoft.NETCore.App]
                # Microsoft.NETCore.App 10.0.0 [...]
                import re

                versions: list[int] = []
                for line in result.stdout.splitlines():
                    match = re.search(r"Microsoft\.NETCore\.App\s+(\d+)\.", line)
                    if match:
                        major_version = int(match.group(1))
                        # Only consider .NET 9+ (required by Roslyn LS)
                        if major_version >= 9:
                            versions.append(major_version)

                if versions:
                    highest_version = max(versions)
                    return str(highest_version)

            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                log.debug(f"Failed to detect system .NET version: {e}")

            return None

        @staticmethod
        def _check_system_dotnet_has_supported_runtime() -> str | None:
            """
            Check if system dotnet has a supported .NET runtime (9 or 10).
            Returns the dotnet executable path if a supported runtime is found, None otherwise.

            The language server supports .NET 9+, with .NET 10 preferred for newer versions.
            """
            system_dotnet = shutil.which("dotnet")
            if not system_dotnet:
                return None

            try:
                result = subprocess.run(
                    [system_dotnet, "--list-runtimes"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                # Prefer .NET 10 first as it's supported by newer language server versions
                if "Microsoft.NETCore.App 10." in result.stdout:
                    log.info("Found system .NET 10 runtime")
                    return system_dotnet
                elif "Microsoft.NETCore.App 9." in result.stdout:
                    log.info("Found system .NET 9 runtime")
                    return system_dotnet
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                log.debug(f"Failed to check system .NET runtime: {e}")

            return None

        def create_launch_command(self) -> list[str] | str:
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
                local_razor_dir = Path(local_razor_path)
                local_razor_dll = local_razor_dir / "Microsoft.VisualStudioCode.RazorExtension.dll"
                if local_razor_dll.exists():
                    log.info(f"Using local Razor extension from {local_razor_dir}")
                    return local_razor_dir
                else:
                    log.warning(f"Local Razor extension path specified but DLL not found: {local_razor_dll}")

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
                local_ls_dll = Path(local_ls_path) / "Microsoft.CodeAnalysis.LanguageServer.dll"
                if local_ls_dll.exists():
                    log.info(f"Using local language server from {local_ls_path}")
                    # Use system dotnet for local builds
                    system_dotnet = shutil.which("dotnet")
                    if system_dotnet:
                        return system_dotnet, str(local_ls_dll)
                    else:
                        log.warning("Local language server specified but dotnet not found in PATH")
                else:
                    log.warning(f"Local language server path specified but DLL not found: {local_ls_dll}")

            runtime_dependency_overrides = cast(list[dict[str, Any]], self._custom_settings.get("runtime_dependencies", []))

            log.debug("Resolving runtime dependencies")

            runtime_dependencies = RuntimeDependencyCollection(
                _RUNTIME_DEPENDENCIES,
                overrides=runtime_dependency_overrides,
            )

            log.debug(
                f"Available runtime dependencies: {runtime_dependencies.get_dependencies_for_current_platform}",
            )

            # Find the dependencies for our platform
            lang_server_dep = runtime_dependencies.get_single_dep_for_current_platform("CSharpLanguageServer")
            dotnet_runtime_dep = runtime_dependencies.get_single_dep_for_current_platform("DotNetRuntime")
            dotnet_path = self._ensure_dotnet_runtime(dotnet_runtime_dep)
            server_dll_path = self._ensure_language_server(lang_server_dep)

            return dotnet_path, server_dll_path

        def _ensure_dotnet_runtime(self, dotnet_runtime_dep: RuntimeDependency) -> str:
            """Ensure .NET runtime is available and return the dotnet executable path."""
            # TODO: use RuntimeDependency util methods instead of custom validation/download logic

            # Check if system dotnet has a supported runtime (.NET 9 or 10)
            system_dotnet = self._check_system_dotnet_has_supported_runtime()
            if system_dotnet:
                return system_dotnet

            # Download .NET runtime using config
            return self._ensure_dotnet_runtime_from_config(dotnet_runtime_dep)

        def _ensure_language_server(self, lang_server_dep: RuntimeDependency) -> str:
            """Ensure language server is available and return the DLL path."""
            package_name = lang_server_dep.package_name
            package_version = lang_server_dep.package_version

            server_dir = Path(self._ls_resources_dir) / f"{package_name}.{package_version}"
            assert lang_server_dep.binary_name is not None
            server_dll = server_dir / lang_server_dep.binary_name

            if server_dll.exists():
                log.info(f"Using cached Microsoft.CodeAnalysis.LanguageServer from {server_dll}")
                return str(server_dll)

            # Download and install the language server
            log.info(f"Downloading {package_name} version {package_version}...")
            assert package_version is not None
            assert package_name is not None
            package_path = self._download_nuget_package_direct(package_name, package_version)

            # Extract and install
            self._extract_language_server(lang_server_dep, package_path, server_dir)

            if not server_dll.exists():
                raise SolidLSPException("Microsoft.CodeAnalysis.LanguageServer.dll not found after extraction")

            # Make executable on Unix systems
            if platform.system().lower() != "windows":
                server_dll.chmod(0o755)

            log.info(f"Successfully installed Microsoft.CodeAnalysis.LanguageServer to {server_dll}")
            return str(server_dll)

        def _extract_language_server(self, lang_server_dep: RuntimeDependency, package_path: Path, server_dir: Path) -> None:
            """Extract language server files from downloaded package."""
            dotnet_major = self._dotnet_runtime_major_version
            extract_path = lang_server_dep.extract_path or f"lib/net{dotnet_major}.0"
            source_dir = package_path / extract_path

            if not source_dir.exists():
                # Try alternative locations
                for possible_dir in [
                    package_path / "tools" / f"net{dotnet_major}.0" / "any",
                    package_path / "lib" / f"net{dotnet_major}.0",
                    package_path / "contentFiles" / "any" / f"net{dotnet_major}.0",
                ]:
                    if possible_dir.exists():
                        source_dir = possible_dir
                        break
                else:
                    raise SolidLSPException(f"Could not find language server files in package. Searched in {package_path}")

            # Copy files to cache directory
            server_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_dir, server_dir, dirs_exist_ok=True)

        def _download_nuget_package_direct(self, package_name: str, package_version: str) -> Path:
            """
            Download a NuGet package directly from the Azure NuGet feed.
            Returns the path to the extracted package directory.
            """
            azure_feed_url = "https://pkgs.dev.azure.com/azure-public/vside/_packaging/vs-impl/nuget/v3/index.json"

            # Create temporary directory for package download
            temp_dir = Path(self._ls_resources_dir) / "temp_downloads"
            temp_dir.mkdir(parents=True, exist_ok=True)

            try:
                # First, get the service index from the Azure feed
                log.debug("Fetching NuGet service index from Azure feed...")
                with urllib.request.urlopen(azure_feed_url) as response:
                    service_index = json.loads(response.read().decode())

                # Find the package base address (for downloading packages)
                package_base_address = None
                for resource in service_index.get("resources", []):
                    if resource.get("@type") == "PackageBaseAddress/3.0.0":
                        package_base_address = resource.get("@id")
                        break

                if not package_base_address:
                    raise SolidLSPException("Could not find package base address in Azure NuGet feed")

                # Construct the download URL for the specific package
                package_id_lower = package_name.lower()
                package_version_lower = package_version.lower()
                package_url = f"{package_base_address.rstrip('/')}/{package_id_lower}/{package_version_lower}/{package_id_lower}.{package_version_lower}.nupkg"

                log.debug(f"Downloading package from: {package_url}")

                # Download the .nupkg file
                nupkg_file = temp_dir / f"{package_name}.{package_version}.nupkg"
                urllib.request.urlretrieve(package_url, nupkg_file)

                # Extract the .nupkg file (it's just a zip file)
                package_extract_dir = temp_dir / f"{package_name}.{package_version}"
                package_extract_dir.mkdir(exist_ok=True)

                # Use SafeZipExtractor to handle long paths and skip errors
                extractor = SafeZipExtractor(archive_path=nupkg_file, extract_dir=package_extract_dir, verbose=False)
                extractor.extract_all()

                # Clean up the nupkg file
                nupkg_file.unlink()

                log.info(f"Successfully downloaded and extracted {package_name} version {package_version}")
                return package_extract_dir

            except Exception as e:
                raise SolidLSPException(
                    f"Failed to download package {package_name} version {package_version} from Azure NuGet feed: {e}"
                ) from e

        def _ensure_dotnet_runtime_from_config(self, dotnet_runtime_dep: RuntimeDependency) -> str:
            """
            Download and install .NET runtime using runtime dependency configuration.
            Returns the path to the dotnet executable.

            Note: This method is called after system dotnet check has failed,
            so it proceeds directly to downloading the runtime.
            """
            # TODO: use RuntimeDependency util methods instead of custom download logic
            dotnet_major = self._dotnet_runtime_major_version

            # Download .NET runtime using config
            dotnet_dir = Path(self._ls_resources_dir) / f"dotnet-runtime-{dotnet_major}.0"
            assert dotnet_runtime_dep.binary_name is not None, "Runtime dependency must have a binary_name"
            dotnet_exe = dotnet_dir / dotnet_runtime_dep.binary_name

            if dotnet_exe.exists():
                log.info(f"Using cached .NET runtime from {dotnet_exe}")
                return str(dotnet_exe)

            # Download .NET runtime
            log.info(f"Downloading .NET {dotnet_major} runtime...")
            dotnet_dir.mkdir(parents=True, exist_ok=True)

            custom_dotnet_runtime_url = self._custom_settings.get("dotnet_runtime_url")
            if custom_dotnet_runtime_url is not None:
                log.info(f"Using custom .NET runtime url: {custom_dotnet_runtime_url}")
                url = custom_dotnet_runtime_url
            else:
                url = dotnet_runtime_dep.url

            archive_type = dotnet_runtime_dep.archive_type

            # Download the runtime
            download_path = dotnet_dir / f"dotnet-runtime.{archive_type}"
            try:
                log.debug(f"Downloading from {url}")
                urllib.request.urlretrieve(url, download_path)

                # Extract the archive
                if archive_type == "zip":
                    with zipfile.ZipFile(download_path, "r") as zip_ref:
                        zip_ref.extractall(dotnet_dir)
                else:
                    # tar.gz
                    with tarfile.open(download_path, "r:gz") as tar_ref:
                        tar_ref.extractall(dotnet_dir)

                # Remove the archive
                download_path.unlink()

                # Make dotnet executable on Unix
                if platform.system().lower() != "windows":
                    dotnet_exe.chmod(0o755)

                log.info(f"Successfully installed .NET {dotnet_major} runtime to {dotnet_exe}")
                return str(dotnet_exe)

            except Exception as e:
                raise SolidLSPException(f"Failed to download .NET {dotnet_major} runtime from {url}: {e}") from e

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
            self.completions_available.set()

        def apply_text_edit(content: str, change: dict) -> str:
            """Apply a single LSP text edit to content.

            Args:
                content: The current document content
                change: A change object with 'range' (optional) and 'newText'

            Returns:
                The content after applying the edit

            """
            new_text = change.get("newText", "")
            range_info = change.get("range")

            # If no range is provided, it's a full document replacement
            if range_info is None:
                return new_text

            # Parse range
            start = range_info.get("start", {})
            end = range_info.get("end", {})
            start_line = start.get("line", 0)
            start_char = start.get("character", 0)
            end_line = end.get("line", 0)
            end_char = end.get("character", 0)

            # Split content into lines (preserving line endings)
            lines = content.splitlines(keepends=True)

            # Handle empty content
            if not lines:
                lines = [""]

            # Ensure we have enough lines
            while len(lines) <= max(start_line, end_line):
                lines.append("")

            # Calculate start and end offsets
            start_offset = sum(len(lines[i]) for i in range(start_line)) + start_char
            end_offset = sum(len(lines[i]) for i in range(end_line)) + end_char

            # Apply the edit
            result = content[:start_offset] + new_text + content[end_offset:]
            return result

        def handle_razor_update_csharp_buffer(params: dict) -> None:
            """
            Handle razor/updateCSharpBuffer notifications from Razor Language Server.
            This caches the generated C# content for Razor files so we can provide
            document symbols for them.

            The changes can be either:
            - Full document replacement (no range in change)
            - Incremental edits (with range specifying start/end positions)
            """
            log.debug(f"[Razor] Received razor/updateCSharpBuffer: {list(params.keys())}")
            host_document_path = params.get("hostDocumentFilePath")
            host_document_version = params.get("hostDocumentVersion")
            changes = params.get("changes", [])

            if not host_document_path or host_document_version is None:
                log.debug("Received razor/updateCSharpBuffer without required fields")
                return

            # Convert to URI format for consistency
            host_document_uri = Path(host_document_path).as_uri()

            # Get existing content or start fresh
            existing = self._razor_virtual_documents.get(host_document_uri)
            if existing:
                _, current_content, virtual_uri = existing
            else:
                current_content = ""
                # Create virtual C# document URI (matching Razor convention)
                virtual_uri = host_document_uri + ".ide.g.cs"

            # Apply changes in order
            if changes:
                for change in changes:
                    current_content = apply_text_edit(current_content, change)

            self._razor_virtual_documents[host_document_uri] = (
                host_document_version,
                current_content,
                virtual_uri,
            )

            log.debug(
                f"Updated Razor virtual C# document: {host_document_path} "
                f"(version {host_document_version}, {len(current_content)} chars)"
            )

        def handle_razor_document_symbol(params: dict) -> list | None:
            """
            Handle razor/documentSymbol requests from Razor Language Server.
            This is called when Razor LS needs document symbols for a .razor/.cshtml file.
            We forward the request to get symbols from the generated C# content.
            """
            log.info(f"[Razor] Received razor/documentSymbol request: {params}")
            identifier = params.get("identifier", {})
            text_document_identifier = identifier.get("textDocumentIdentifier", {})
            document_uri = text_document_identifier.get("uri", "")

            if not document_uri:
                log.debug("Received razor/documentSymbol without document URI")
                return []

            # Look up the virtual C# document for this Razor file
            virtual_doc_info = self._razor_virtual_documents.get(document_uri)
            if not virtual_doc_info:
                log.debug(f"No virtual C# document found for {document_uri}")
                return []

            version, content, virtual_uri = virtual_doc_info

            # Request document symbols from the C# language server
            # We need to ensure the virtual document is "open" in the LSP
            try:
                # First, open the virtual document if not already opened
                # This is a simplified approach - in production, we'd track open state
                self.server.notify.did_open_text_document(
                    {
                        "textDocument": {
                            "uri": virtual_uri,
                            "languageId": "csharp",
                            "version": version,
                            "text": content,
                        }
                    }
                )

                # Now request document symbols
                result = self.server.send.document_symbol({"textDocument": {"uri": virtual_uri}})

                log.debug(f"Got {len(result) if result else 0} symbols for Razor file {document_uri}")

                return result

            except Exception as e:
                log.warning(f"Failed to get document symbols for Razor file {document_uri}: {e}")
                return []

        def log_unhandled_notification(method: str, params: dict) -> None:
            """Log unhandled notifications for debugging Razor communication."""
            if method.startswith("razor/"):
                log.info(f"[Razor] Unhandled notification: {method} - keys: {list(params.keys()) if params else 'None'}")

        # Set up notification handlers
        self.server.on_notification("window/logMessage", window_log_message)
        self.server.on_notification("$/progress", handle_progress)
        self.server.on_notification("textDocument/publishDiagnostics", do_nothing)
        self.server.on_notification("workspace/projectInitializationComplete", handle_workspace_indexing_complete)
        self.server.on_notification("razor/updateCSharpBuffer", handle_razor_update_csharp_buffer)
        self.server.on_notification("razor/updateHtmlBuffer", do_nothing)  # HTML buffer updates (not needed for symbols)
        self.server.on_request("workspace/configuration", handle_workspace_configuration)
        self.server.on_request("window/workDoneProgress/create", handle_work_done_progress_create)
        self.server.on_request("client/registerCapability", handle_register_capability)
        self.server.on_request("client/unregisterCapability", handle_unregister_capability)
        self.server.on_request("workspace/_roslyn_projectNeedsRestore", handle_project_needs_restore)
        self.server.on_request("razor/documentSymbol", handle_razor_document_symbol)

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

        self.initialization_complete.set()

        log.info(
            "Microsoft.CodeAnalysis.LanguageServer initialized and ready\n"
            "Waiting for language server to index project files...\n"
            "This may take a while for large projects"
        )

        if self.completions_available.wait(30):  # Wait up to 30 seconds for indexing
            log.info("Indexing complete")
        else:
            log.warning("Timeout waiting for indexing to complete, proceeding anyway")
            self.completions_available.set()

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
