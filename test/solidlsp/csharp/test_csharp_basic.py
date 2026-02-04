import os
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest
from sensai.util import logging

from serena.util.logging import SuspendedLoggersContext
from solidlsp import SolidLanguageServer
from solidlsp.language_servers.csharp_language_server import (
    CSharpLanguageServer,
    breadth_first_file_scan,
    find_solution_or_project_file,
)
from solidlsp.ls_config import Language, LanguageServerConfig
from solidlsp.ls_utils import SymbolUtils
from solidlsp.settings import SolidLSPSettings


@pytest.mark.csharp
class TestCSharpLanguageServer:
    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_find_symbol(self, language_server: SolidLanguageServer) -> None:
        """Test finding symbols in the full symbol tree."""
        symbols = language_server.request_full_symbol_tree()
        assert SymbolUtils.symbol_tree_contains_name(symbols, "Program"), "Program class not found in symbol tree"
        assert SymbolUtils.symbol_tree_contains_name(symbols, "Calculator"), "Calculator class not found in symbol tree"
        assert SymbolUtils.symbol_tree_contains_name(symbols, "Add"), "Add method not found in symbol tree"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_get_document_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test getting document symbols from a C# file."""
        file_path = os.path.join("Program.cs")
        symbols = language_server.request_document_symbols(file_path).get_all_symbols_and_roots()

        # Check that we have symbols
        assert len(symbols) > 0

        # Flatten the symbols if they're nested
        if isinstance(symbols[0], list):
            symbols = symbols[0]

        # Look for expected classes
        class_names = [s.get("name") for s in symbols if s.get("kind") == 5]  # 5 is class
        assert "Program" in class_names
        assert "Calculator" in class_names

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_find_referencing_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test finding references using symbol selection range."""
        file_path = os.path.join("Program.cs")
        symbols = language_server.request_document_symbols(file_path).get_all_symbols_and_roots()
        add_symbol = None
        # Handle nested symbol structure
        symbol_list = symbols[0] if symbols and isinstance(symbols[0], list) else symbols
        for sym in symbol_list:
            # Symbol names are normalized to base form (e.g., "Add" not "Add(int, int) : int")
            if sym.get("name") == "Add":
                add_symbol = sym
                break
        assert add_symbol is not None, "Could not find 'Add' method symbol in Program.cs"
        sel_start = add_symbol["selectionRange"]["start"]
        refs = language_server.request_references(file_path, sel_start["line"], sel_start["character"] + 1)
        assert any(
            "Program.cs" in ref.get("relativePath", "") for ref in refs
        ), "Program.cs should reference Add method (tried all positions in selectionRange)"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_nested_namespace_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test getting symbols from nested namespace."""
        file_path = os.path.join("Models", "Person.cs")
        symbols = language_server.request_document_symbols(file_path).get_all_symbols_and_roots()

        # Check that we have symbols
        assert len(symbols) > 0

        # Flatten the symbols if they're nested
        if isinstance(symbols[0], list):
            symbols = symbols[0]

        # Check that we have the Person class
        assert any(s.get("name") == "Person" and s.get("kind") == 5 for s in symbols)

        # Check for properties and methods (names are normalized to base form)
        symbol_names = [s.get("name") for s in symbols]
        assert "Name" in symbol_names, "Name property not found"
        assert "Age" in symbol_names, "Age property not found"
        assert "Email" in symbol_names, "Email property not found"
        assert "ToString" in symbol_names, "ToString method not found"
        assert "IsAdult" in symbol_names, "IsAdult method not found"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_find_referencing_symbols_across_files(self, language_server: SolidLanguageServer) -> None:
        """Test finding references to Calculator.Subtract method across files."""
        # First, find the Subtract method in Program.cs
        file_path = os.path.join("Program.cs")
        symbols = language_server.request_document_symbols(file_path).get_all_symbols_and_roots()

        # Flatten the symbols if they're nested
        symbol_list = symbols[0] if symbols and isinstance(symbols[0], list) else symbols

        subtract_symbol = None
        for sym in symbol_list:
            # Symbol names are normalized to base form (e.g., "Subtract" not "Subtract(int, int) : int")
            if sym.get("name") == "Subtract":
                subtract_symbol = sym
                break

        assert subtract_symbol is not None, "Could not find 'Subtract' method symbol in Program.cs"

        # Get references to the Subtract method
        sel_start = subtract_symbol["selectionRange"]["start"]
        refs = language_server.request_references(file_path, sel_start["line"], sel_start["character"] + 1)

        # Should find references where the method is called
        ref_files = cast(list[str], [ref.get("relativePath", "") for ref in refs])
        print(f"Found references: {refs}")
        print(f"Reference files: {ref_files}")

        # Check that we have reference in Models/Person.cs where Calculator.Subtract is called
        # Note: New Roslyn version doesn't include the definition itself as a reference (more correct behavior)
        assert any(
            os.path.join("Models", "Person.cs") in ref_file for ref_file in ref_files
        ), "Should find reference in Models/Person.cs where Calculator.Subtract is called"
        assert len(refs) > 0, "Should find at least one reference"

        # check for a second time, since the first call may trigger initialization and change the state of the LS
        refs_second_call = language_server.request_references(file_path, sel_start["line"], sel_start["character"] + 1)
        assert refs_second_call == refs, "Second call to request_references should return the same results"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_hover_includes_type_information(self, language_server: SolidLanguageServer) -> None:
        """Test that hover information is available and includes type information."""
        file_path = os.path.join("Models", "Person.cs")

        # Open the file first
        language_server.open_file(file_path)

        # Test 1: Hover over the Name property (line 6, column 23 - on "Name")
        # Source: public string Name { get; set; }
        hover_info = language_server.request_hover(file_path, 6, 23)

        # Verify hover returns content
        assert hover_info is not None, "Hover should return information for Name property"
        assert isinstance(hover_info, dict), "Hover should be a dict"
        assert "contents" in hover_info, "Hover should have contents"

        contents = hover_info["contents"]
        assert isinstance(contents, dict), "Hover contents should be a dict"
        assert "value" in contents, "Hover contents should have value"
        hover_text = contents["value"]

        # Verify the hover contains property signature with type
        assert "string" in hover_text, f"Hover should include 'string' type, got: {hover_text}"
        assert "Name" in hover_text, f"Hover should include 'Name' property name, got: {hover_text}"

        # Test 2: Hover over the IsAdult method (line 22, column 21 - on "IsAdult")
        # Source: public bool IsAdult()
        hover_method = language_server.request_hover(file_path, 22, 21)

        # Verify method hover returns content
        assert hover_method is not None, "Hover should return information for IsAdult method"
        assert isinstance(hover_method, dict), "Hover should be a dict"
        assert "contents" in hover_method, "Hover should have contents"

        contents = hover_method["contents"]
        assert isinstance(contents, dict), "Hover contents should be a dict"
        assert "value" in contents, "Hover contents should have value"
        method_hover_text = contents["value"]

        # Verify the hover contains method signature with return type
        assert "bool" in method_hover_text, f"Hover should include 'bool' return type, got: {method_hover_text}"
        assert "IsAdult" in method_hover_text, f"Hover should include 'IsAdult' method name, got: {method_hover_text}"


