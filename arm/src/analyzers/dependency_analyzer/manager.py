import logging
from typing import Dict, Any, List, Tuple, Optional

from ..base_analyzer import BaseAnalyzer
from .python_checker import PythonDependencyChecker
from .js_checker import JSDependencyChecker
from .base_checker import BaseDependencyChecker  # For type hinting

logger = logging.getLogger(__name__)


class DependencyManager(BaseAnalyzer):
    """
    Manages the analysis of different types of dependency files (requirements.txt, package.json).
    Acts as the main entry point for dependency analysis within the analyzer framework.
    """

    def __init__(self):
        """Initializes the specific dependency checkers."""
        self._checkers: Dict[str, BaseDependencyChecker] = {
            "python": PythonDependencyChecker(),
            "javascript": JSDependencyChecker(),
            # Add checkers for other languages here as needed
        }
        logger.info(
            f"DependencyManager initialized with checkers: {list(self._checkers.keys())}"
        )

    @property
    def analysis_key(self) -> str:
        return "dependencies"

    @property
    def relevant_file_patterns(self) -> List[str]:
        # Patterns for supported dependency files
        return [
            r"requirements\.txt$",
            r"package\.json$",
            # Add patterns for other files like pom.xml, go.mod etc. here
        ]

    def _get_checker_and_type(
        self, file_path: str
    ) -> Tuple[Optional[BaseDependencyChecker], Optional[str]]:
        """Determines the checker and type based on the file path."""
        file_path_lower = file_path.lower()
        if file_path_lower.endswith("requirements.txt"):
            return self._checkers.get("python"), "python"
        elif file_path_lower.endswith("package.json"):
            return self._checkers.get("javascript"), "javascript"
        # Add elif for other file types here
        else:
            logger.warning(
                f"No specific dependency checker found for file: {file_path}"
            )
            return None, None

    def analyze(self, file_content: str, file_path: str) -> Dict[str, Any]:
        """
        Parses dependencies from a given file using the appropriate checker.

        Args:
            file_content: The content of the dependency file.
            file_path: The path to the dependency file.

        Returns:
            A dictionary containing the list of parsed dependencies and the file type.
            Example: {'parsed_deps': [{'name': 'requests', ...}], 'file_type': 'python', 'file': file_path}
                     Returns empty list if parsing fails or no checker exists.
        """
        checker, file_type = self._get_checker_and_type(file_path)

        if checker and file_type:
            try:
                parsed_deps = checker.parse_dependencies(file_content, file_path)
                logger.debug(
                    f"Parsed {len(parsed_deps)} dependencies from {file_path} using {file_type} checker."
                )
                return {
                    "parsed_deps": parsed_deps,
                    "file_type": file_type,
                    "file": file_path,
                }
            except Exception as e:
                logger.error(
                    f"Error during dependency parsing for {file_path} using {file_type} checker: {e}",
                    exc_info=True,
                )
                # Return structure indicating error or empty list?
                return {
                    "parsed_deps": [],
                    "file_type": file_type,
                    "file": file_path,
                    "error": str(e),
                }
        else:
            # No checker found for this file type
            return {"parsed_deps": [], "file_type": None, "file": file_path}

    def aggregate_results(
        self, analysis_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates dependency compatibility results from multiple file analyses.

        Args:
            analysis_outputs: A list of dictionaries, output from the `analyze` method.
                Example: [{'parsed_deps': [...], 'file_type': 'python', 'file': 'reqs.txt'}, ...]

        Returns:
            A dictionary containing:
            - 'results': List of compatibility checks for each unique dependency.
            - 'recommendations': List of suggested actions.
            - 'reasoning': List of explanations for compatibility statuses.
        """
        all_compatibility_results = []
        recommendations = []
        reasoning = []
        # Track incompatible direct Python dependencies for better recommendations
        # TODO: Extend this concept for other languages if needed
        direct_python_incompatible = set()

        logger.info(
            f"Aggregating dependency analysis results from {len(analysis_outputs)} analysis outputs."
        )

        for output in analysis_outputs:
            parsed_deps = output.get("parsed_deps", [])
            file_type = output.get("file_type")
            file_path = output.get("file", "unknown_file")  # Get file path from output

            if not file_type or not parsed_deps:
                if output.get("error"):
                    logger.error(
                        f"Skipping aggregation for {file_path} due to parsing error: {output['error']}"
                    )
                elif not file_type:
                    logger.warning(
                        f"Skipping aggregation for {file_path} as file type is not supported."
                    )
                continue  # Skip if no checker or no dependencies parsed

            checker = self._checkers.get(file_type)
            if not checker:
                # This shouldn't happen if analyze worked, but check defensively
                logger.error(
                    f"Internal error: No checker found for file type '{file_type}' during aggregation."
                )
                continue

            logger.debug(
                f"Checking compatibility for {len(parsed_deps)} dependencies from {file_path} ({file_type})."
            )
            for dep_info in parsed_deps:
                try:
                    # Ensure 'file' key is present in dep_info if not already added by parser
                    if "file" not in dep_info:
                        dep_info["file"] = file_path

                    # Check compatibility using the appropriate checker
                    compatibility_result = checker.check_compatibility(dep_info)
                    all_compatibility_results.append(compatibility_result)

                    # --- Generate Recommendations and Reasoning (adapted from old logic) ---
                    comp_status = compatibility_result.get("compatible")
                    reason = compatibility_result.get("reason", "No reason provided.")
                    is_direct = compatibility_result.get(
                        "direct", True
                    )  # Assume direct unless specified otherwise
                    is_dev = compatibility_result.get("dev_dependency", False)  # For JS

                    pkg_name = compatibility_result.get("name", "unknown_package")
                    # Format package info string based on type
                    if file_type == "python":
                        version_str = compatibility_result.get("version_spec") or ""
                        package_info = f"`{pkg_name}{version_str}`"
                        lang_prefix = "Python"
                    elif file_type == "javascript":
                        version_str = (
                            compatibility_result.get("version_spec")
                            or compatibility_result.get("version")
                            or ""
                        )
                        package_info = f"`{pkg_name}@{version_str}`"
                        lang_prefix = "JavaScript"
                    else:
                        package_info = f"`{pkg_name}`"  # Generic fallback
                        lang_prefix = "Dependency"

                    # Add file path context to recommendations and reasoning
                    file_context = f"in `{file_path}`"

                    if comp_status is False:
                        reason_msg = f"{lang_prefix} package {package_info} is not compatible with ARM64 {file_context}. Reason: {reason}"
                        rec_msg = f"Replace {package_info} with an ARM64 compatible alternative {file_context}."
                        # Specific handling for Python direct/transitive (can be expanded)
                        if file_type == "python":
                            if is_direct:
                                direct_python_incompatible.add(pkg_name)
                            else:
                                parent = compatibility_result.get(
                                    "parent", "unknown parent"
                                )
                                reason_msg = f"Transitive {lang_prefix} dependency {package_info} (required by `{parent}`) is not compatible with ARM64 {file_context}. Reason: {reason}"
                                # Avoid redundant recommendations if parent is already marked incompatible
                                if parent not in direct_python_incompatible:
                                    rec_msg = f"Consider alternatives for `{parent}` to avoid its incompatible dependency {package_info} {file_context}."
                                else:
                                    rec_msg = None  # Don't add recommendation if parent is already being replaced

                        if rec_msg:
                            recommendations.append(rec_msg)
                        reasoning.append(reason_msg)

                    elif comp_status == "partial":
                        reason_msg = f"{lang_prefix} package {package_info} may have ARM64 compatibility issues {file_context}. Reason: {reason}"
                        rec_msg = f"Test {package_info} on ARM64 and check for compatibility issues {file_context}."
                        # Specific handling for JS dev dependencies
                        if file_type == "javascript" and is_dev:
                            rec_msg = f"Test dev dependency {package_info} on ARM64 {file_context} (may only affect build environment)."
                        # Specific handling for Python transitive
                        elif file_type == "python" and not is_direct:
                            parent = compatibility_result.get(
                                "parent", "unknown parent"
                            )
                            reason_msg = f"Transitive {lang_prefix} dependency {package_info} (required by `{parent}`) may have ARM64 compatibility issues {file_context}. Reason: {reason}"
                            rec_msg = f"Test `{parent}` with its dependency {package_info} on ARM64 for compatibility {file_context}."

                        recommendations.append(rec_msg)
                        reasoning.append(reason_msg)

                    # Optionally add reasoning for 'compatible' or 'unknown' status if desired
                    # elif comp_status is True:
                    #    reasoning.append(f"{lang_prefix} package {package_info} appears compatible {file_context}. Reason: {reason}")
                    # elif comp_status == "unknown":
                    #    reasoning.append(f"{lang_prefix} package {package_info} compatibility is unknown {file_context}. Reason: {reason}")

                except Exception as check_err:
                    logger.error(
                        f"Error checking compatibility for dependency {dep_info.get('name', 'unknown')} from {file_path}: {check_err}",
                        exc_info=True,
                    )
                    # Add an error entry to results?
                    all_compatibility_results.append(
                        {
                            **dep_info,
                            "compatible": "error",
                            "reason": f"Failed to check compatibility: {check_err}",
                        }
                    )

        # --- Deduplication ---
        # Deduplicate final results based on name and version (or original line?)
        unique_keys = set()
        deduplicated_results = []
        for result in all_compatibility_results:
            # Use name and version_spec/version as key
            key = (
                result.get("name"),
                result.get("version_spec", result.get("version")),
            )
            # Add file path to key to distinguish same package from different files? Maybe not needed for final report.
            if key not in unique_keys:
                unique_keys.add(key)
                deduplicated_results.append(result)
            # else: logger.debug(f"Skipping duplicate dependency result: {key}") # Optional logging

        # Deduplicate recommendations
        unique_recommendations = sorted(list(set(recommendations)))
        # Deduplicate reasoning? Might lose context if identical reasons came from different packages. Keep for now.

        logger.info(
            f"Finished aggregating dependency results. Found {len(deduplicated_results)} unique dependencies."
        )
        return {
            "results": deduplicated_results,  # Renamed from 'dependencies' for clarity
            "recommendations": unique_recommendations,
            "reasoning": reasoning,
        }
