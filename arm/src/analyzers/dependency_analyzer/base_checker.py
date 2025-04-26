from abc import ABC, abstractmethod
from typing import Dict, Any, List

# Import the interface definition from the core package
# Use relative import if preferred and structure allows, but absolute might be safer in Lambda
from core.interfaces import DependencyChecker


class BaseDependencyChecker(DependencyChecker, ABC):
    """
    Abstract base class for specific dependency checkers (e.g., Python, Node.js).
    Ensures that all concrete checkers implement the required methods
    defined in the DependencyChecker interface.
    """

    @abstractmethod
    def check_compatibility(self, dependency_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks the ARM64 compatibility of a single parsed dependency.
        Implementation required by subclasses (e.g., PythonChecker, JSChecker).
        """
        pass

    @abstractmethod
    def parse_dependencies(
        self, file_content: str, file_path: str
    ) -> List[Dict[str, Any]]:
        """
        Parses dependencies from a specific manifest file.
        Implementation required by subclasses.
        """
        pass


# No concrete implementation here, just enforcing the interface contract.
