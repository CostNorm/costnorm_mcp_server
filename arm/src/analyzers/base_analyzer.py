from abc import ABC, abstractmethod
from typing import Dict, Any, List

# Import the interface definition
from core.interfaces import Analyzer


class BaseAnalyzer(Analyzer, ABC):
    """
    Abstract base class for all specific analyzers (Terraform, Docker, Dependency).
    Ensures that all concrete analyzers implement the required methods
    defined in the Analyzer interface.
    """

    @abstractmethod
    def analyze(self, file_content: str, file_path: str) -> Dict[str, Any]:
        """
        Analyzes the content of a single file relevant to this analyzer.
        Implementation required by subclasses.
        """
        pass

    @abstractmethod
    def aggregate_results(
        self, analysis_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates results from multiple file analyses for this analyzer type.
        Implementation required by subclasses.
        """
        pass

    @property
    @abstractmethod
    def relevant_file_patterns(self) -> List[str]:
        """
        Returns a list of regex patterns for file paths relevant to this analyzer.
        Implementation required by subclasses.
        """
        pass

    @property
    @abstractmethod
    def analysis_key(self) -> str:
        """
        Returns the key for this analyzer's output in the final results dictionary.
        Implementation required by subclasses.
        """
        pass


# No concrete implementation here, just enforcing the interface contract.
