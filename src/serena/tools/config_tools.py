from serena.tools import Tool, ToolMarkerDoesNotRequireActiveProject, ToolMarkerOptional, ToolRegistry
from serena.tools.tool_categories import ToolCategory, ToolCategoryRegistry


class OpenDashboardTool(Tool, ToolMarkerOptional, ToolMarkerDoesNotRequireActiveProject):
    """
    Opens the Serena web dashboard in the default web browser.
    The dashboard provides logs, session information, and tool usage statistics.
    """

    def apply(self) -> str:
        """
        Opens the Serena web dashboard in the default web browser.
        """
        if self.agent.open_dashboard():
            return f"Serena web dashboard has been opened in the user's default web browser: {self.agent.get_dashboard_url()}"
        else:
            return f"Serena web dashboard could not be opened automatically; tell the user to open it via {self.agent.get_dashboard_url()}"


class ActivateProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Activates a project based on the project name or path.
    """

    def apply(self, project: str) -> str:
        """
        Activates the project with the given name or path.

        :param project: the name of a registered project to activate or a path to a project directory
        """
        active_project = self.agent.activate_project_from_path_or_name(project)
        result = active_project.get_activation_message()
        result += "\nIMPORTANT: If you have not yet read the 'Serena Instructions Manual', do it now before continuing!"
        return result


class RemoveProjectTool(Tool, ToolMarkerDoesNotRequireActiveProject, ToolMarkerOptional):
    """
    Removes a project from the Serena configuration.
    """

    def apply(self, project_name: str) -> str:
        """
        Removes a project from the Serena configuration.

        :param project_name: Name of the project to remove
        """
        self.agent.serena_config.remove_project(project_name)
        return f"Successfully removed project '{project_name}' from configuration."


class SwitchModesTool(Tool, ToolMarkerOptional):
    """
    Activates modes by providing a list of their names
    """

    def apply(self, modes: list[str]) -> str:
        """
        Activates the desired modes, like ["editing", "interactive"] or ["planning", "one-shot"]

        :param modes: the names of the modes to activate
        """
        self.agent.set_modes(modes)

        # Inform the Agent about the activated modes and the currently active tools
        mode_instances = self.agent.get_active_modes()
        result_str = f"Active modes: {', '.join([mode.name for mode in mode_instances])}" + "\n"
        result_str += "\n".join([mode_instance.prompt for mode_instance in mode_instances]) + "\n"
        result_str += f"Currently active tools: {', '.join(self.agent.get_active_tool_names())}"
        return result_str


class GetCurrentConfigTool(Tool):
    """
    Prints the current configuration of the agent, including the active and available projects, tools, contexts, and modes.
    """

    def apply(self) -> str:
        """
        Print the current configuration of the agent, including the active and available projects, tools, contexts, and modes.
        """
        return self.agent.get_current_config_overview()


class SearchToolsTool(Tool, ToolMarkerDoesNotRequireActiveProject):
    """
    Searches for available tools by name, category, or keyword.
    Use this tool to discover tools when deferred loading is enabled.
    """

    def apply(
        self,
        query: str = "",
        category: str | None = None,
        include_descriptions: bool = True,
        max_results: int = 20,
    ) -> str:
        """
        Search for available tools by name pattern or category.

        :param query: search query to match against tool names (case-insensitive substring match)
        :param category: filter by category (file_operations, symbolic_read, symbolic_edit, memory, config, workflow, shell, jetbrains)
        :param include_descriptions: whether to include tool descriptions in the results
        :param max_results: maximum number of results to return
        :return: a formatted list of matching tools with their metadata
        """
        registry = ToolRegistry()
        category_registry = ToolCategoryRegistry()

        # Get all tool names
        all_tool_names = registry.get_tool_names()

        # Filter by category if specified
        if category:
            try:
                cat_enum = ToolCategory(category.lower())
                category_tools = set(category_registry.get_tools_by_category(cat_enum))
                all_tool_names = [name for name in all_tool_names if name in category_tools]
            except ValueError:
                valid_categories = ", ".join([c.value for c in ToolCategory])
                return f"Invalid category '{category}'. Valid categories are: {valid_categories}"

        # Filter by query if specified
        if query:
            query_lower = query.lower()
            all_tool_names = [name for name in all_tool_names if query_lower in name.lower()]

        # Limit results
        all_tool_names = sorted(all_tool_names)[:max_results]

        if not all_tool_names:
            result = "No tools found matching the search criteria."
            if category:
                result += f"\nCategory filter: {category}"
            if query:
                result += f"\nQuery: {query}"
            return result

        # Build result
        result_lines = [f"Found {len(all_tool_names)} tool(s):"]
        for tool_name in all_tool_names:
            tool_class = registry.get_tool_class_by_name(tool_name)
            tool_category = category_registry.get_category(tool_name)
            is_active = self.agent.tool_is_active(tool_name)
            can_edit = tool_class.can_edit()

            line = f"\n- **{tool_name}**"
            line += f" [{'active' if is_active else 'inactive'}]"
            if can_edit:
                line += " [can_edit]"
            if tool_category:
                line += f" [{tool_category.value}]"

            if include_descriptions:
                description = tool_class.get_tool_description()
                if description:
                    # Truncate long descriptions
                    if len(description) > 100:
                        description = description[:97] + "..."
                    line += f"\n  {description}"

            result_lines.append(line)

        # Add available categories at the end
        result_lines.append("\n\nAvailable categories: " + ", ".join([c.value for c in ToolCategory]))

        return "\n".join(result_lines)
