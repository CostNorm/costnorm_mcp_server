import base64
import requests
import logging
from typing import Dict, Any, Optional


# Keep exceptions at module level for potential external use
class GithubApiException(Exception):
    """Base exception for Github API errors."""

    pass


class RepositoryNotFoundException(GithubApiException):
    """Raised when a repository is not found (404)."""

    pass


class BranchNotFoundException(GithubApiException):
    """Raised when a branch is not found."""

    pass


class FileContentDecodeException(GithubApiException):
    """Raised when decoding file content fails."""

    pass


logger = logging.getLogger(
    __name__
)  # Keep module-level logger for potential use outside class


class GithubService:
    """
    Handles interactions with the GitHub API.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, github_token: Optional[str]):
        """
        Initializes the GithubService.

        Args:
            github_token: The GitHub personal access token. Can be None.
        """
        self.token = github_token
        self.logger = logging.getLogger(
            f"{__name__}.{self.__class__.__name__}"
        )  # Instance logger
        if not self.token:
            self.logger.warning(
                "GITHUB_TOKEN is not set. API rate limits may be lower."
            )

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for GitHub API requests."""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    def get_repository_info(self, owner: str, repo: str) -> Dict[str, Any]:
        """
        Get basic information about a repository.

        Raises:
            RepositoryNotFoundException: If the repository returns a 404.
            GithubApiException: For other non-200 responses.
            requests.exceptions.RequestException: For network errors.
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}"
        self.logger.info(f"Fetching repository info for {owner}/{repo}")
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                self.logger.error(f"Repository not found: {owner}/{repo}")
                raise RepositoryNotFoundException(
                    f"Repository {owner}/{repo} not found (404)."
                )
            else:
                self.logger.error(
                    f"Error getting repository info for {owner}/{repo}: {response.status_code} - {response.text}"
                )
                raise GithubApiException(
                    f"Failed to get repository info for {owner}/{repo}: HTTP {response.status_code}"
                )
        except requests.exceptions.RequestException as e:
            self.logger.error(
                f"Network error getting repository info for {owner}/{repo}: {e}"
            )
            raise

    def get_repository_tree(
        self, owner: str, repo: str, branch: str = "main"
    ) -> Dict[str, Any]:
        """
        Get the file tree of a repository for a specific branch.

        Raises:
            BranchNotFoundException: If the specified branch is not found.
            GithubApiException: For other non-200 responses during branch or tree fetching.
            requests.exceptions.RequestException: For network errors.
        """
        self.logger.info(
            f"Fetching repository tree for {owner}/{repo} (branch: {branch})"
        )
        # First, get the branch information to get the latest commit SHA
        branch_url = f"{self.BASE_URL}/repos/{owner}/{repo}/branches/{branch}"
        try:
            branch_response = requests.get(
                branch_url, headers=self._get_headers(), timeout=10
            )
            if branch_response.status_code != 200:
                if branch_response.status_code == 404:
                    self.logger.error(f"Branch '{branch}' not found for {owner}/{repo}")
                    raise BranchNotFoundException(
                        f"Branch '{branch}' not found for {owner}/{repo} (404)."
                    )
                else:
                    self.logger.error(
                        f"Error getting branch info for {owner}/{repo}/{branch}: {branch_response.status_code} - {branch_response.text}"
                    )
                    raise GithubApiException(
                        f"Failed to get branch info for {owner}/{repo}/{branch}: HTTP {branch_response.status_code}"
                    )

            branch_data = branch_response.json()
            commit_sha = branch_data.get("commit", {}).get("sha")

            if not commit_sha:
                self.logger.error(
                    f"Could not find commit SHA for branch '{branch}' in {owner}/{repo}"
                )
                raise GithubApiException(
                    f"Could not find commit SHA for branch '{branch}' in {owner}/{repo}"
                )

            # Now, get the tree using the commit SHA
            tree_url = f"{self.BASE_URL}/repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1"
            self.logger.debug(f"Fetching tree using SHA {commit_sha}")
            tree_response = requests.get(
                tree_url, headers=self._get_headers(), timeout=30
            )  # Longer timeout for potentially large trees

            if tree_response.status_code == 200:
                return tree_response.json()
            else:
                self.logger.error(
                    f"Error getting repository tree for {owner}/{repo} (SHA: {commit_sha}): {tree_response.status_code} - {tree_response.text}"
                )
                raise GithubApiException(
                    f"Failed to get repository tree for {owner}/{repo}: HTTP {tree_response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.logger.error(
                f"Network error getting repository tree for {owner}/{repo}/{branch}: {e}"
            )
            raise

    def get_file_content(
        self, owner: str, repo: str, path: str, branch: str = "main"
    ) -> Optional[str]:
        """
        Get the decoded content of a specific file. Returns None if file not found.

        Raises:
            GithubApiException: For non-200/404 responses.
            FileContentDecodeException: If decoding fails.
            requests.exceptions.RequestException: For network errors.
        """
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}?ref={branch}"
        self.logger.info(
            f"Fetching file content for {owner}/{repo}/{path} (branch: {branch})"
        )
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=15)

            if response.status_code == 200:
                content_data = response.json()
                if (
                    content_data.get("encoding") == "base64"
                    and "content" in content_data
                ):
                    try:
                        # Add padding if needed for base64 decoding
                        encoded_content = content_data["content"]
                        missing_padding = len(encoded_content) % 4
                        if missing_padding:
                            encoded_content += "=" * (4 - missing_padding)

                        decoded_bytes = base64.b64decode(encoded_content)

                        # Attempt decoding with utf-8, fallback to latin-1 if needed
                        try:
                            decoded_content = decoded_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            self.logger.warning(
                                f"UTF-8 decoding failed for {path}, trying latin-1."
                            )
                            decoded_content = decoded_bytes.decode(
                                "latin-1"
                            )  # Common fallback

                        return decoded_content
                    except (base64.binascii.Error, UnicodeDecodeError, Exception) as e:
                        self.logger.error(f"Error decoding content for {path}: {e}")
                        raise FileContentDecodeException(
                            f"Failed to decode content for file {path}: {e}"
                        )
                elif content_data.get("type") != "file":
                    self.logger.warning(
                        f"Path '{path}' is not a file (type: {content_data.get('type')}). Cannot get content."
                    )
                    return None  # Or raise specific error? Returning None seems reasonable.
                else:
                    self.logger.warning(
                        f"Could not get base64 content for file {path}. Data: {content_data}"
                    )
                    raise GithubApiException(
                        f"Unexpected content format for file {path}."
                    )

            elif response.status_code == 404:
                self.logger.warning(
                    f"File not found: {owner}/{repo}/{path} (branch: {branch})"
                )
                return None  # File not found is not necessarily a critical error for the whole process
            else:
                self.logger.error(
                    f"Error getting file content for {path}: {response.status_code} - {response.text}"
                )
                raise GithubApiException(
                    f"Failed to get file content for {path}: HTTP {response.status_code}"
                )

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Network error getting file content for {path}: {e}")
            raise
