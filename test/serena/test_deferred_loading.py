"""
Tests for the deferred loading feature.
"""

from serena.config.context_mode import SerenaAgentContext
from serena.constants import DEFAULT_CORE_TOOLS
from serena.tools.tool_categories import ToolCategory, ToolCategoryRegistry


class TestToolCategories:
    """Tests for the tool category system."""

    def test_get_all_categories(self) -> None:
        """Test that all categories are returned."""
        registry = ToolCategoryRegistry()
        categories = registry.get_all_categories()
        assert len(categories) == len(ToolCategory)
        assert ToolCategory.FILE_OPERATIONS in categories
        assert ToolCategory.SYMBOLIC_READ in categories
        assert ToolCategory.SYMBOLIC_EDIT in categories
        assert ToolCategory.MEMORY in categories
        assert ToolCategory.CONFIG in categories
        assert ToolCategory.WORKFLOW in categories
        assert ToolCategory.SHELL in categories
        assert ToolCategory.JETBRAINS in categories

    def test_get_category_for_known_tool(self) -> None:
        """Test getting category for a known tool."""
        registry = ToolCategoryRegistry()
        assert registry.get_category("list_dir") == ToolCategory.FILE_OPERATIONS
        assert registry.get_category("find_symbol") == ToolCategory.SYMBOLIC_READ
        assert registry.get_category("replace_symbol_body") == ToolCategory.SYMBOLIC_EDIT
        assert registry.get_category("read_memory") == ToolCategory.MEMORY
        assert registry.get_category("activate_project") == ToolCategory.CONFIG
        assert registry.get_category("initial_instructions") == ToolCategory.WORKFLOW

    def test_get_category_for_unknown_tool(self) -> None:
        """Test that unknown tools return None."""
        registry = ToolCategoryRegistry()
        assert registry.get_category("nonexistent_tool") is None

    def test_get_tools_by_category(self) -> None:
        """Test getting tools by category."""
        registry = ToolCategoryRegistry()
        file_tools = registry.get_tools_by_category(ToolCategory.FILE_OPERATIONS)
        assert "list_dir" in file_tools
        assert "find_file" in file_tools

        memory_tools = registry.get_tools_by_category(ToolCategory.MEMORY)
        assert "read_memory" in memory_tools
        assert "write_memory" in memory_tools

    def test_register_tool(self) -> None:
        """Test registering a new tool with a category."""
        registry = ToolCategoryRegistry()
        registry.register_tool("new_test_tool", ToolCategory.CONFIG)
        assert registry.get_category("new_test_tool") == ToolCategory.CONFIG
        assert "new_test_tool" in registry.get_tools_by_category(ToolCategory.CONFIG)


class TestDeferredLoadingContext:
    """Tests for the deferred loading context configuration."""

    def test_default_context_has_deferred_loading_false(self) -> None:
        """Test that default contexts have deferred_loading=False."""
        context = SerenaAgentContext.load_default()
        assert context.deferred_loading is False

    def test_deferred_loading_context_loads(self) -> None:
        """Test that the deferred-loading context can be loaded."""
        context = SerenaAgentContext.from_name("deferred-loading")
        assert context.deferred_loading is True
        assert len(context.core_tools) > 0

    def test_deferred_loading_context_has_search_tools(self) -> None:
        """Test that the deferred-loading context includes search_tools."""
        context = SerenaAgentContext.from_name("deferred-loading")
        assert "search_tools" in context.core_tools

    def test_core_tools_conversion_from_yaml(self) -> None:
        """Test that core_tools list is converted to tuple from YAML."""
        context = SerenaAgentContext.from_name("deferred-loading")
        # YAML lists are converted to tuples
        assert isinstance(context.core_tools, tuple)


class TestDefaultCoreTools:
    """Tests for the default core tools constant."""

    def test_default_core_tools_is_tuple(self) -> None:
        """Test that DEFAULT_CORE_TOOLS is a tuple."""
        assert isinstance(DEFAULT_CORE_TOOLS, tuple)

    def test_default_core_tools_has_search_tools(self) -> None:
        """Test that search_tools is in DEFAULT_CORE_TOOLS."""
        assert "search_tools" in DEFAULT_CORE_TOOLS

    def test_default_core_tools_has_essential_tools(self) -> None:
        """Test that essential tools are in DEFAULT_CORE_TOOLS."""
        assert "initial_instructions" in DEFAULT_CORE_TOOLS
        assert "activate_project" in DEFAULT_CORE_TOOLS
        assert "get_current_config" in DEFAULT_CORE_TOOLS
        assert "list_dir" in DEFAULT_CORE_TOOLS
        assert "find_file" in DEFAULT_CORE_TOOLS