@pytest.mark.csharp
class TestCSharpSolutionProjectOpening:
    """Test C# language server solution and project opening functionality."""

    def test_breadth_first_file_scan(self):
        """Test that breadth_first_file_scan finds files in breadth-first order."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test directory structure
            (temp_path / "file1.txt").touch()
            (temp_path / "subdir1").mkdir()
            (temp_path / "subdir1" / "file2.txt").touch()
            (temp_path / "subdir2").mkdir()
            (temp_path / "subdir2" / "file3.txt").touch()
            (temp_path / "subdir1" / "subdir3").mkdir()
            (temp_path / "subdir1" / "subdir3" / "file4.txt").touch()

            # Scan files
            files = list(breadth_first_file_scan(str(temp_path)))
            filenames = [os.path.basename(f) for f in files]

            # Should find all files
            assert len(files) == 4
            assert "file1.txt" in filenames
            assert "file2.txt" in filenames
            assert "file3.txt" in filenames
            assert "file4.txt" in filenames

            # file1.txt should be found first (breadth-first)
            assert filenames[0] == "file1.txt"

    def test_find_solution_or_project_file_with_solution(self):
        """Test that find_solution_or_project_file prefers .sln files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create both .sln and .csproj files
            solution_file = temp_path / "MySolution.sln"
            project_file = temp_path / "MyProject.csproj"
            solution_file.touch()
            project_file.touch()

            result = find_solution_or_project_file(str(temp_path))

            # Should prefer .sln file
            assert result == str(solution_file)

    def test_find_solution_or_project_file_with_project_only(self):
        """Test that find_solution_or_project_file falls back to .csproj files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create only .csproj file
            project_file = temp_path / "MyProject.csproj"
            project_file.touch()

            result = find_solution_or_project_file(str(temp_path))

            # Should return .csproj file
            assert result == str(project_file)

    def test_find_solution_or_project_file_with_nested_files(self):
        """Test that find_solution_or_project_file finds files in subdirectories."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create nested structure
            (temp_path / "src").mkdir()
            solution_file = temp_path / "src" / "MySolution.sln"
            solution_file.touch()

            result = find_solution_or_project_file(str(temp_path))

            # Should find nested .sln file
            assert result == str(solution_file)

    def test_find_solution_or_project_file_returns_none_when_no_files(self):
        """Test that find_solution_or_project_file returns None when no .sln or .csproj files exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create some other files
            (temp_path / "readme.txt").touch()
            (temp_path / "other.cs").touch()

            result = find_solution_or_project_file(str(temp_path))

            # Should return None
            assert result is None

    def test_find_solution_or_project_file_prefers_solution_breadth_first(self):
        """Test that solution files are preferred even when deeper in the tree."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create .csproj at root and .sln in subdirectory
            project_file = temp_path / "MyProject.csproj"
            project_file.touch()

            (temp_path / "src").mkdir()
            solution_file = temp_path / "src" / "MySolution.sln"
            solution_file.touch()

            result = find_solution_or_project_file(str(temp_path))

            # Should still prefer .sln file even though it's deeper
            assert result == str(solution_file)

    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_server_installed")
    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer._start_server")
    def test_csharp_language_server_logs_solution_discovery(self, mock_start_server, mock_ensure_server_installed):
        """Test that CSharpLanguageServer logs solution/project discovery during initialization."""
        mock_ensure_server_installed.return_value = ("/usr/bin/dotnet", "/path/to/server.dll")

        # Create test directory with solution file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            solution_file = temp_path / "TestSolution.sln"
            solution_file.touch()

            mock_config = Mock(spec=LanguageServerConfig)
            mock_config.ignored_paths = []

            # Create CSharpLanguageServer instance
            mock_settings = Mock(spec=SolidLSPSettings)
            mock_settings.ls_resources_dir = "/tmp/test_ls_resources"
            mock_settings.project_data_relative_path = "project_data"

            with SuspendedLoggersContext():
                logging.getLogger().setLevel(logging.DEBUG)
                with logging.MemoryLoggerContext() as mem_log:
                    CSharpLanguageServer(mock_config, str(temp_path), mock_settings)

                    # Verify that logger was called with solution file discovery
                    expected_log_msg = f"Found solution/project file: {solution_file}"
                    assert expected_log_msg in mem_log.get_log()

    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_server_installed")
    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer._start_server")
    def test_csharp_language_server_logs_no_solution_warning(self, mock_start_server, mock_ensure_server_installed):
        """Test that CSharpLanguageServer logs warning when no solution/project files are found."""
        # Mock the server installation
        mock_ensure_server_installed.return_value = ("/usr/bin/dotnet", "/path/to/server.dll")

        # Create empty test directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Mock logger to capture log messages
            mock_config = Mock(spec=LanguageServerConfig)
            mock_config.ignored_paths = []

            mock_settings = Mock(spec=SolidLSPSettings)
            mock_settings.ls_resources_dir = "/tmp/test_ls_resources"
            mock_settings.project_data_relative_path = "project_data"

            # Create CSharpLanguageServer instance
            with SuspendedLoggersContext():
                logging.getLogger().setLevel(logging.DEBUG)
                with logging.MemoryLoggerContext() as mem_log:
                    CSharpLanguageServer(mock_config, str(temp_path), mock_settings)

                    # Verify that logger was called with warning about no solution/project files
                    expected_log_msg = "No .sln or .csproj file found, language server will attempt auto-discovery"
                    assert expected_log_msg in mem_log.get_log()

    def test_solution_and_project_opening_with_real_test_repo(self):
        """Test solution and project opening with the actual C# test repository."""
        # Get the C# test repo path
        test_repo_path = Path(__file__).parent.parent.parent / "resources" / "repos" / "csharp" / "test_repo"

        if not test_repo_path.exists():
            pytest.skip("C# test repository not found")

        # Test solution/project discovery in the real test repo
        result = find_solution_or_project_file(str(test_repo_path))

        # Should find either .sln or .csproj file
        assert result is not None
        assert result.endswith((".sln", ".csproj"))

        # Verify the file actually exists
        assert os.path.exists(result)


