"""
Tool categorization system for deferred loading support.
"""

from enum import Enum

from serena.util.class_decorators import singleton


class ToolCategory(Enum):
    """Categories for grouping tools by functionality."""

    FILE_OPERATIONS = "file_operations"
    """File system operations like reading, writing, and searching files."""

    SYMBOLIC_READ = "symbolic_read"
    """Tools for reading and analyzing code symbols."""

    SYMBOLIC_EDIT = "symbolic_edit"
    """Tools for editing code at the symbol level."""

    MEMORY = "memory"
    """Tools for persisting and retrieving project knowledge."""

    CONFIG = "config"
    """Tools for configuration and project management."""

    WORKFLOW = "workflow"
    """Tools for onboarding and meta-operations."""

    SHELL = "shell"
    """Tools for executing shell commands."""

    JETBRAINS = "jetbrains"
    """JetBrains IDE-specific tools."""


# Tool name to category mapping
_TOOL_CATEGORY_MAP: dict[str, ToolCategory] = {
    # File operations
    "list_dir": ToolCategory.FILE_OPERATIONS,
    "find_file": ToolCategory.FILE_OPERATIONS,
    "search_for_pattern": ToolCategory.FILE_OPERATIONS,
    "read_file": ToolCategory.FILE_OPERATIONS,
    "write_file": ToolCategory.FILE_OPERATIONS,
    "replace_content": ToolCategory.FILE_OPERATIONS,
    # Symbolic read
    "get_symbols_overview": ToolCategory.SYMBOLIC_READ,
    "find_symbol": ToolCategory.SYMBOLIC_READ,
    "find_referencing_symbols": ToolCategory.SYMBOLIC_READ,
    # Symbolic edit
    "replace_symbol_body": ToolCategory.SYMBOLIC_EDIT,
    "insert_after_symbol": ToolCategory.SYMBOLIC_EDIT,
    "insert_before_symbol": ToolCategory.SYMBOLIC_EDIT,
    "rename_symbol": ToolCategory.SYMBOLIC_EDIT,
    # Memory
    "read_memory": ToolCategory.MEMORY,
    "write_memory": ToolCategory.MEMORY,
    "list_memories": ToolCategory.MEMORY,
    "delete_memory": ToolCategory.MEMORY,
    "edit_memory": ToolCategory.MEMORY,
    # Config
    "activate_project": ToolCategory.CONFIG,
    "get_current_config": ToolCategory.CONFIG,
    "switch_modes": ToolCategory.CONFIG,
    "remove_project": ToolCategory.CONFIG,
    "open_dashboard": ToolCategory.CONFIG,
    "search_tools": ToolCategory.CONFIG,
    # Workflow
    "initial_instructions": ToolCategory.WORKFLOW,
    "check_onboarding_performed": ToolCategory.WORKFLOW,
    "onboarding": ToolCategory.WORKFLOW,
    "think_about_collected_information": ToolCategory.WORKFLOW,
    "think_about_task_adherence": ToolCategory.WORKFLOW,
    "think_about_whether_you_are_done": ToolCategory.WORKFLOW,
    # Shell
    "run_shell_command": ToolCategory.SHELL,
    # JetBrains
    "jetbrains_read_file": ToolCategory.JETBRAINS,
    "jetbrains_write_file": ToolCategory.JETBRAINS,
    "jetbrains_run_action": ToolCategory.JETBRAINS,
    "jetbrains_list_actions": ToolCategory.JETBRAINS,
    "jetbrains_get_run_configurations": ToolCategory.JETBRAINS,
    "jetbrains_run": ToolCategory.JETBRAINS,
    "jetbrains_open_file": ToolCategory.JETBRAINS,
    "jetbrains_get_open_files": ToolCategory.JETBRAINS,
}


@singleton
class ToolCategoryRegistry:
    """Registry managing tool-to-category mappings."""

    def __init__(self) -> None:
        self._tool_to_category = dict(_TOOL_CATEGORY_MAP)
        self._category_to_tools: dict[ToolCategory, list[str]] = {}
        for tool_name, category in self._tool_to_category.items():
            if category not in self._category_to_tools:
                self._category_to_tools[category] = []
            self._category_to_tools[category].append(tool_name)

    def get_category(self, tool_name: str) -> ToolCategory | None:
        """
        Get the category for a tool.

        :param tool_name: the name of the tool
        :return: the category of the tool, or None if the tool is not categorized
        """
        return self._tool_to_category.get(tool_name)

    def get_tools_by_category(self, category: ToolCategory) -> list[str]:
        """
        Get all tools in a category.

        :param category: the category to get tools for
        :return: list of tool names in the category
        """
        return self._category_to_tools.get(category, [])

    def get_all_categories(self) -> list[ToolCategory]:
        """
        Get all available categories.

        :return: list of all tool categories
        """
        return list(ToolCategory)

    def register_tool(self, tool_name: str, category: ToolCategory) -> None:
        """
        Register a tool with a category.

        :param tool_name: the name of the tool
        :param category: the category to assign
        """
        self._tool_to_category[tool_name] = category
        if category not in self._category_to_tools:
            self._category_to_tools[category] = []
        if tool_name not in self._category_to_tools[category]:
            self._category_to_tools[category].append(tool_name)
