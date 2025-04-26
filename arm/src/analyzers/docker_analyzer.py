import re
import logging
import requests
import time
from typing import Dict, Any, List, Optional, Tuple, Union

# Assuming base_analyzer and config are in the correct relative paths
from .base_analyzer import BaseAnalyzer

# Make sure config.py exists and has DOCKERHUB_USERNAME/PASSWORD or handle their absence
try:
    from config import DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD
except ImportError:
    DOCKERHUB_USERNAME = None
    DOCKERHUB_PASSWORD = None
    logging.warning(
        "Could not import Docker Hub credentials from config. Anonymous access will be used."
    )


logger = logging.getLogger(__name__)

# --- Module-level Caches ---
_DOCKER_MANIFEST_CACHE: Dict[str, Dict[str, Any]] = {}
_DOCKER_AUTH_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}

# --- Constants ---
DOCKER_HUB_REGISTRY = "registry-1.docker.io"
DOCKER_HUB_AUTH_URL = "https://auth.docker.io/token"
DOCKER_MANIFEST_V2_HEADER = "application/vnd.docker.distribution.manifest.v2+json"
DOCKER_MANIFEST_LIST_V2_HEADER = (
    "application/vnd.docker.distribution.manifest.list.v2+json"
)
OCI_MANIFEST_V1_HEADER = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX_V1_HEADER = "application/vnd.oci.image.index.v1+json"
ACCEPT_HEADERS = f"{OCI_INDEX_V1_HEADER}, {OCI_MANIFEST_V1_HEADER}, {DOCKER_MANIFEST_LIST_V2_HEADER}, {DOCKER_MANIFEST_V2_HEADER}"
ARM64_ARCHS = ["arm64", "aarch64"]
X86_ARCHS = ["amd64", "x86_64"]


