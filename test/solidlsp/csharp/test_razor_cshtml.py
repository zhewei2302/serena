"""
Test script for debugging Razor (.razor) and CSHTML (.cshtml) file parsing.

Run with:
    pytest test/solidlsp/csharp/test_razor_cshtml.py -v -s --log-cli-level=DEBUG

Or run specific test:
    pytest test/solidlsp/csharp/test_razor_cshtml.py::TestRazorCshtmlParsing::test_cshtml_document_symbols -v -s --log-cli-level=DEBUG
"""

import logging
import os
import time

import pytest

from solidlsp.ls import SolidLanguageServer
from solidlsp.ls_config import Language

log = logging.getLogger(__name__)


@pytest.mark.csharp
class TestRazorCshtmlParsing:
    """Test Razor and CSHTML file parsing."""

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_razor_document_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test getting document symbols from a .razor file."""
        file_path = os.path.join("Components", "Counter.razor")
        log.info(f"Testing .razor file: {file_path}")

        # Wait for Razor extension to initialize
        time.sleep(2)

        symbols = language_server.request_document_symbols(file_path)
        all_symbols = symbols.get_all_symbols_and_roots()

        log.info(f"Razor symbols received: {len(all_symbols)} items")
        log.info(f"Razor symbols content: {all_symbols}")

        # Check that we have symbols
        assert len(all_symbols) > 0, "No symbols returned for .razor file"

        # Look for expected symbols from Counter.razor
        symbol_names = []
        for s in all_symbols:
            if isinstance(s, list):
                symbol_names.extend([item.get("name") for item in s if isinstance(item, dict)])
            elif isinstance(s, dict):
                symbol_names.append(s.get("name"))

        log.info(f"Symbol names found: {symbol_names}")
        assert (
            "currentCount" in symbol_names or "IncrementCount" in symbol_names
        ), f"Expected Counter.razor symbols not found. Got: {symbol_names}"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_cshtml_document_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test getting document symbols from a .cshtml file."""
        file_path = os.path.join("Views", "WithCode.cshtml")
        log.info(f"Testing .cshtml file: {file_path}")

        # Wait for Razor extension to initialize
        time.sleep(2)

        symbols = language_server.request_document_symbols(file_path)
        all_symbols = symbols.get_all_symbols_and_roots()

        log.info(f"CSHTML symbols received: {len(all_symbols)} items")
        log.info(f"CSHTML symbols content: {all_symbols}")

        # Check that we have symbols
        assert len(all_symbols) > 0, "No symbols returned for .cshtml file"

        # Look for expected symbols from WithCode.cshtml
        symbol_names = []
        for s in all_symbols:
            if isinstance(s, list):
                symbol_names.extend([item.get("name") for item in s if isinstance(item, dict)])
            elif isinstance(s, dict):
                symbol_names.append(s.get("name"))

        log.info(f"Symbol names found: {symbol_names}")
        # WithCode.cshtml has: Message, Counter, IncrementCounter, GetGreeting
        assert any(
            name in symbol_names for name in ["Message", "Counter", "IncrementCounter", "GetGreeting"]
        ), f"Expected WithCode.cshtml symbols not found. Got: {symbol_names}"

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_cshtml_simple_document_symbols(self, language_server: SolidLanguageServer) -> None:
        """Test getting document symbols from a simple .cshtml file (no @functions)."""
        file_path = os.path.join("Views", "Index.cshtml")
        log.info(f"Testing simple .cshtml file: {file_path}")

        # Wait for Razor extension to initialize
        time.sleep(2)

        symbols = language_server.request_document_symbols(file_path)
        all_symbols = symbols.get_all_symbols_and_roots()

        log.info(f"Simple CSHTML symbols received: {len(all_symbols)} items")
        log.info(f"Simple CSHTML symbols content: {all_symbols}")

        # Index.cshtml has minimal code, so it might return empty or minimal symbols
        # This is expected behavior for a simple view without @functions

    @pytest.mark.parametrize("language_server", [Language.CSHARP], indirect=True)
    def test_compare_razor_vs_cshtml(self, language_server: SolidLanguageServer) -> None:
        """Compare symbol retrieval between .razor and .cshtml files."""
        # Wait for Razor extension to initialize
        time.sleep(3)

        # Test .razor file
        razor_path = os.path.join("Components", "Counter.razor")
        log.info(f"=== Testing .razor: {razor_path} ===")
        razor_symbols = language_server.request_document_symbols(razor_path)
        razor_all = razor_symbols.get_all_symbols_and_roots()
        log.info(f"Razor result: {len(razor_all)} symbols")
        for i, s in enumerate(razor_all[:5]):  # Show first 5
            log.info(f"  [{i}]: {s}")

        # Test .cshtml file with code
        cshtml_path = os.path.join("Views", "WithCode.cshtml")
        log.info(f"=== Testing .cshtml: {cshtml_path} ===")
        cshtml_symbols = language_server.request_document_symbols(cshtml_path)
        cshtml_all = cshtml_symbols.get_all_symbols_and_roots()
        log.info(f"CSHTML result: {len(cshtml_all)} symbols")
        for i, s in enumerate(cshtml_all[:5]):  # Show first 5
            log.info(f"  [{i}]: {s}")

        # Report comparison
        log.info("=== Comparison ===")
        log.info(f"Razor symbols: {len(razor_all)}")
        log.info(f"CSHTML symbols: {len(cshtml_all)}")

        if len(cshtml_all) == 0 and len(razor_all) > 0:
            log.error("BUG DETECTED: .cshtml returns empty but .razor works!")
