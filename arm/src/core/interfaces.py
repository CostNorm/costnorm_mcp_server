from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class Analyzer(ABC):
    """Interface for analyzing a specific aspect (Terraform, Docker, Dependencies)."""

    @abstractmethod
    def analyze(self, file_content: str, file_path: str) -> Dict[str, Any]:
        """
        Analyzes the content of a single file relevant to this analyzer.

        Args:
            file_content: The content of the file.
            file_path: The path to the file within the repository.

        Returns:
            A dictionary containing the raw analysis results for this file.
            The structure depends on the specific analyzer.
        """
        pass

    @abstractmethod
    def aggregate_results(
        self, analysis_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates results from multiple file analyses for this analyzer type
        into the final format expected by the orchestrator.

        Args:
            analysis_outputs: A list of dictionaries, where each dictionary is the
                              output of the `analyze` method for a single file,
                              often including the 'file' path.

        Returns:
            A dictionary containing the aggregated results, recommendations,
            and reasoning specific to this analyzer type. Expected keys might
            include 'results' (list of findings), 'recommendations' (list of strings),
            'reasoning' (list of strings).
        """
        pass

    @property
    @abstractmethod
    def relevant_file_patterns(self) -> List[str]:
        """
        Returns a list of regex patterns for file paths relevant to this analyzer.
        Used by the orchestrator to identify which files to process.
        """
        pass

    @property
    @abstractmethod
    def analysis_key(self) -> str:
        """
        Returns the key that should be used in the final overall compatibility
        results dictionary to store the output of this analyzer's aggregation.
        (e.g., "instance_types", "docker_images", "dependencies").
        """
        pass


class DependencyChecker(ABC):
    """Interface for checking compatibility of dependencies for a specific ecosystem (e.g., Python, Node.js)."""

    @abstractmethod
    def check_compatibility(self, dependency_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks the ARM64 compatibility of a single parsed dependency.

        Args:
            dependency_info: A dictionary containing information about the dependency
                             (e.g., name, version, source file).

        Returns:
            A dictionary containing the compatibility status ('compatible': bool/str),
            reasoning ('reason': str), and potentially other details.
        """
        pass

    @abstractmethod
    def parse_dependencies(
        self, file_content: str, file_path: str
    ) -> List[Dict[str, Any]]:
        """
        Parses dependencies from a specific manifest file (e.g., requirements.txt, package.json).

        Args:
            file_content: The content of the manifest file.
            file_path: The path to the manifest file.

        Returns:
            A list of dictionaries, each representing a parsed dependency with
            enough information for the `check_compatibility` method (e.g., name,
            version specifier, file path).
        """
        pass