@pytest.mark.csharp
class TestLocalCacheLogic:
    """Tests for the local language server/razor extension caching logic."""

    def test_cache_metadata_save_and_load(self):
        """Test saving and loading cache metadata."""
        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            meta_file = temp_path / "test.meta.json"
            source_path = temp_path / "source"
            source_path.mkdir()

            # Create a dummy DLL to get a modification time
            dll_file = source_path / "test.dll"
            dll_file.touch()
            source_mtime = dll_file.stat().st_mtime

            # Save metadata
            CSharpLanguageServer.DependencyProvider._save_local_cache_metadata(meta_file, source_path, source_mtime)

            # Load metadata
            metadata = CSharpLanguageServer.DependencyProvider._load_local_cache_metadata(meta_file)
            assert metadata is not None
            assert metadata["source_path"] == str(source_path)
            assert metadata["source_mtime"] == source_mtime
            assert "copied_at" in metadata

    def test_cache_metadata_load_nonexistent(self):
        """Test loading metadata from a nonexistent file returns None."""
        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            meta_file = temp_path / "nonexistent.meta.json"

            metadata = CSharpLanguageServer.DependencyProvider._load_local_cache_metadata(meta_file)
            assert metadata is None

    def test_cache_metadata_load_corrupted(self):
        """Test loading corrupted metadata returns None."""
        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            meta_file = temp_path / "corrupted.meta.json"

            # Write invalid JSON
            with open(meta_file, "w") as f:
                f.write("not valid json {{{")

            metadata = CSharpLanguageServer.DependencyProvider._load_local_cache_metadata(meta_file)
            assert metadata is None

    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_dotnet_runtime")
    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_language_server")
    @patch("shutil.which")
    def test_is_local_cache_up_to_date_fresh_cache(self, mock_which, mock_ensure_ls, mock_ensure_dotnet):
        """Test that cache is considered up-to-date when DLL hasn't changed."""
        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer
        from solidlsp.settings import SolidLSPSettings

        mock_which.return_value = "/usr/bin/dotnet"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create source directory with DLL
            source_path = temp_path / "source"
            source_path.mkdir()
            main_dll = source_path / "Microsoft.CodeAnalysis.LanguageServer.dll"
            main_dll.touch()
            source_mtime = main_dll.stat().st_mtime

            # Create cache directory
            cache_dir = temp_path / "cache"
            cache_dir.mkdir()
            (cache_dir / "Microsoft.CodeAnalysis.LanguageServer.dll").touch()

            # Create metadata file
            meta_file = temp_path / "cache.meta.json"
            CSharpLanguageServer.DependencyProvider._save_local_cache_metadata(meta_file, source_path, source_mtime)

            # Create DependencyProvider instance
            mock_settings = Mock(spec=SolidLSPSettings)
            mock_settings.ls_resources_dir = str(temp_path)
            mock_settings.project_data_relative_path = "project_data"

            custom_settings = {"local_language_server_path": str(source_path)}

            provider = CSharpLanguageServer.DependencyProvider(
                custom_settings=cast(SolidLSPSettings.CustomLSSettings, custom_settings),
                ls_resources_dir=str(temp_path / "resources"),
                solidlsp_settings=mock_settings,
                repository_root_path=str(temp_path),
            )

            # Test that cache is up-to-date
            result = provider._is_local_cache_up_to_date(source_path, cache_dir, meta_file, "Microsoft.CodeAnalysis.LanguageServer.dll")
            assert result is True

    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_dotnet_runtime")
    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_language_server")
    @patch("shutil.which")
    def test_is_local_cache_up_to_date_stale_cache(self, mock_which, mock_ensure_ls, mock_ensure_dotnet):
        """Test that cache is considered stale when DLL modification time changes."""
        import time

        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer
        from solidlsp.settings import SolidLSPSettings

        mock_which.return_value = "/usr/bin/dotnet"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create source directory with DLL
            source_path = temp_path / "source"
            source_path.mkdir()
            main_dll = source_path / "Microsoft.CodeAnalysis.LanguageServer.dll"
            main_dll.touch()
            old_mtime = main_dll.stat().st_mtime

            # Create cache directory
            cache_dir = temp_path / "cache"
            cache_dir.mkdir()
            (cache_dir / "Microsoft.CodeAnalysis.LanguageServer.dll").touch()

            # Create metadata file with old mtime
            meta_file = temp_path / "cache.meta.json"
            CSharpLanguageServer.DependencyProvider._save_local_cache_metadata(meta_file, source_path, old_mtime)

            # Simulate DLL rebuild by modifying it
            time.sleep(0.01)  # Ensure time difference
            main_dll.write_text("updated content")

            # Create DependencyProvider instance
            mock_settings = Mock(spec=SolidLSPSettings)
            mock_settings.ls_resources_dir = str(temp_path)
            mock_settings.project_data_relative_path = "project_data"

            custom_settings = {"local_language_server_path": str(source_path)}

            provider = CSharpLanguageServer.DependencyProvider(
                custom_settings=cast(SolidLSPSettings.CustomLSSettings, custom_settings),
                ls_resources_dir=str(temp_path / "resources"),
                solidlsp_settings=mock_settings,
                repository_root_path=str(temp_path),
            )

            # Test that cache is stale
            result = provider._is_local_cache_up_to_date(source_path, cache_dir, meta_file, "Microsoft.CodeAnalysis.LanguageServer.dll")
            assert result is False

    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_dotnet_runtime")
    @patch("solidlsp.language_servers.csharp_language_server.CSharpLanguageServer.DependencyProvider._ensure_language_server")
    @patch("shutil.which")
    def test_copy_local_to_cache(self, mock_which, mock_ensure_ls, mock_ensure_dotnet):
        """Test copying local directory to cache."""
        from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer
        from solidlsp.settings import SolidLSPSettings

        mock_which.return_value = "/usr/bin/dotnet"

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create source directory with files
            source_path = temp_path / "source"
            source_path.mkdir()
            main_dll = source_path / "Microsoft.CodeAnalysis.LanguageServer.dll"
            main_dll.write_text("dll content")
            (source_path / "other.dll").write_text("other content")
            (source_path / "subdir").mkdir()
            (source_path / "subdir" / "nested.dll").write_text("nested content")

            cache_dir = temp_path / "cache"
            meta_file = temp_path / "cache.meta.json"

            # Create DependencyProvider instance
            mock_settings = Mock(spec=SolidLSPSettings)
            mock_settings.ls_resources_dir = str(temp_path)
            mock_settings.project_data_relative_path = "project_data"

            custom_settings = {"local_language_server_path": str(source_path)}

            provider = CSharpLanguageServer.DependencyProvider(
                custom_settings=cast(SolidLSPSettings.CustomLSSettings, custom_settings),
                ls_resources_dir=str(temp_path / "resources"),
                solidlsp_settings=mock_settings,
                repository_root_path=str(temp_path),
            )

            # Copy to cache
            result = provider._copy_local_to_cache(source_path, cache_dir, meta_file, "Microsoft.CodeAnalysis.LanguageServer.dll")

            assert result is not None
            assert result == cache_dir
            assert cache_dir.exists()
            assert (cache_dir / "Microsoft.CodeAnalysis.LanguageServer.dll").exists()
            assert (cache_dir / "other.dll").exists()
            assert (cache_dir / "subdir" / "nested.dll").exists()
            assert meta_file.exists()

            # Verify metadata content
            metadata = CSharpLanguageServer.DependencyProvider._load_local_cache_metadata(meta_file)
            assert metadata is not None
            assert metadata["source_path"] == str(source_path)
