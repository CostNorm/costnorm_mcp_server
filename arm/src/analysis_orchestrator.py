import re
import logging
from typing import Dict, Any, List, Optional, Tuple, Type

# Configuration
from config import ENABLED_ANALYZERS

# Services
from services.github_service import (
    GithubService,
    GithubApiException,
    RepositoryNotFoundException,
    BranchNotFoundException,
    FileContentDecodeException,
)

# Analyzers
from analyzers.base_analyzer import BaseAnalyzer
from analyzers.terraform_analyzer import TerraformAnalyzer
from analyzers.docker_analyzer import DockerAnalyzer
from analyzers.dependency_analyzer.manager import DependencyManager

logger = logging.getLogger(__name__)


class AnalysisOrchestrator:
    """
    Orchestrates the ARM compatibility analysis process for a GitHub repository.
    Uses GithubService to fetch data and delegates analysis to specific Analyzers.
    """

    def __init__(self, github_service: Optional[GithubService] = None):
        """
        Initializes the orchestrator, optionally with a pre-configured GithubService.
        Instantiates enabled analyzers based on config.
        """
        self.github_service = (
            github_service or GithubService()
        )  # Instantiate if not provided

        self.analyzers: Dict[str, BaseAnalyzer] = {}
        self._analyzer_instances: Dict[str, Type[BaseAnalyzer]] = {
            "terraform": TerraformAnalyzer,
            "docker": DockerAnalyzer,
            "dependency": DependencyManager,
            # Add mappings for new analyzers here
        }

        self.enabled_analyzer_names: List[str] = []
        for name, enabled in ENABLED_ANALYZERS.items():
            if enabled and name in self._analyzer_instances:
                try:
                    self.analyzers[name] = self._analyzer_instances[name]()
                    self.enabled_analyzer_names.append(name)
                    logger.info(f"Enabled analyzer: {name}")
                except Exception as e:
                    logger.error(
                        f"Failed to instantiate analyzer '{name}': {e}", exc_info=True
                    )
            elif enabled:
                logger.warning(
                    f"Analyzer '{name}' is enabled in config but no implementation class found."
                )

        if not self.analyzers:
            logger.warning(
                "No analyzers are enabled or instantiated. Analysis will yield empty results."
            )

    def _extract_repo_info(self, repo_url: str) -> Tuple[str, str]:
        """Extracts owner and repo name from a GitHub URL."""
        # Allow optional .git suffix and trailing slash
        pattern = r"https?://github\.com/([^/]+)/([^/\s]+?)(?:\.git)?/?$"
        match = re.match(pattern, repo_url.strip())
        if not match:
            raise ValueError(f"Invalid GitHub repository URL format: {repo_url}")
        return match.group(1), match.group(2)

    def analyze_repository(self, github_url: str) -> Dict[str, Any]:
        """
        Performs the end-to-end ARM compatibility analysis for a given GitHub repository URL.

        Args:
            github_url: The URL of the GitHub repository.

        Returns:
            A dictionary containing the comprehensive analysis results or an error message.
            Structure on success:
            {
                'repository': 'owner/repo',
                'github_url': '...',
                'default_branch': '...',
                'analysis_details': {
                    'instance_types': {'results': [...], 'recommendations': [...], 'reasoning': [...]}, # If enabled
                    'docker_images': {'results': [...], 'recommendations': [...], 'reasoning': [...]},  # If enabled
                    'dependencies': {'results': [...], 'recommendations': [...], 'reasoning': [...]},   # If enabled
                },
                'overall_compatibility': 'compatible' | 'incompatible' | 'unknown',
                'recommendations': [...], # Combined recommendations
                'context': {
                    'analysis_summary': {'files_analyzed_by_type': {...}, 'total_files_analyzed': ...},
                    'reasoning': [...], # Combined reasoning
                    'process_description': '...',
                    'enabled_analyzers': [...],
                    'statistics': {...}
                }
            }
            Structure on error:
            {
                'repository': 'owner/repo' or github_url,
                'github_url': '...',
                'error': 'Error message description'
            }
        """
        owner = None
        repo = None
        try:
            owner, repo = self._extract_repo_info(github_url)
            repo_identifier = f"{owner}/{repo}"
            logger.info(f"Starting ARM compatibility analysis for: {repo_identifier}")

            if not self.analyzers:
                raise ValueError("No analysis modules are enabled.")

            # 1. Get Repository Info & Default Branch
            repo_info = self.github_service.get_repository_info(owner, repo)
            default_branch = repo_info.get(
                "default_branch", "main"
            )  # Default to 'main' if not found
            logger.info(f"Using default branch: {default_branch}")

            # 2. Get Repository File Tree
            tree_data = self.github_service.get_repository_tree(
                owner, repo, default_branch
            )
            if not tree_data or "tree" not in tree_data:
                logger.warning(
                    f"Could not retrieve file tree for {repo_identifier}. It might be empty or inaccessible."
                )
                # Proceed with empty tree, analysis will yield 'unknown'
                tree_items = []
            else:
                tree_items = tree_data.get("tree", [])

            # 3. Identify Relevant Files and Fetch Content
            files_to_analyze: Dict[str, List[str]] = {
                name: [] for name in self.enabled_analyzer_names
            }
            relevant_files_found = False
            for item in tree_items:
                if item.get("type") == "blob":  # Only process files
                    path = item["path"]
                    for analyzer_name, analyzer_instance in self.analyzers.items():
                        for pattern in analyzer_instance.relevant_file_patterns:
                            # Use re.search for flexibility (matches anywhere in path if needed)
                            # Add IGNORECASE for robustness
                            if re.search(pattern, path, re.IGNORECASE):
                                files_to_analyze[analyzer_name].append(path)
                                relevant_files_found = True
                                logger.debug(
                                    f"Found relevant file '{path}' for analyzer '{analyzer_name}'"
                                )
                                break  # File matched this analyzer, move to next file item

            if not relevant_files_found:
                logger.warning(
                    f"No relevant files found for enabled analyzers in {repo_identifier}."
                )
                # Proceed, analysis will likely result in 'unknown'

            # 4. Perform Analysis for Each Relevant File
            raw_analysis_outputs: Dict[str, List[Dict[str, Any]]] = {
                name: [] for name in self.enabled_analyzer_names
            }
            total_files_analyzed = 0
            files_analyzed_by_type: Dict[str, int] = {
                name: 0 for name in self.enabled_analyzer_names
            }

            for analyzer_name, file_paths in files_to_analyze.items():
                analyzer_instance = self.analyzers[analyzer_name]
                logger.info(
                    f"Analyzing {len(file_paths)} files for '{analyzer_name}'..."
                )
                for file_path in file_paths:
                    try:
                        content = self.github_service.get_file_content(
                            owner, repo, file_path, default_branch
                        )
                        if content is not None:
                            analysis_output = analyzer_instance.analyze(
                                content, file_path
                            )
                            # Store the raw output from the analyze method, including the file path
                            raw_analysis_outputs[analyzer_name].append(
                                {
                                    "file": file_path,
                                    **analysis_output,  # Include the results from analyze()
                                }
                            )
                            total_files_analyzed += 1
                            files_analyzed_by_type[analyzer_name] += 1
                        else:
                            logger.warning(
                                f"Could not get content for file: {file_path} (Skipping analysis for this file)"
                            )
                    except FileContentDecodeException as decode_err:
                        logger.error(
                            f"Error decoding file {file_path}: {decode_err}. Skipping analysis."
                        )
                    except Exception as file_error:
                        logger.error(
                            f"Error analyzing file {file_path} with {analyzer_name}: {file_error}",
                            exc_info=True,
                        )
                        # Optionally record file-specific errors if needed

            logger.info(
                f"Total files analyzed across all enabled types: {total_files_analyzed}"
            )

            # 5. Aggregate Results for Each Analyzer
            aggregated_results: Dict[str, Dict[str, Any]] = {}
            combined_recommendations: List[str] = []
            combined_reasoning: List[str] = []

            for analyzer_name, analyzer_instance in self.analyzers.items():
                logger.info(f"Aggregating results for '{analyzer_name}'...")
                try:
                    # Pass the raw outputs collected for this analyzer
                    agg_result = analyzer_instance.aggregate_results(
                        raw_analysis_outputs[analyzer_name]
                    )
                    aggregated_results[analyzer_instance.analysis_key] = (
                        agg_result  # Use the key defined by the analyzer
                    )
                    combined_recommendations.extend(
                        agg_result.get("recommendations", [])
                    )
                    combined_reasoning.extend(agg_result.get("reasoning", []))
                    logger.info(f"Finished aggregating for '{analyzer_name}'.")
                except Exception as agg_error:
                    logger.error(
                        f"Error aggregating results for {analyzer_name}: {agg_error}",
                        exc_info=True,
                    )
                    # Store error state? For now, log and continue.
                    aggregated_results[analyzer_instance.analysis_key] = {
                        "error": str(agg_error),
                        "results": [],
                        "recommendations": [],
                        "reasoning": [],
                    }

            # 6. Determine Overall Compatibility and Final Structure
            final_result = self._determine_overall_compatibility(
                aggregated_results,
                combined_recommendations,
                combined_reasoning,
                files_analyzed_by_type,
                total_files_analyzed,
            )

            logger.info(
                f"Overall compatibility assessment for {repo_identifier}: {final_result.get('overall_compatibility')}"
            )

            return {
                "repository": repo_identifier,
                "github_url": github_url,
                "default_branch": default_branch,
                **final_result,  # Merge the overall compatibility results
            }

        except (
            ValueError,
            RepositoryNotFoundException,
            BranchNotFoundException,
            GithubApiException,
        ) as known_err:
            logger.error(f"Analysis failed for {github_url}: {known_err}")
            return {
                "repository": repo_identifier if owner else github_url,
                "github_url": github_url,
                "error": str(known_err),
            }
        except Exception as e:
            # Catch unexpected errors during the orchestration
            logger.exception(
                f"Critical error during analysis orchestration for {github_url}: {e}"
            )
            return {
                "repository": repo_identifier if owner else github_url,
                "github_url": github_url,
                "error": f"An unexpected error occurred: {e}",
            }

    def _determine_overall_compatibility(
        self,
        aggregated_results: Dict[str, Dict[str, Any]],
        combined_recommendations: List[str],
        combined_reasoning: List[str],
        files_analyzed_by_type: Dict[str, int],
        total_files_analyzed: int,
    ) -> Dict[str, Any]:
        """
        Determines the overall compatibility status based on aggregated results
        from all enabled analyzers. Builds the final result structure.

        Args:
            aggregated_results: Dict where keys are analysis_keys (e.g., 'instance_types')
                                and values are the output of each analyzer's aggregate_results.
            combined_recommendations: All recommendations gathered from analyzers.
            combined_reasoning: All reasoning strings gathered from analyzers.
            files_analyzed_by_type: Count of files analyzed per analyzer name.
            total_files_analyzed: Total count of files analyzed.

        Returns:
            A dictionary containing the final analysis structure including overall status,
            details, recommendations, and context.
        """
        overall_compatibility = "unknown"  # Default status
        has_findings = False
        has_incompatible = False
        incompatible_count = 0
        compatible_count = 0
        unknown_count = 0

        # Check results from each enabled analyzer's aggregation output
        for analysis_key, agg_result in aggregated_results.items():
            results_list = agg_result.get("results", [])
            if results_list:  # Check if this analyzer produced any findings
                has_findings = True
                for item in results_list:
                    comp_status = item.get("compatible")
                    if comp_status is False:
                        has_incompatible = True
                        incompatible_count += 1
                    elif comp_status is True:
                        compatible_count += 1
                    else:  # partial or unknown
                        unknown_count += 1
            elif agg_result.get("error"):
                # If an analyzer failed aggregation, treat as unknown contribution
                unknown_count += 1  # Or handle error state more explicitly?

        # Determine overall status
        if not has_findings and total_files_analyzed == 0:
            overall_compatibility = "unknown"
            final_reasoning = [
                "No relevant files found for enabled analyzers."
            ] + combined_reasoning
            final_recommendations = [
                "Verify repository structure and enabled analyzers if analysis was expected."
            ] + combined_recommendations
        elif not has_findings and total_files_analyzed > 0:
            overall_compatibility = "unknown"
            final_reasoning = [
                "No specific ARM64 compatibility indicators found in analyzed files."
            ] + combined_reasoning
            final_recommendations = [
                "Manual verification recommended as no specific issues were detected."
            ] + combined_recommendations
        elif has_incompatible:
            overall_compatibility = "incompatible"
            final_reasoning = [
                "Repository marked as incompatible due to one or more components conflicting with ARM64."
            ] + combined_reasoning
            final_recommendations = (
                combined_recommendations  # Use recommendations generated by analyzers
            )
        else:  # Has findings, but none are explicitly incompatible
            overall_compatibility = "compatible"  # Mark as likely compatible
            final_reasoning = [
                "Repository appears likely compatible with ARM64 as no explicitly incompatible components were found."
            ] + combined_reasoning
            final_recommendations = combined_recommendations

        # Deduplicate final recommendations and reasoning
        unique_recommendations = sorted(list(set(final_recommendations)))
        # Keep reasoning order for now, might contain useful flow
        unique_reasoning = list(
            dict.fromkeys(final_reasoning)
        )  # Simple order-preserving dedupe

        # Build final context block
        context = {
            "analysis_summary": {
                "files_analyzed_by_type": files_analyzed_by_type,
                "total_files_analyzed": total_files_analyzed,
            },
            "reasoning": unique_reasoning,
            "process_description": "ARM compatibility analyzed by examining relevant files for enabled analyzers.",
            "enabled_analyzers": self.enabled_analyzer_names,
            "statistics": {
                "incompatible_items": incompatible_count,
                "compatible_items": compatible_count,
                "unknown_items": unknown_count,  # Includes 'partial' status for this count
                "total_recommendations": len(unique_recommendations),
            },
        }

        return {
            "analysis_details": aggregated_results,  # Contains detailed results per analyzer
            "overall_compatibility": overall_compatibility,
            "recommendations": unique_recommendations,
            "context": context,
        }


# Example Usage (for understanding, not for Lambda execution)
# if __name__ == "__main__":
#     logging.basicConfig(level=logging.DEBUG)
#     # Assumes GITHUB_TOKEN is set in environment
#     orchestrator = AnalysisOrchestrator()
#     # test_url = "https://github.com/some_owner/some_repo"
#     test_url = "https://github.com/boto/boto3" # Example public repo
#     try:
#         result = orchestrator.analyze_repository(test_url)
#         import json
#         print(json.dumps(result, indent=2))
#     except Exception as e:
#         print(f"Analysis failed: {e}")
