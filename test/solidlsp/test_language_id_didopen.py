"""Tests for languageId in textDocument/didOpen messages.

This module tests that the correct languageId is sent when opening files
via the LSP protocol.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from solidlsp.language_servers.csharp_language_server import CSharpLanguageServer
from solidlsp.language_servers.vue_language_server import VueLanguageServer, VueTypeScriptServer
from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language
from solidlsp.lsp_protocol_handler.lsp_constants import LSPConstants


class DummyLanguageServer(SolidLanguageServer):
    """Dummy language server for testing without starting a real server."""

    def _start_server(self) -> None:
        raise AssertionError("Not used in this test")


class TestGetLanguageIdForFile:
    """Test _get_language_id_for_file method implementations."""

    def test_solid_language_server_default_language_id(self, tmp_path) -> None:
        """Test that SolidLanguageServer returns the default language_id."""
        language_server = object.__new__(DummyLanguageServer)
        language_server.language_id = "python"

        assert language_server._get_language_id_for_file("test.py") == "python"
        assert language_server._get_language_id_for_file("foo/bar.py") == "python"
        assert language_server._get_language_id_for_file("anything.xyz") == "python"

    def test_csharp_language_server_cs_files(self) -> None:
        """Test that CSharpLanguageServer returns 'csharp' for .cs files."""
        # We need to test the method directly without instantiating the full server
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "test.cs").touch()
            # Create a mock instance
            mock_server = object.__new__(CSharpLanguageServer)
            mock_server.language_id = "csharp"

            assert mock_server._get_language_id_for_file("test.cs") == "csharp"
            assert mock_server._get_language_id_for_file("src/Models/User.cs") == "csharp"
            assert mock_server._get_language_id_for_file("TEST.CS") == "csharp"

    def test_csharp_language_server_razor_files(self) -> None:
        """Test that CSharpLanguageServer returns 'aspnetcorerazor' for .razor files."""
        mock_server = object.__new__(CSharpLanguageServer)
        mock_server.language_id = "csharp"

        assert mock_server._get_language_id_for_file("Component.razor") == "aspnetcorerazor"
        assert mock_server._get_language_id_for_file("Pages/Index.razor") == "aspnetcorerazor"
        assert mock_server._get_language_id_for_file("COMPONENT.RAZOR") == "aspnetcorerazor"

    def test_csharp_language_server_cshtml_files(self) -> None:
        """Test that CSharpLanguageServer returns 'aspnetcorerazor' for .cshtml files."""
        mock_server = object.__new__(CSharpLanguageServer)
        mock_server.language_id = "csharp"

        assert mock_server._get_language_id_for_file("Index.cshtml") == "aspnetcorerazor"
        assert mock_server._get_language_id_for_file("Views/Home/Index.cshtml") == "aspnetcorerazor"
        assert mock_server._get_language_id_for_file("PAGE.CSHTML") == "aspnetcorerazor"

    def test_vue_typescript_server_vue_files(self) -> None:
        """Test that VueTypeScriptServer returns 'vue' for .vue files."""
        mock_server = object.__new__(VueTypeScriptServer)

        assert mock_server._get_language_id_for_file("App.vue") == "vue"
        assert mock_server._get_language_id_for_file("components/Button.vue") == "vue"
        assert mock_server._get_language_id_for_file("COMPONENT.VUE") == "vue"

    def test_vue_typescript_server_typescript_files(self) -> None:
        """Test that VueTypeScriptServer returns 'typescript' for .ts files."""
        mock_server = object.__new__(VueTypeScriptServer)

        assert mock_server._get_language_id_for_file("main.ts") == "typescript"
        assert mock_server._get_language_id_for_file("utils/helper.tsx") == "typescript"
        assert mock_server._get_language_id_for_file("types.mts") == "typescript"
        assert mock_server._get_language_id_for_file("config.cts") == "typescript"

    def test_vue_typescript_server_javascript_files(self) -> None:
        """Test that VueTypeScriptServer returns 'javascript' for .js files."""
        mock_server = object.__new__(VueTypeScriptServer)

        assert mock_server._get_language_id_for_file("main.js") == "javascript"
        assert mock_server._get_language_id_for_file("utils/helper.jsx") == "javascript"
        assert mock_server._get_language_id_for_file("config.mjs") == "javascript"
        assert mock_server._get_language_id_for_file("module.cjs") == "javascript"

    def test_vue_typescript_server_other_files(self) -> None:
        """Test that VueTypeScriptServer returns 'typescript' for other files."""
        mock_server = object.__new__(VueTypeScriptServer)

        # Default fallback is typescript
        assert mock_server._get_language_id_for_file("config.json") == "typescript"
        assert mock_server._get_language_id_for_file("unknown.xyz") == "typescript"

    def test_vue_language_server_vue_files(self) -> None:
        """Test that VueLanguageServer returns 'vue' for .vue files."""
        mock_server = object.__new__(VueLanguageServer)

        assert mock_server._get_language_id_for_file("App.vue") == "vue"
        assert mock_server._get_language_id_for_file("components/Button.vue") == "vue"

    def test_vue_language_server_typescript_files(self) -> None:
        """Test that VueLanguageServer returns 'typescript' for .ts files."""
        mock_server = object.__new__(VueLanguageServer)

        assert mock_server._get_language_id_for_file("main.ts") == "typescript"
        assert mock_server._get_language_id_for_file("utils/helper.tsx") == "typescript"

    def test_vue_language_server_javascript_files(self) -> None:
        """Test that VueLanguageServer returns 'javascript' for .js files."""
        mock_server = object.__new__(VueLanguageServer)

        assert mock_server._get_language_id_for_file("main.js") == "javascript"
        assert mock_server._get_language_id_for_file("utils/helper.jsx") == "javascript"

    def test_vue_language_server_other_files(self) -> None:
        """Test that VueLanguageServer returns 'vue' for other files (different from VueTypeScriptServer)."""
        mock_server = object.__new__(VueLanguageServer)

        # Default fallback is vue (different from VueTypeScriptServer)
        assert mock_server._get_language_id_for_file("config.json") == "vue"
        assert mock_server._get_language_id_for_file("unknown.xyz") == "vue"


class TestDidOpenLanguageId:
    """Test that textDocument/didOpen sends the correct languageId."""

    def test_did_open_sends_correct_language_id(self, tmp_path) -> None:
        """Test that open_file sends the correct languageId in didOpen notification."""
        (tmp_path / "test.ts").write_text("const x = 1;\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(DummyLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "typescript"
        language_server.server = server

        with language_server.open_file("test.ts"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "typescript"

    def test_did_open_csharp_cs_file(self, tmp_path) -> None:
        """Test that CSharpLanguageServer sends 'csharp' for .cs files."""
        (tmp_path / "Program.cs").write_text("class Program { }\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        # Create a mock CSharpLanguageServer instance
        language_server = object.__new__(CSharpLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "csharp"
        language_server.server = server

        with language_server.open_file("Program.cs"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "csharp"

    def test_did_open_csharp_razor_file(self, tmp_path) -> None:
        """Test that CSharpLanguageServer sends 'aspnetcorerazor' for .razor files."""
        (tmp_path / "Component.razor").write_text("<h1>Hello</h1>\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(CSharpLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "csharp"
        language_server.server = server

        with language_server.open_file("Component.razor"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "aspnetcorerazor"

    def test_did_open_csharp_cshtml_file(self, tmp_path) -> None:
        """Test that CSharpLanguageServer sends 'aspnetcorerazor' for .cshtml files."""
        (tmp_path / "Index.cshtml").write_text("@page\n<h1>Hello</h1>\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(CSharpLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "csharp"
        language_server.server = server

        with language_server.open_file("Index.cshtml"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "aspnetcorerazor"

    def test_did_open_vue_typescript_server_vue_file(self, tmp_path) -> None:
        """Test that VueTypeScriptServer sends 'vue' for .vue files."""
        (tmp_path / "App.vue").write_text("<template><div>Hello</div></template>\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(VueTypeScriptServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "vue"
        language_server.server = server

        with language_server.open_file("App.vue"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "vue"

    def test_did_open_vue_typescript_server_ts_file(self, tmp_path) -> None:
        """Test that VueTypeScriptServer sends 'typescript' for .ts files."""
        (tmp_path / "main.ts").write_text("const x = 1;\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(VueTypeScriptServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "vue"
        language_server.server = server

        with language_server.open_file("main.ts"):
            pass

        assert len(captured_params) == 1
        text_document = captured_params[0][LSPConstants.TEXT_DOCUMENT]
        assert text_document[LSPConstants.LANGUAGE_ID] == "typescript"

    def test_did_open_file_buffer_stores_language_id(self, tmp_path) -> None:
        """Test that the file buffer stores the correct language_id."""
        (tmp_path / "Component.razor").write_text("<h1>Hello</h1>\n", encoding="utf-8")

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda *_args, **_kwargs: None
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(CSharpLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "csharp"
        language_server.server = server

        with language_server.open_file("Component.razor") as file_buffer:
            # The file buffer should store the correct language_id
            assert file_buffer.language_id == "aspnetcorerazor"

    def test_did_open_multiple_file_types(self, tmp_path) -> None:
        """Test opening multiple file types returns correct languageIds."""
        (tmp_path / "Program.cs").write_text("class Program { }\n", encoding="utf-8")
        (tmp_path / "Component.razor").write_text("<h1>Hello</h1>\n", encoding="utf-8")
        (tmp_path / "Index.cshtml").write_text("@page\n", encoding="utf-8")

        captured_params: list[dict] = []

        notify = MagicMock()
        notify.did_open_text_document.side_effect = lambda params: captured_params.append(params)
        notify.did_close_text_document.side_effect = lambda *_args, **_kwargs: None

        server = MagicMock()
        server.notify = notify

        language_server = object.__new__(CSharpLanguageServer)
        language_server.repository_root_path = str(tmp_path)
        language_server.server_started = True
        language_server.open_file_buffers = {}
        language_server._encoding = "utf-8"
        language_server.language_id = "csharp"
        language_server.server = server

        # Open files sequentially
        files = ["Program.cs", "Component.razor", "Index.cshtml"]
        expected_language_ids = ["csharp", "aspnetcorerazor", "aspnetcorerazor"]

        for file_name in files:
            with language_server.open_file(file_name):
                pass

        assert len(captured_params) == 3
        for i, expected_id in enumerate(expected_language_ids):
            text_document = captured_params[i][LSPConstants.TEXT_DOCUMENT]
            assert (
                text_document[LSPConstants.LANGUAGE_ID] == expected_id
            ), f"File {files[i]} should have languageId '{expected_id}', but got '{text_document[LSPConstants.LANGUAGE_ID]}'"


@pytest.mark.csharp
class TestCSharpLanguageIdIntegration:
    """Integration tests for languageId with real C# language server.

    Note: These tests use enable_razor=False because the bundled Razor extension
    is compiled for .NET 10 while the default downloaded language server is compiled for .NET 9.
    The languageId logic is tested in unit tests above.

    If you have a local .NET 10 language server (e.g., built from roslyn source), you can use:
    ls_specific_settings={Language.CSHARP: {"local_language_server_path": "/path/to/net10.0"}}
    """

    @pytest.fixture(scope="class")
    def csharp_ls_no_razor(self):
        """Create C# language server with Razor disabled."""
        from test.conftest import start_ls_context

        with start_ls_context(Language.CSHARP, ls_specific_settings={Language.CSHARP: {"enable_razor": False}}) as ls:
            yield ls

    def test_cs_file_opens_successfully(self, csharp_ls_no_razor: SolidLanguageServer) -> None:
        """Test that .cs files can be opened with the real server (Razor disabled)."""
        file_path = "Program.cs"

        # Open the file and check the file buffer's language_id
        with csharp_ls_no_razor.open_file(file_path) as file_buffer:
            assert file_buffer.language_id == "csharp", f"Expected languageId 'csharp' for .cs file, got '{file_buffer.language_id}'"

    def test_cshtml_file_language_id_with_real_server(self, csharp_ls_no_razor: SolidLanguageServer) -> None:
        """Test that .cshtml files are opened with 'aspnetcorerazor' languageId using real server."""
        # The test repo should have Views/Index.cshtml
        file_path = os.path.join("Views", "Index.cshtml")

        # Open the file and check the file buffer's language_id
        with csharp_ls_no_razor.open_file(file_path) as file_buffer:
            assert (
                file_buffer.language_id == "aspnetcorerazor"
            ), f"Expected languageId 'aspnetcorerazor' for .cshtml file, got '{file_buffer.language_id}'"

    def test_razor_file_language_id_with_real_server(self, csharp_ls_no_razor: SolidLanguageServer) -> None:
        """Test that .razor files are opened with 'aspnetcorerazor' languageId using real server."""
        # The test repo should have Components/Counter.razor
        file_path = os.path.join("Components", "Counter.razor")

        # Open the file and check the file buffer's language_id
        with csharp_ls_no_razor.open_file(file_path) as file_buffer:
            assert (
                file_buffer.language_id == "aspnetcorerazor"
            ), f"Expected languageId 'aspnetcorerazor' for .razor file, got '{file_buffer.language_id}'"

    def test_multiple_file_types_language_id_with_real_server(self, csharp_ls_no_razor: SolidLanguageServer) -> None:
        """Test opening multiple file types with real server returns correct languageIds."""
        test_cases = [
            ("Program.cs", "csharp"),
            (os.path.join("Views", "Index.cshtml"), "aspnetcorerazor"),
            (os.path.join("Components", "Counter.razor"), "aspnetcorerazor"),
            (os.path.join("Models", "Person.cs"), "csharp"),
        ]

        for file_path, expected_language_id in test_cases:
            with csharp_ls_no_razor.open_file(file_path) as file_buffer:
                assert (
                    file_buffer.language_id == expected_language_id
                ), f"File '{file_path}' should have languageId '{expected_language_id}', but got '{file_buffer.language_id}'"