class DockerAnalyzer(BaseAnalyzer):
    """
    Analyzes Dockerfiles for potential ARM64 compatibility, even if explicitly
    built for amd64 using --platform. Includes base image manifest inspection.
    """

    @property
    def analysis_key(self) -> str:
        return "docker_analysis"  # Changed key slightly for clarity

    @property
    def relevant_file_patterns(self) -> List[str]:
        return [r"dockerfile(\..*)?$", r"\.dockerfile$"]

    def analyze(self, file_content: str, file_path: str) -> Dict[str, Any]:
        """
        Analyzes Dockerfile content, extracting base images (noting platform flags)
        and potentially architecture-specific commands.

        Args:
            file_content: The content of the Dockerfile.
            file_path: The path to the Dockerfile.

        Returns:
            A dictionary for the file including 'base_images_info' (list of dicts
            with 'name', 'platform_used', 'line') and 'arch_specific_lines'.
            Example: {
                'file': 'Dockerfile',
                'base_images_info': [
                    {'name': 'python:3.9-slim', 'platform_used': 'linux/amd64', 'line': 'FROM --platform=linux/amd64 python:3.9-slim'},
                    {'name': 'alpine:latest', 'platform_used': None, 'line': 'FROM alpine:latest'}
                ],
                'arch_specific_lines': ['RUN dpkg --add-architecture amd64', 'COPY my_x86_binary /usr/local/bin/']
            }
        """
        logger.debug(f"Analyzing Dockerfile: {file_path}")
        results: Dict[str, Any] = {
            "file": file_path,
            "base_images_info": [],
            "arch_specific_lines": [],
        }

        try:
            # Preprocess file content to join backslash-continued lines
            # This handles multiline RUN, COPY, etc. commands
            lines = file_content.splitlines()
            joined_lines = []
            current_line = ""

            for line in lines:
                line_stripped = line.strip()
                if not line_stripped or line_stripped.startswith("#"):
                    # Keep comments as separate lines
                    if current_line:
                        joined_lines.append(current_line)
                        current_line = ""
                    joined_lines.append(line)
                    continue

                if current_line and current_line.endswith("\\"):
                    # Continue previous line
                    current_line = current_line[:-1] + " " + line_stripped
                else:
                    # Start new line after adding any pending line
                    if current_line:
                        joined_lines.append(current_line)
                    current_line = line_stripped

                # Check if this line continues
                if line_stripped.endswith("\\"):
                    continue
                else:
                    # Line doesn't continue, add it and reset
                    joined_lines.append(current_line)
                    current_line = ""

            # Add final pending line if any
            if current_line:
                joined_lines.append(current_line)

            # Rejoin for regex processing
            processed_content = "\n".join(joined_lines)

            # Regex to capture FROM instruction, optionally capture --platform, get image name, handle AS
            # Made platform capture optional and non-capturing for the value initially
            # Refined regex to better capture platform value if present
            from_pattern = re.compile(
                r"^\s*FROM\s+(?:--platform=(\S+)\s+)?([\w.:/@-]+)(?:\s+AS\s+\S+)?\s*$",
                re.IGNORECASE | re.MULTILINE,
            )

            for match in from_pattern.finditer(processed_content):
                full_line = match.group(0).strip()
                platform_used = match.group(
                    1
                )  # Platform value (e.g., linux/amd64) or None
                base_image_name = match.group(2)  # The actual image name

                # Clean potential variable substitutions like ${VAR}
                if not base_image_name.startswith("${"):
                    results["base_images_info"].append(
                        {
                            "name": base_image_name,
                            "platform_used": platform_used,
                            "line": full_line,
                        }
                    )

            logger.debug(
                f"Found base images in {file_path}: {results['base_images_info']}"
            )

            # Look for architecture-specific keywords/patterns in relevant commands
            arch_keywords = (
                X86_ARCHS
                + ARM64_ARCHS
                + ["graviton", "--platform", "TARGETARCH", "TARGETPLATFORM"]
            )
            # More specific patterns for potentially problematic commands
            arch_patterns = [
                re.compile(r"dpkg --add-architecture\s+(amd64|x86_64)", re.IGNORECASE),
                re.compile(
                    r"(wget|curl)\s+.*\/(.*(amd64|x86_64).*\.(deb|rpm|tar\.gz|zip|bin))",
                    re.IGNORECASE,
                ),  # Download x86 binaries
                re.compile(
                    r"(COPY|ADD)\s+.*\.(so|a)(\s+|$)", re.IGNORECASE
                ),  # Copying libraries
                re.compile(
                    r"(COPY|ADD)\s+.*(amd64|x86_64)", re.IGNORECASE
                ),  # Copying files with arch names
            ]

            arch_lines_found = set()  # Use set to avoid duplicates for this file
            # Check lines starting with relevant Docker instructions
            command_pattern = re.compile(
                r"^\s*(FROM|RUN|ARG|ENV|COPY|ADD)\s+", re.IGNORECASE
            )

            for line in joined_lines:
                line_strip = line.strip()
                if not line_strip or line_strip.startswith(
                    "#"
                ):  # Skip empty/comment lines
                    continue

                # Check keywords first (broad check)
                line_lower = line_strip.lower()
                keyword_found = False
                if command_pattern.match(line_strip):
                    for keyword in arch_keywords:
                        keyword_pattern = (
                            r"\b" + re.escape(keyword) + r"\b"
                            if keyword.isalnum()
                            else re.escape(keyword)
                        )
                        if re.search(keyword_pattern, line_lower):
                            arch_lines_found.add(line_strip)
                            keyword_found = True
                            break  # Keyword found, add line and check next line

                # If no keyword found, check specific problematic patterns (more targeted)
                if not keyword_found:
                    for pattern in arch_patterns:
                        if pattern.search(line_strip):
                            arch_lines_found.add(line_strip)
                            break  # Pattern found, add line and check next line

            if arch_lines_found:
                results["arch_specific_lines"] = sorted(list(arch_lines_found))
                logger.debug(
                    f"Found potential architecture specific lines in {file_path}: {results['arch_specific_lines']}"
                )

        except Exception as e:
            logger.error(f"Error parsing Dockerfile {file_path}: {e}", exc_info=True)
            # Return structure indicating error for this file if needed, or empty lists
            results["base_images_info"] = []
            results["arch_specific_lines"] = ["ERROR parsing file"]  # Indicate error

        return results

    # --- Manifest Inspection Logic ---
    # _get_docker_auth_token and _parse_image_name remain the same as your provided code
    # Make sure _parse_image_name handles 'scratch' special case if necessary
    # Make sure _get_docker_auth_token handles missing credentials gracefully

    def _get_docker_auth_token(self, registry: str, repository: str) -> Optional[str]:
        """Gets an auth token for the specified Docker registry and repository."""
        global _DOCKER_AUTH_TOKEN_CACHE
        if registry != DOCKER_HUB_REGISTRY:
            logger.debug(
                f"Auth token retrieval not implemented for registry: {registry}. Proceeding without token."
            )
            return None

        # Use username+repository as cache key for proper scope-based caching
        cache_key = f"{DOCKERHUB_USERNAME or 'anonymous'}:{repository}"
        cached_token, expiry = _DOCKER_AUTH_TOKEN_CACHE.get(cache_key, (None, 0))

        if cached_token and expiry > time.time() + 60:
            logger.debug(
                f"Using cached Docker Hub token ({cache_key}) for {repository}"
            )
            return cached_token

        # If anonymous or no credentials, try anonymous token request
        auth_args = {}
        if DOCKERHUB_USERNAME and DOCKERHUB_PASSWORD:
            auth_args["auth"] = (DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD)
            logger.info(
                f"Requesting new Docker Hub auth token for scope: {repository} (User: {DOCKERHUB_USERNAME})"
            )
        else:
            logger.info(
                f"Requesting new Docker Hub anonymous token for scope: {repository}"
            )

        auth_params = {
            "service": "registry.docker.io",
            "scope": f"repository:{repository}:pull",
        }
        try:
            response = requests.get(
                DOCKER_HUB_AUTH_URL,
                params=auth_params,
                timeout=10,
                **auth_args,  # Pass auth tuple if available
            )
            response.raise_for_status()
            token_data = response.json()
            token = token_data.get("token")
            expires_in = token_data.get("expires_in", 300)  # Default to 5 minutes
            expiry_time = time.time() + expires_in

            if token:
                logger.info(
                    f"Successfully obtained Docker Hub token ({cache_key}) for {repository}"
                )
                _DOCKER_AUTH_TOKEN_CACHE[cache_key] = (token, expiry_time)
                return token
            else:
                logger.error(
                    f"Failed to get token from Docker Hub auth response ({cache_key})."
                )
                return None
        except requests.exceptions.RequestException as e:
            # Handle 401 specifically if auth was attempted
            if (
                DOCKERHUB_USERNAME
                and e.response is not None
                and e.response.status_code == 401
            ):
                logger.error(
                    f"Docker Hub authentication failed for user {DOCKERHUB_USERNAME}. Check credentials.",
                    exc_info=False,
                )
            else:
                logger.error(
                    f"Error requesting Docker Hub token ({cache_key}): {e}",
                    exc_info=True,
                )
            return None
        except Exception as e:
            logger.error(
                f"Unexpected error getting Docker Hub token ({cache_key}): {e}",
                exc_info=True,
            )
            return None

    def _parse_image_name(self, image_name: str) -> Tuple[str, str, str]:
        """
        Parses image name into registry, repository, and tag/digest.

        Note: This manual parsing handles most common cases but has known edge cases.
        TODO: Consider replacing with a dedicated parser library like "docker-name"
        which would reduce code complexity and improve maintainability while
        handling all edge cases correctly.
        """
        # Handle 'scratch' special case explicitly
        if image_name.lower() == "scratch":
            logger.debug("Parsed 'scratch' -> Special case, no registry/repo/tag.")
            return "scratch", "scratch", ""  # Or adjust as needed for downstream logic

        registry = DOCKER_HUB_REGISTRY
        repo_name = image_name
        tag_or_digest = "latest"

        if "/" in image_name:
            parts = image_name.split("/", 1)
            if (
                "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
            ):  # localhost check for local registries
                registry = parts[0]
                repo_part = parts[1]
            else:
                # Assumed Docker Hub user/repo format OR official image with namespace (e.g. bitnami/redis)
                repo_part = image_name
                # Determine if it needs library/ prefix - simpler heuristic: no '/' means official OR local
                # The logic below will handle separating repo/tag later
        else:
            # Simple image name like 'python', 'ubuntu', 'my-local-image'
            repo_part = image_name
            # Assume Docker Hub official image *only if* it looks like one (no dots, dashes, underscores?)
            # A bit risky, full qualification is always better. Let's default to library/ only for simple alphanumeric names.
            # Or better: rely on the manifest check to resolve library/ if needed (Docker Hub API handles this)
            # So, if no registry specified, assume Docker Hub and pass the name as is.
            if registry == DOCKER_HUB_REGISTRY and "/" not in repo_part:
                # Let's tentatively assume it *might* be official, but the API call will confirm
                # No need to add 'library/' here, the auth scope/API call will handle it.
                pass  # repo_part remains the simple name

        # Separate tag or digest from repo_part
        if "@" in repo_part:  # Check for digest first
            repo_name, tag_or_digest = repo_part.split("@", 1)
            tag_or_digest = f"@{tag_or_digest}"
        elif ":" in repo_part:
            # Check if the part after : is likely a port number for a registry
            repo_base, maybe_tag = repo_part.rsplit(":", 1)
            if (
                registry == DOCKER_HUB_REGISTRY
                and "/" not in repo_base
                and maybe_tag.isdigit()
            ):
                # Ambiguous case: image:port vs image:tag. Assume tag unless registry was specific.
                # If registry wasn't specified and image name has no slash, it's likely an official image with a tag.
                repo_name, tag_or_digest = repo_base, maybe_tag
            elif (
                "/" in repo_part or registry != DOCKER_HUB_REGISTRY
            ):  # If it has a slash or non-default registry, ':' is likely the tag separator
                repo_name, tag_or_digest = repo_part.rsplit(":", 1)
            else:
                # Default to assuming it's a tag for simple names on Docker Hub
                repo_name, tag_or_digest = repo_part.rsplit(":", 1)

        else:
            repo_name = repo_part
            # tag_or_digest remains 'latest'

        # Docker Hub quirk: Official images (like 'python') need 'library/' prefix in API calls
        # If registry is Docker Hub and repo_name has no '/', add 'library/'
        if registry == DOCKER_HUB_REGISTRY and "/" not in repo_name:
            logger.debug(
                f"Assuming official Docker Hub image for '{repo_name}', prepending 'library/'."
            )
            repo_name = f"library/{repo_name}"

        logger.debug(
            f"Parsed '{image_name}' -> Registry: {registry}, Repo: {repo_name}, Tag/Digest: {tag_or_digest}"
        )
        # Add error handling for unparseable names?
        return registry, repo_name, tag_or_digest

    def _check_image_compatibility_via_manifest(
        self, image_name_full: str
    ) -> Dict[str, Any]:
        """
        Checks Docker image ARM64 compatibility by inspecting its manifest via Registry API.
        Uses caching. Returns detailed compatibility info.

        Note: Currently optimized for Docker Hub. Support for other registries (GHCR, ECR, etc.)
        is limited and may return "unknown" compatibility. To support additional registries:
        1. Implement registry-specific authentication (parse 'WWW-Authenticate' header)
        2. Handle registry-specific manifest retrieval differences
        """
        global _DOCKER_MANIFEST_CACHE
        # Normalize cache key (e.g., handle implicit 'latest' tag)
        if (
            ":" not in image_name_full
            and "@" not in image_name_full
            and image_name_full != "scratch"
        ):
            cache_key = f"{image_name_full}:latest"
        else:
            cache_key = image_name_full

        if cache_key in _DOCKER_MANIFEST_CACHE:
            logger.debug(f"Using cached manifest result for {cache_key}")
            return _DOCKER_MANIFEST_CACHE[cache_key]

        if cache_key == "scratch:latest" or cache_key == "scratch":
            logger.debug("Manifest check for 'scratch' image: inherently multi-arch.")
            result = {
                "compatible": True,
                "reason": "Base image is 'scratch', which is inherently multi-arch.",
                "details": {"architectures": ["multiple"]},
                "checked_type": "special",
            }
            _DOCKER_MANIFEST_CACHE[cache_key] = result
            return result

        logger.info(f"Checking manifest for Docker image: {cache_key}")
        result: Dict[str, Any] = {
            "compatible": "unknown",  # True, False, unknown
            "reason": "Check not performed yet.",
            "details": {
                "architectures": []
            },  # Initialize with empty dictionary instead of None
            "checked_type": None,  # manifest, manifest_list, index, special, error
        }

        try:
            registry, repository, tag_or_digest = self._parse_image_name(cache_key)

            # For non-Docker Hub registries, we currently return "unknown" for ARM compatibility
            # TODO: Implement proper auth and manifest checking for other registries like ECR, GHCR, GCR
            # This would involve parsing WWW-Authenticate headers and using registry-specific auth flows
            if registry != DOCKER_HUB_REGISTRY and registry != "scratch":
                logger.info(
                    f"Non-Docker Hub registry detected: {registry}. Limited support for manifest checks."
                )
                if registry.endswith("amazonaws.com"):  # ECR
                    result["compatible"] = "unknown"
                    result["reason"] = (
                        "ECR images require AWS credentials. Cannot check manifest without proper IAM configuration."
                    )
                    result["checked_type"] = "limited_support"
                    _DOCKER_MANIFEST_CACHE[cache_key] = result
                    return result

            token = self._get_docker_auth_token(registry, repository)
            headers = {"Accept": ACCEPT_HEADERS}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            manifest_url = (
                f"https://{registry}/v2/{repository}/manifests/{tag_or_digest}"
            )
            logger.debug(f"Requesting manifest from: {manifest_url}")
            response = requests.get(manifest_url, headers=headers, timeout=15)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()
            manifest_data = response.json()

            architectures_found = set()
            is_arm64_compatible = False

            if content_type.startswith(
                DOCKER_MANIFEST_LIST_V2_HEADER
            ) or content_type.startswith(OCI_INDEX_V1_HEADER):
                result["checked_type"] = "manifest_list/index"
                manifests = manifest_data.get("manifests", [])
                if not manifests:
                    result["reason"] = "Manifest list/index is empty."
                else:
                    for entry in manifests:
                        platform = entry.get("platform", {})
                        arch = platform.get("architecture", "").lower()
                        os = platform.get("os", "").lower()
                        if arch and os:
                            architectures_found.add(f"{os}/{arch}")
                        if arch in ARM64_ARCHS and os == "linux":
                            is_arm64_compatible = True
                            # Don't break early, collect all architectures

            elif content_type.startswith(
                DOCKER_MANIFEST_V2_HEADER
            ) or content_type.startswith(OCI_MANIFEST_V1_HEADER):
                result["checked_type"] = "manifest"
                config_digest = manifest_data.get("config", {}).get("digest")
                if config_digest:
                    # Fetch config blob (more reliable)
                    try:
                        config_url = (
                            f"https://{registry}/v2/{repository}/blobs/{config_digest}"
                        )
                        logger.debug(f"Fetching config blob from: {config_url}")
                        config_response = requests.get(
                            config_url, headers=headers, timeout=10
                        )
                        config_response.raise_for_status()
                        config_data = config_response.json()
                        arch = config_data.get("architecture", "").lower()
                        os = config_data.get("os", "").lower()
                        if arch and os:
                            architectures_found.add(f"{os}/{arch}")
                        if arch in ARM64_ARCHS and os == "linux":
                            is_arm64_compatible = True
                    except Exception as config_e:
                        logger.warning(
                            f"Failed to fetch or parse config blob for {cache_key}: {config_e}. Relying on manifest top-level info if available."
                        )
                        # Fallback to less reliable top-level architecture if config fetch fails
                        arch = manifest_data.get("architecture", "").lower()
                        # Assume OS is linux if not specified? Risky.
                        if arch in ARM64_ARCHS:
                            # Cannot be certain about OS here
                            architectures_found.add(f"unknown/{arch}")
                            # Let's not mark compatible=True based on this uncertain data
                            logger.warning(
                                f"Found ARM arch '{arch}' in manifest top-level, but OS unknown/config failed."
                            )
                        elif arch:
                            architectures_found.add(f"unknown/{arch}")

                else:
                    # Fallback for older Docker v2 schema (less reliable)
                    arch = manifest_data.get("architecture", "").lower()
                    if arch in ARM64_ARCHS:
                        architectures_found.add(f"unknown/{arch}")  # OS unknown
                        logger.warning(
                            f"Found ARM arch '{arch}' in manifest top-level (no config digest). OS unknown."
                        )
                    elif arch:
                        architectures_found.add(f"unknown/{arch}")

                if not architectures_found:
                    result["reason"] = (
                        "Single manifest architecture could not be determined (missing config digest and architecture field)."
                    )

            else:
                result["reason"] = f"Unsupported manifest Content-Type: {content_type}"
                result["compatible"] = "unknown"

            # Final compatibility decision based on findings
            if is_arm64_compatible:
                result["compatible"] = True
                result["reason"] = "Image manifest supports linux/arm64."
            elif architectures_found:
                result["compatible"] = False
                result["reason"] = (
                    f"Image manifest does not list linux/arm64 support. Found: {', '.join(sorted(architectures_found))}"
                )
            else:
                # If we reached here with no architectures found and no specific error reason set above
                if (
                    result["compatible"] == "unknown"
                    and result["reason"] == "Check not performed yet."
                ):
                    result["reason"] = (
                        "Could not determine architecture support from manifest."
                    )

            result["details"] = {"architectures": sorted(list(architectures_found))}

        except requests.exceptions.HTTPError as e:
            result["checked_type"] = "error"
            status_code = e.response.status_code
            if status_code == 401:
                result["reason"] = (
                    "Authentication error accessing manifest. Check credentials or image visibility."
                )
            elif status_code == 403:
                result["reason"] = (
                    "Permission denied accessing manifest. Check repository permissions."
                )
            elif status_code == 404:
                result["reason"] = (
                    "Image manifest not found (404). Check image name, tag, and registry."
                )
            elif status_code == 429:
                result["reason"] = (
                    "API rate limit hit checking manifest. Try again later."
                )
            else:
                result["reason"] = f"HTTP error {status_code} checking manifest: {e}"
            logger.error(f"HTTP error checking manifest for {cache_key}: {e}")
            result["compatible"] = "unknown"  # Treat HTTP errors as unknown
            # Make sure details is set to a dictionary
            if result.get("details") is None:
                result["details"] = {"architectures": []}
        except requests.exceptions.RequestException as e:
            result["checked_type"] = "error"
            result["reason"] = f"Network error checking manifest: {e}"
            logger.error(f"Network error checking manifest for {cache_key}: {e}")
            result["compatible"] = "unknown"
            # Make sure details is set to a dictionary
            if result.get("details") is None:
                result["details"] = {"architectures": []}
        except Exception as e:
            result["checked_type"] = "error"
            result["reason"] = f"Unexpected error checking manifest: {e}"
            logger.exception(f"Unexpected error checking manifest for {cache_key}: {e}")
            result["compatible"] = "unknown"
            # Make sure details is set to a dictionary
            if result.get("details") is None:
                result["details"] = {"architectures": []}

        _DOCKER_MANIFEST_CACHE[cache_key] = result
        logger.info(
            f"Manifest check result for {cache_key}: {result['compatible']} - {result['reason']}"
        )
        return result

    # --- Aggregation ---

    def aggregate_results(
        self, analysis_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates Dockerfile analysis, checks base image potential, and provides
        recommendations for ARM64 migration suitability.

        Args:
            analysis_outputs: List of outputs from the `analyze` method.
              Example: [{'file': 'df1', 'base_images_info': [{'name': 'py', 'platform': 'amd64', 'line':'...'}], 'arch_lines':[]}, ...]

        Returns:
            Aggregated results including potential compatibility and recommendations.
            'results': List of detailed assessments per unique base image.
            'recommendations': Actionable advice for ARM migration.
            'reasoning': Supporting details for the recommendations.
        """
        image_assessments: List[Dict[str, Any]] = []
        recommendations: List[str] = []
        reasoning: List[str] = []
        processed_images: set[str] = (
            set()
        )  # Track unique base image names (without tag initially)
        all_arch_specific_lines: Dict[str, List[str]] = {}  # {line: [file1, file2]}
        images_data: Dict[str, Dict[str, Any]] = (
            {}
        )  # { 'image_name': {'files': [], 'platforms_used': set(), manifest_info:{...} }}

        logger.info(
            f"Aggregating Docker analysis results from {len(analysis_outputs)} files."
        )

        # --- Pass 1: Collect data from all files ---
        for output in analysis_outputs:
            file_path = output.get("file", "unknown_file")

            # Collect Arch Specific Lines
            for line in output.get("arch_specific_lines", []):
                if line not in all_arch_specific_lines:
                    all_arch_specific_lines[line] = []
                if file_path not in all_arch_specific_lines[line]:
                    all_arch_specific_lines[line].append(file_path)

            # Collect Base Image Info
            for img_info in output.get("base_images_info", []):
                img_name = img_info.get("name")
                if not img_name:
                    continue

                # Normalize name for dictionary key (handle implicit latest)
                if (
                    ":" not in img_name
                    and "@" not in img_name
                    and img_name != "scratch"
                ):
                    dict_key = f"{img_name}:latest"
                else:
                    dict_key = img_name

                if dict_key not in images_data:
                    images_data[dict_key] = {
                        "files": set(),
                        "platforms_used": set(),
                        "manifest_info": None,
                    }

                images_data[dict_key]["files"].add(file_path)
                platform = img_info.get("platform_used")
                if platform:
                    images_data[dict_key]["platforms_used"].add(platform.lower())

        # --- Pass 2: Check Manifests for unique images ---
        for image_key in sorted(images_data.keys()):
            logger.debug(f"Performing manifest check for aggregated image: {image_key}")
            manifest_info = self._check_image_compatibility_via_manifest(image_key)
            images_data[image_key]["manifest_info"] = manifest_info

        # --- Pass 3: Generate Assessments and Recommendations ---
        overall_arm_potential = "High"  # Assume high unless blockers found

        for image_key, data in sorted(images_data.items()):
            manifest_info = data["manifest_info"]
            files_list = sorted(list(data["files"]))
            files_str = f"(used in: {', '.join(f'`{f}`' for f in files_list)})"
            platforms_used = data["platforms_used"]

            assessment = {
                "image": image_key,
                "files": files_list,
                "platforms_explicitly_used": sorted(list(platforms_used)),
                "arm64_support_native": (
                    manifest_info.get("compatible")
                    if manifest_info is not None
                    else "unknown"
                ),  # True, False, unknown
                "native_support_reason": (
                    manifest_info.get("reason")
                    if manifest_info is not None
                    else "Information unavailable"
                ),
                "native_architectures": (
                    manifest_info.get("details", {})
                    if manifest_info is not None
                    else {}
                ).get("architectures", []),
                "migration_potential": "Unknown",  # High, Medium, Low, Not Possible
                "required_actions": [],
            }

            comp_status = assessment["arm64_support_native"]
            reason = assessment["native_support_reason"] or "Information unavailable"

            # Determine Migration Potential and Actions
            if comp_status is True:
                assessment["migration_potential"] = "High"
                reasoning.append(
                    f"✅ Base image `{image_key}` natively supports ARM64 {files_str}."
                )
                if any(p == "linux/amd64" for p in platforms_used):
                    reasoning.append(
                        f"   * Note: It was used with `--platform=linux/amd64` which needs removal/change."
                    )
                    assessment["required_actions"].append(
                        "Remove or change `--platform=linux/amd64` flag in FROM lines."
                    )
                    recommendations.append(
                        f"Modify Dockerfile(s) for `{image_key}`: remove/change explicit `--platform=linux/amd64` {files_str}."
                    )
                    # Doesn't lower potential significantly, just requires Dockerfile edit
                else:
                    # Good sign, no explicit amd64 platform used
                    reasoning.append(
                        f"   * No explicit `--platform=linux/amd64` flag was detected for this image."
                    )

                # Check for other potential issues from arch_specific_lines later

            elif comp_status is False:
                assessment["migration_potential"] = "Not Possible / Very Difficult"
                reasoning.append(
                    f"❌ Base image `{image_key}` does *not* natively support ARM64 {files_str}. Reason: {reason}"
                )
                recommendations.append(
                    f"Major Blocker: Base image `{image_key}` is not ARM64 compatible. Replace it with a multi-arch or ARM64 variant {files_str}."
                )
                overall_arm_potential = "Low"  # Major blocker

            else:  # compatible is "unknown"
                assessment["migration_potential"] = "Unknown / Needs Verification"
                reasoning.append(
                    f"❓ Native ARM64 support for base image `{image_key}` is unknown {files_str}. Reason: {reason}"
                )
                recommendations.append(
                    f"Action Required: Manually verify ARM64 support for `{image_key}` {files_str} (e.g., check Docker Hub, docs, try building for arm64)."
                )
                # If unknown, we can't be sure about potential, keep overall potential cautious
                if overall_arm_potential == "High":
                    overall_arm_potential = "Medium"

            image_assessments.append(assessment)

        # --- Check Architecture Specific Lines ---
        hard_blockers_found = False
        review_items_found = False
        if all_arch_specific_lines:
            reasoning.append("---")
            reasoning.append(
                "ℹ️ Analysis of specific commands/lines across Dockerfiles:"
            )
            for line, files in sorted(all_arch_specific_lines.items()):
                files_str = f"(in {', '.join(f'`{f}`' for f in files)})"
                line_lower = line.lower()
                is_blocker = False
                is_review_item = True  # Default to needing review

                # Check for explicit x86 binary downloads/installs
                if (
                    re.search(
                        r"(wget|curl).*(amd64|x86_64).*\.(deb|rpm|bin|zip|tar\.gz)",
                        line_lower,
                    )
                    or re.search(r"dpkg --add-architecture (amd64|x86_64)", line_lower)
                    or re.search(
                        r"(apt-get|yum|dnf|apk)\s+install.*:(amd64|x86_64)", line_lower
                    )
                ):
                    reasoning.append(
                        f"   * ❌ Potential Blocker: Line explicitly fetches or installs x86-specific binary/package: `{line}` {files_str}"
                    )
                    recommendations.append(
                        f"Investigate/Modify: Replace x86-specific download/install with ARM64 equivalent or multi-arch method in line: `{line}` {files_str}"
                    )
                    is_blocker = True
                    hard_blockers_found = True
                elif re.search(r"(copy|add).*\.(so|a)\s+", line_lower):
                    reasoning.append(
                        f"   * ⚠️ Review Needed: Line copies native library (`.so`, `.a`). Ensure ARM64 version is available/built: `{line}` {files_str}"
                    )
                    recommendations.append(
                        f"Verify/Modify: Ensure ARM64 compatible library is copied or built for line: `{line}` {files_str}"
                    )
                    review_items_found = True
                elif re.search(r"(copy|add).*(amd64|x86_64)", line_lower):
                    reasoning.append(
                        f"   * ⚠️ Review Needed: Line copies file potentially named for x86. Check if ARM variant needed: `{line}` {files_str}"
                    )
                    recommendations.append(
                        f"Verify/Modify: Check if ARM variant needed for file copied in line: `{line}` {files_str}"
                    )
                    review_items_found = True
                elif (
                    "--platform=linux/amd64" in line_lower
                    and not line.strip().upper().startswith("FROM")
                ):  # Platform flag in other commands?
                    reasoning.append(
                        f"   * ⚠️ Review Needed: Line uses `--platform` flag outside FROM. Check context: `{line}` {files_str}"
                    )
                    recommendations.append(
                        f"Verify: Understand use of `--platform` in non-FROM line: `{line}` {files_str}"
                    )
                    review_items_found = True
                elif re.search(
                    r"\b(TARGETARCH|TARGETPLATFORM)\b", line
                ):  # Using build args is often good!
                    reasoning.append(
                        f"   * ✅ Info: Line uses multi-arch build arguments (TARGETARCH/TARGETPLATFORM). This is generally good for ARM compatibility: `{line}` {files_str}"
                    )
                    is_review_item = False  # Less concerning
                elif re.search(
                    r"\b(amd64|x86_64)\b", line_lower
                ):  # General keyword match (lower priority)
                    reasoning.append(
                        f"   * ⚠️ Review Needed: Line contains x86 keyword ('amd64'/'x86_64'). Review context: `{line}` {files_str}"
                    )
                    recommendations.append(
                        f"Verify: Review use of x86 keyword in line: `{line}` {files_str}"
                    )
                    review_items_found = True
                # Add more specific checks here if needed

            if hard_blockers_found:
                if overall_arm_potential != "Low":
                    overall_arm_potential = (
                        "Low"  # Hard blockers override previous assessment
                    )
            elif review_items_found:
                if overall_arm_potential == "High":
                    overall_arm_potential = "Medium"

        # --- Final Recommendation ---
        final_summary = f"Overall ARM Migration Potential: {overall_arm_potential}. "
        if overall_arm_potential == "High":
            final_summary += "Looks promising. Primarily requires Dockerfile adjustments (like removing --platform) and standard testing."
        elif overall_arm_potential == "Medium":
            final_summary += "Possible, but requires careful review of base image compatibility (if unknown) and specific Dockerfile commands. Thorough testing is crucial."
        elif overall_arm_potential == "Low":
            final_summary += "Significant challenges detected (incompatible base images or hard-coded x86 dependencies). Major refactoring or alternative solutions likely needed."
        else:  # Unknown base image was the main factor
            final_summary += (
                "Cannot determine potential without verifying base image compatibility."
            )

        # Prepend summary to recommendations
        final_recommendations = [final_summary] + sorted(list(set(recommendations)))

        logger.info(
            f"Finished aggregating Docker results. Overall potential: {overall_arm_potential}"
        )
        return {
            "results": image_assessments,
            "recommendations": final_recommendations,
            "reasoning": reasoning,
            "overall_potential": overall_arm_potential,  # Add a simple overall score/level
        }