@pytest.mark.csharp
class TestCSharpLanguageIdWithLocalNet10Server:
    """Integration tests using local .NET 10 language server with Razor enabled.

    These tests require a locally built Roslyn language server compiled for .NET 10.
    Set the LOCAL_ROSLYN_LS_PATH environment variable to the language server directory.

    Example:
        export LOCAL_ROSLYN_LS_PATH="/path/to/roslyn/artifacts/bin/Microsoft.CodeAnalysis.LanguageServer/Debug/net10.0"
        uv run pytest test/solidlsp/test_language_id_didopen.py::TestCSharpLanguageIdWithLocalNet10Server -v

    """

    @pytest.fixture(scope="class")
    def csharp_ls_net10(self):
        """Create C# language server using local .NET 10 build with Razor enabled."""
        from test.conftest import start_ls_context

        local_path = os.environ.get("LOCAL_ROSLYN_LS_PATH")

        if not local_path:
            pytest.skip("LOCAL_ROSLYN_LS_PATH environment variable not set")

        if not Path(local_path).exists():
            pytest.skip(f"Local .NET 10 language server not found at: {local_path}")

        with start_ls_context(
            Language.CSHARP,
            ls_specific_settings={
                Language.CSHARP: {
                    "local_language_server_path": local_path,
                    "enable_razor": True,
                }
            },
        ) as ls:
            yield ls

    def test_local_net10_server_starts(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test that local .NET 10 language server starts successfully."""
        assert csharp_ls_net10.server_started, "Language server should be started"

    def test_cs_file_with_net10_server(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test .cs file with local .NET 10 server."""
        with csharp_ls_net10.open_file("Program.cs") as file_buffer:
            assert file_buffer.language_id == "csharp"

    def test_cshtml_file_with_net10_server(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test .cshtml file with local .NET 10 server (Razor enabled)."""
        file_path = os.path.join("Views", "Index.cshtml")
        with csharp_ls_net10.open_file(file_path) as file_buffer:
            assert file_buffer.language_id == "aspnetcorerazor"

    def test_razor_file_with_net10_server(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test .razor file with local .NET 10 server (Razor enabled)."""
        file_path = os.path.join("Components", "Counter.razor")
        with csharp_ls_net10.open_file(file_path) as file_buffer:
            assert file_buffer.language_id == "aspnetcorerazor"

    def test_document_symbols_cs_file(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test getting document symbols from .cs file."""
        symbols = csharp_ls_net10.request_document_symbols("Program.cs")
        all_symbols = symbols.get_all_symbols_and_roots()
        assert len(all_symbols) > 0, "Should find symbols in Program.cs"

    def test_all_file_types_with_net10_server(self, csharp_ls_net10: SolidLanguageServer) -> None:
        """Test all file types with local .NET 10 server."""
        test_cases = [
            ("Program.cs", "csharp"),
            (os.path.join("Views", "Index.cshtml"), "aspnetcorerazor"),
            (os.path.join("Components", "Counter.razor"), "aspnetcorerazor"),
            (os.path.join("Models", "Person.cs"), "csharp"),
        ]

        for file_path, expected_language_id in test_cases:
            with csharp_ls_net10.open_file(file_path) as file_buffer:
                assert (
                    file_buffer.language_id == expected_language_id
                ), f"File '{file_path}' should have languageId '{expected_language_id}', got '{file_buffer.language_id}'"
