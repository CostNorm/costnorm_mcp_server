import re
import json
import logging
import requests
import zipfile
import io
import lzma
from typing import Optional, Dict, Any, List, Union

# Packaging library for version/specifier handling
from packaging.utils import canonicalize_name
from packaging.version import parse as parse_version, InvalidVersion
from packaging.specifiers import SpecifierSet, InvalidSpecifier

# Base class and config
from .base_checker import BaseDependencyChecker
from config import GITHUB_TOKEN  # Assumes config is importable

logger = logging.getLogger(__name__)

# --- Module-level Caches ---
# Cache for PyPI package information {cache_key: result}
_PYPI_CACHE: Dict[str, Dict[str, Any]] = {}
# Cache for Wheel Tester results {normalized_name: data}
_WHEEL_TESTER_CACHE: Optional[Dict[str, Any]] = None
_WHEEL_TESTER_CACHE_FETCHED = False

# --- GitHub API Helpers (for Wheel Tester) ---
_WHEEL_TESTER_OWNER = "geoffreyblake"
_WHEEL_TESTER_REPO = "arm64-python-wheel-tester"
_WHEEL_TESTER_WORKFLOW_ID = "wheel-test.yaml"
_WHEEL_TESTER_ARTIFACT_NAME_PATTERN = "results"


def _get_github_api_headers() -> Dict[str, str]:
    """GitHub API request headers for Wheel Tester artifact download."""
    if not GITHUB_TOKEN:
        # Log warning but don't raise error immediately, let the fetch attempt fail
        logger.warning(
            "GITHUB_TOKEN is not set. Wheel Tester results fetch will likely fail."
        )
        # Return headers without auth, might work for public data but likely insufficient
        return {"Accept": "application/vnd.github.v3+json"}
    return {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
    }


def _get_latest_wheel_tester_results() -> Optional[Dict[str, Any]]:
    """
    Fetches and parses the latest successful arm64-python-wheel-tester results artifact.
    Uses module-level caching.
    """
    global _WHEEL_TESTER_CACHE, _WHEEL_TESTER_CACHE_FETCHED
    if _WHEEL_TESTER_CACHE_FETCHED:
        logger.debug("Using cached wheel tester results (or cached failure).")
        return _WHEEL_TESTER_CACHE

    if not GITHUB_TOKEN:
        logger.error(
            "Cannot fetch Wheel Tester results: GITHUB_TOKEN is not configured."
        )
        _WHEEL_TESTER_CACHE_FETCHED = True  # Mark as fetched (failed)
        return None

    logger.info(
        f"Fetching latest wheel tester results from {_WHEEL_TESTER_OWNER}/{_WHEEL_TESTER_REPO}..."
    )
    api_base = f"https://api.github.com/repos/{_WHEEL_TESTER_OWNER}/{_WHEEL_TESTER_REPO}/actions"

    try:
        headers = _get_github_api_headers()

        # 1. Find the latest successful workflow run
        runs_url = f"{api_base}/workflows/{_WHEEL_TESTER_WORKFLOW_ID}/runs?status=success&per_page=5"
        logger.debug(f"Getting workflow runs: {runs_url}")
        runs_response = requests.get(runs_url, headers=headers, timeout=15)
        runs_response.raise_for_status()
        runs_data = runs_response.json()

        if (
            not runs_data
            or "workflow_runs" not in runs_data
            or not runs_data["workflow_runs"]
        ):
            logger.warning("No successful workflow runs found for Wheel Tester.")
            _WHEEL_TESTER_CACHE_FETCHED = True
            return None

        latest_run_id = runs_data["workflow_runs"][0]["id"]
        logger.info(f"Latest successful Wheel Tester run ID: {latest_run_id}")

        # 2. Get artifacts for that run
        artifacts_url = f"{api_base}/runs/{latest_run_id}/artifacts"
        logger.debug(f"Getting artifacts for run {latest_run_id}: {artifacts_url}")
        artifacts_response = requests.get(artifacts_url, headers=headers, timeout=15)
        artifacts_response.raise_for_status()
        artifacts_data = artifacts_response.json()

        if (
            not artifacts_data
            or "artifacts" not in artifacts_data
            or not artifacts_data["artifacts"]
        ):
            logger.warning(f"No artifacts found for Wheel Tester run {latest_run_id}.")
            _WHEEL_TESTER_CACHE_FETCHED = True
            return None

        # Find the results artifact
        target_artifact = None
        for artifact in artifacts_data["artifacts"]:
            if _WHEEL_TESTER_ARTIFACT_NAME_PATTERN in artifact["name"].lower():
                target_artifact = artifact
                break
        if not target_artifact:
            # Fallback to the first artifact if pattern not found (less reliable)
            target_artifact = artifacts_data["artifacts"][0]
            logger.warning(
                f"Wheel Tester artifact pattern not found, using first artifact: {target_artifact['name']}"
            )

        artifact_id = target_artifact["id"]
        artifact_name = target_artifact["name"]
        logger.info(
            f"Found Wheel Tester artifact: '{artifact_name}' (ID: {artifact_id})"
        )

        # 3. Download the artifact (zip)
        download_url = f"{api_base}/artifacts/{artifact_id}/zip"
        logger.info(f"Downloading artifact: {download_url}")
        download_response = requests.get(
            download_url, headers=headers, allow_redirects=True, timeout=60
        )
        download_response.raise_for_status()
        logger.info("Artifact download complete.")

        # 4. Extract and parse the .json.xz file
        logger.info("Extracting and parsing Wheel Tester results from zip...")
        with io.BytesIO(download_response.content) as zip_buffer:
            with zipfile.ZipFile(zip_buffer) as zf:
                result_filename = None
                for name in zf.namelist():
                    if name.endswith(".json.xz"):
                        result_filename = name
                        logger.info(f"Found results file in zip: {result_filename}")
                        break

                if not result_filename:
                    logger.error(
                        "Could not find .json.xz file within the Wheel Tester artifact zip."
                    )
                    _WHEEL_TESTER_CACHE_FETCHED = True
                    return None

                with zf.open(result_filename) as xz_file:
                    with lzma.open(xz_file, "rt", encoding="utf-8") as json_file:
                        parsed_results = json.load(json_file)
                        logger.info(
                            f"Successfully parsed Wheel Tester results from '{result_filename}'."
                        )
                        _WHEEL_TESTER_CACHE = parsed_results  # Store in cache
                        _WHEEL_TESTER_CACHE_FETCHED = True
                        return parsed_results

    except requests.exceptions.RequestException as e:
        logger.error(f"GitHub API request error fetching Wheel Tester results: {e}")
    except zipfile.BadZipFile:
        logger.error("Downloaded Wheel Tester artifact is not a valid Zip file.")
    except lzma.LZMAError as e:
        logger.error(f"XZ decompression error for Wheel Tester results: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error for Wheel Tester results: {e}")
    except KeyError as e:
        logger.error(
            f"Missing expected key in GitHub API response for Wheel Tester: {e}"
        )
    except Exception as e:
        logger.exception(f"Unexpected error fetching Wheel Tester results: {e}")

    _WHEEL_TESTER_CACHE_FETCHED = True  # Mark as fetched (failed) on any error
    return None


# --- PyPI API Helper ---
def _check_pypi_package_arm_compatibility(
    package_name: str, package_version_specifier: Optional[str] = None
) -> Dict[str, Any]:
    """
    Checks PyPI for ARM64 compatibility information (wheels, sdist).
    Uses module-level caching.
    """
    global _PYPI_CACHE
    try:
        normalized_name = canonicalize_name(package_name)
    except Exception as e:
        logger.error(f"Error canonicalizing package name '{package_name}': {e}")
        return {
            "compatible": "unknown",
            "reason": f"Invalid package name format: {package_name}",
            "checked_version": None,
        }

    cache_key = (
        f"{normalized_name}@{package_version_specifier}"
        if package_version_specifier
        else normalized_name
    )
    if cache_key in _PYPI_CACHE:
        logger.debug(f"Using cached PyPI result for {cache_key}")
        return _PYPI_CACHE[cache_key]

    logger.info(
        f"Checking PyPI compatibility for: {normalized_name}{'@' + package_version_specifier if package_version_specifier else ' (latest)'}"
    )

    try:
        url = f"https://pypi.org/pypi/{normalized_name}/json"
        response = requests.get(url, timeout=10)

        if response.status_code == 404:
            logger.warning(f"Package {normalized_name} not found on PyPI.")
            result = {
                "compatible": "unknown",
                "reason": f"Package '{normalized_name}' not found on PyPI.",
                "checked_version": None,
            }
            _PYPI_CACHE[cache_key] = result
            return result
        elif response.status_code != 200:
            logger.error(
                f"PyPI API error for {normalized_name}: HTTP {response.status_code} - {response.text}"
            )
            # Don't cache transient API errors
            return {
                "compatible": "unknown",
                "reason": f"PyPI API error: HTTP {response.status_code}",
                "checked_version": None,
            }

        data = response.json()
        available_versions_str = list(data.get("releases", {}).keys())
        if not available_versions_str:
            logger.warning(f"No releases found for {normalized_name} on PyPI.")
            result = {
                "compatible": "unknown",
                "reason": f"No releases found for '{normalized_name}' on PyPI.",
                "checked_version": None,
            }
            _PYPI_CACHE[cache_key] = result
            return result

        # Determine target version
        target_version_str: Optional[str] = None
        if package_version_specifier:
            try:
                specifier_set = SpecifierSet(package_version_specifier)
                candidate_versions = [
                    v
                    for v_str in available_versions_str
                    if (v := parse_version(v_str)) and not isinstance(v, InvalidVersion)
                ]
                allowed_versions = list(
                    specifier_set.filter(candidate_versions, prereleases=True)
                )  # Allow prereleases if spec allows
                if allowed_versions:
                    target_version_str = str(max(allowed_versions))
                    logger.info(
                        f"Found latest version satisfying '{package_version_specifier}': {target_version_str}"
                    )
                else:
                    logger.warning(
                        f"No version of {normalized_name} satisfies '{package_version_specifier}'. Available: {available_versions_str[-5:]}"
                    )
                    result = {
                        "compatible": "unknown",
                        "reason": f"No version found satisfying '{package_version_specifier}'.",
                        "checked_version": None,
                    }
                    _PYPI_CACHE[cache_key] = result
                    return result
            except InvalidSpecifier:
                logger.error(
                    f"Invalid version specifier '{package_version_specifier}' for {normalized_name}"
                )
                result = {
                    "compatible": "unknown",
                    "reason": f"Invalid version specifier: '{package_version_specifier}'",
                    "checked_version": None,
                }
                _PYPI_CACHE[cache_key] = result
                return result
            except InvalidVersion as e:
                logger.error(
                    f"Error parsing available versions for {normalized_name}: {e}"
                )
                # Proceed with latest if version parsing fails? Or return unknown?
                target_version_str = data.get("info", {}).get(
                    "version"
                )  # Fallback to latest reported
                if not target_version_str:
                    result = {
                        "compatible": "unknown",
                        "reason": "Could not determine target version due to parsing errors.",
                        "checked_version": None,
                    }
                    _PYPI_CACHE[cache_key] = result
                    return result
                logger.warning(
                    f"Version parsing issues, falling back to latest reported: {target_version_str}"
                )

        else:
            target_version_str = data.get("info", {}).get("version")
            if not target_version_str:
                logger.error(
                    f"Could not determine latest version for {normalized_name}"
                )
                return {
                    "compatible": "unknown",
                    "reason": "Could not determine latest version from PyPI info.",
                    "checked_version": None,
                }
            logger.info(f"Using latest reported PyPI version: {target_version_str}")

        # Analyze release files for the target version
        if target_version_str not in data.get("releases", {}):
            logger.error(
                f"Target version {target_version_str} details missing in PyPI response for {normalized_name}"
            )
            return {
                "compatible": "unknown",
                "reason": f"Internal error: Target version {target_version_str} details missing.",
                "checked_version": target_version_str,
            }

        release_files = data["releases"].get(target_version_str, [])
        info_for_version = data.get("info", {})  # Use main info for classifiers etc.

        # Check for yanked status (more robust check)
        yanked = False
        yanked_reason = "No reason provided"
        release_info_list = data.get("releases", {}).get(target_version_str, [])
        if release_info_list:  # Check the list of files for yanked status
            first_file_info = release_info_list[0]
            yanked = first_file_info.get("yanked", False)
            if yanked:
                yanked_reason = first_file_info.get("yanked_reason") or yanked_reason
        if yanked:
            logger.warning(
                f"Version {target_version_str} of {normalized_name} is yanked: {yanked_reason}"
            )

        # Check for compatible wheels or sdist (only non-yanked files)
        arm_wheels = []
        universal_wheels = []
        sdist_files = []
        other_arch_wheels = []

        for release in release_files:
            if release.get("yanked", False):
                continue  # Skip yanked files

            filename = release.get("filename", "")
            packagetype = release.get("packagetype", "")

            if packagetype == "bdist_wheel":
                wheel_tags_match = re.search(r"-([^-]+-[^-]+-[^-]+)\.whl$", filename)
                if wheel_tags_match:
                    wheel_tags = wheel_tags_match.group(1).lower()
                    # Check ARM tags
                    if any(arm_id in wheel_tags for arm_id in ["aarch64", "arm64"]):
                        arm_wheels.append(filename)
                    # Check for universal2 wheels on macOS
                    elif "universal2" in wheel_tags and "macosx" in wheel_tags:
                        universal_wheels.append(filename)
                    # Check for truly universal wheels marked as 'any'
                    elif "any" in wheel_tags and not any(
                        arch in wheel_tags
                        for arch in ["win", "linux", "macosx", "x86_64", "amd64"]
                    ):
                        universal_wheels.append(filename)
                    # Check common x86 tags
                    elif any(
                        x86_id in wheel_tags
                        for x86_id in [
                            "win_amd64",
                            "amd64",
                            "x86_64",
                            "x64",
                            "win32",
                            "i686",
                        ]
                    ):
                        other_arch_wheels.append(filename)
            elif packagetype == "sdist":
                sdist_files.append(filename)

        # Determine compatibility based on non-yanked files
        final_result = {}
        if arm_wheels:
            final_result = {
                "compatible": True,
                "reason": f"ARM-specific wheels found for version {target_version_str}.",
            }
        elif universal_wheels:
            # Pure python wheels ('any') or mac universal wheels
            final_result = {
                "compatible": True,
                "reason": f"Platform-agnostic or universal wheels found for version {target_version_str}.",
            }
        elif sdist_files:
            # Check sdist for potential compilation needs
            classifiers = info_for_version.get("classifiers", [])
            has_native_code = (
                any("Programming Language :: C" in c for c in classifiers)
                or any("Programming Language :: C++" in c for c in classifiers)
                or any("Programming Language :: Cython" in c for c in classifiers)
            )
            platform_info = info_for_version.get("platform")
            is_platform_specific = platform_info not in [None, "", "any"]

            if has_native_code or is_platform_specific:
                final_result = {
                    "compatible": "partial",
                    "reason": f"Source distribution found for {target_version_str}, may require compilation on ARM64 (contains C/C++/Cython or platform markers).",
                }
            else:
                final_result = {
                    "compatible": True,
                    "reason": f"Likely pure Python source distribution found for {target_version_str}.",
                }
        elif other_arch_wheels:
            final_result = {
                "compatible": False,
                "reason": f"Only non-ARM wheels (e.g., x86_64) found for non-yanked files of version {target_version_str}.",
            }
        else:
            # No non-yanked wheels or sdist found
            final_result = {
                "compatible": (
                    "unknown" if not yanked else "unknown"
                ),  # If only yanked files existed, still unknown
                "reason": f"No non-yanked wheels or source distribution found for version {target_version_str} on PyPI.",
            }

        # Add yanked warning if applicable
        if yanked:
            final_result["warning"] = (
                f"Version {target_version_str} is yanked: {yanked_reason}"
            )

        final_result["checked_version"] = target_version_str
        _PYPI_CACHE[cache_key] = final_result
        logger.debug(f"PyPI check result for {cache_key}: {final_result}")
        return final_result

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error checking PyPI for {normalized_name}: {e}")
        return {
            "compatible": "unknown",
            "reason": f"Network error checking PyPI: {e}",
            "checked_version": None,
        }
    except Exception as e:
        logger.exception(
            f"Unexpected error checking PyPI for {normalized_name}@{package_version_specifier or 'latest'}: {e}"
        )
        return {
            "compatible": "unknown",
            "reason": f"Unexpected error during PyPI check: {e}",
            "checked_version": None,
        }


# --- Python Dependency Checker Class ---
class PythonDependencyChecker(BaseDependencyChecker):
    """Checks Python dependencies (from requirements.txt) for ARM64 compatibility."""

    def parse_dependencies(
        self, file_content: str, file_path: str
    ) -> List[Dict[str, Any]]:
        """
        Parses dependencies from requirements.txt content.

        Args:
            file_content: Content of the requirements.txt file.
            file_path: Path to the requirements.txt file.

        Returns:
            List of dictionaries, each representing a dependency.
            Example: [{'name': 'requests', 'version_spec': '>=2.0', 'original_line': 'requests>=2.0', 'file': 'reqs.txt'}]
        """
        parsed_deps = []
        logger.debug(f"Parsing dependencies from: {file_path}")
        line_num = 0
        for line in file_content.splitlines():
            line_num += 1
            line = line.strip()
            # Remove comments
            if "#" in line:
                line = line.split("#", 1)[0].strip()

            if not line:
                continue  # Skip empty lines and comments

            # Basic parsing of name and optional version specifier
            # Handles common specifiers: ==, >=, <=, ~=, <, > but not complex URLs or editable installs yet
            match = re.match(
                r"^([A-Za-z0-9_.-]+)(\[[A-Za-z0-9,_.-]+\])?\s*([=<>!~].+)?$", line
            )
            if match:
                package_name = match.group(1)
                version_spec = match.group(2).strip() if match.group(2) else None
                parsed_deps.append(
                    {
                        "name": package_name,
                        "version_spec": version_spec,
                        "original_line": line,  # Keep original line for context/reporting
                        "file": file_path,
                    }
                )
            else:
                # Could be a URL, editable install (-e), or invalid line
                logger.warning(
                    f"Could not parse line {line_num} in {file_path}: '{line}'. Skipping."
                )
                # Optionally add an 'unknown' entry or just skip
                parsed_deps.append(
                    {
                        "name": line,  # Use the whole line as name for unparsed
                        "version_spec": None,
                        "original_line": line,
                        "file": file_path,
                        "parse_error": True,
                    }
                )

        logger.info(
            f"Parsed {len(parsed_deps)} potential dependencies from {file_path}."
        )
        return parsed_deps

    def check_compatibility(self, dependency_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks the ARM compatibility of a single Python dependency using PyPI and Wheel Tester data.

        Args:
            dependency_info: Dictionary containing 'name', 'version_spec', 'original_line', 'file'.

        Returns:
            Dictionary with compatibility status ('compatible', 'reason', etc.).
        """
        package_name = dependency_info.get("name")
        version_spec = dependency_info.get("version_spec")
        original_line = dependency_info.get(
            "original_line", package_name
        )  # Fallback for display

        if dependency_info.get("parse_error"):
            logger.warning(
                f"Skipping compatibility check for unparsed line: {original_line}"
            )
            return {
                **dependency_info,  # Keep original info
                "compatible": "unknown",
                "reason": "Could not parse line in requirements file.",
                "debug_info": None,
            }

        logger.debug(
            f"Checking compatibility for Python package: {package_name} {version_spec or ''}"
        )

        # Use canonical name for Wheel Tester lookup, original name for PyPI API call
        try:
            normalized_name_for_tester = canonicalize_name(package_name)
        except Exception:
            normalized_name_for_tester = package_name.lower().replace(
                "_", "-"
            )  # Best effort fallback

        debug_info = {"pypi_check": None, "wheel_tester_check": None}
        final_compatibility: Union[bool, str] = "unknown"
        final_reason = "Compatibility status could not be determined."
        pypi_warning = None

        # --- 1. PyPI Check ---
        try:
            pypi_result = _check_pypi_package_arm_compatibility(
                package_name, version_spec
            )
            debug_info["pypi_check"] = pypi_result
            logger.debug(f"[{package_name}] PyPI Check Result: {pypi_result}")
            pypi_warning = pypi_result.get("warning")

            pypi_compat_status = pypi_result.get("compatible")
            if pypi_compat_status is True:
                final_compatibility = True
                final_reason = pypi_result.get(
                    "reason", "Compatible according to PyPI."
                )
            elif pypi_compat_status is False:
                final_compatibility = False
                final_reason = pypi_result.get(
                    "reason", "Incompatible according to PyPI."
                )
            elif pypi_compat_status == "partial":
                final_compatibility = "partial"
                final_reason = pypi_result.get(
                    "reason", "Partially compatible or requires build (PyPI)."
                )
            else:  # unknown
                final_compatibility = "unknown"
                final_reason = pypi_result.get(
                    "reason", "Compatibility unknown (PyPI)."
                )

        except Exception as e:
            logger.error(
                f"[{package_name}] PyPI check failed unexpectedly: {e}", exc_info=True
            )
            debug_info["pypi_check"] = {"error": str(e)}
            # Continue to Wheel Tester even if PyPI check fails

        # --- 2. Arm64 Wheel Tester Check ---
        wheel_tester_results_data = _get_latest_wheel_tester_results()
        if wheel_tester_results_data:
            if normalized_name_for_tester in wheel_tester_results_data:
                package_test_info = wheel_tester_results_data[
                    normalized_name_for_tester
                ]
                debug_info["wheel_tester_check"] = {
                    "status": "found",
                    "tests": list(package_test_info.keys()),
                }
                logger.debug(
                    f"[{normalized_name_for_tester}] Found in Wheel Tester results."
                )

                passed_on_recent_ubuntu = False
                failed_envs = []
                # Check recent Ubuntu versions first
                for test_env in ["noble", "jammy", "focal"]:  # Order matters
                    if test_env in package_test_info:
                        test_result = package_test_info[test_env]
                        if test_result.get("test-passed") is True:
                            passed_on_recent_ubuntu = True
                            # Wheel tester pass overrides PyPI (unless PyPI was False?) - Let's prioritize pass
                            final_compatibility = True
                            final_reason = (
                                f"Passed tests on {test_env} in Wheel Tester."
                            )
                            if test_result.get("build-required") is True:
                                final_reason += " (Build was required)."
                            logger.info(
                                f"[{normalized_name_for_tester}] Confirmed compatible via Wheel Tester ({test_env})."
                            )
                            break  # Stop checking environments if passed
                        else:
                            # Record failure, but continue checking other envs in case one passed
                            failed_envs.append(test_env)

                # If it didn't pass on any recent Ubuntu, and PyPI didn't say True/Partial, mark as False based on tester failure
                if not passed_on_recent_ubuntu and failed_envs:
                    failed_env_str = ", ".join(failed_envs)
                    if final_compatibility not in [True, "partial"]:
                        final_compatibility = False
                        final_reason = (
                            f"Failed tests on {failed_env_str} in Wheel Tester."
                        )
                        logger.warning(
                            f"[{normalized_name_for_tester}] Marked incompatible due to Wheel Tester failures ({failed_env_str})."
                        )
                    elif final_compatibility == "partial":
                        final_reason += f" Additionally, failed tests on {failed_env_str} in Wheel Tester."
                        logger.warning(
                            f"[{normalized_name_for_tester}] PyPI partial, but failed Wheel Tester ({failed_env_str})."
                        )
                    # If PyPI said False, we can add the tester failure info
                    elif final_compatibility is False:
                        final_reason += (
                            f" Also failed tests on {failed_env_str} in Wheel Tester."
                        )

            else:  # Not found in Wheel Tester results
                logger.debug(
                    f"[{normalized_name_for_tester}] Not found in Wheel Tester results."
                )
                debug_info["wheel_tester_check"] = {"status": "not_found"}
                # PyPI result remains the primary indicator
        else:  # Failed to fetch Wheel Tester results
            logger.warning(
                f"[{normalized_name_for_tester}] Could not fetch Wheel Tester results."
            )
            debug_info["wheel_tester_check"] = {"status": "fetch_error"}
            # PyPI result remains the primary indicator

        # --- Final Result Consolidation ---
        if final_compatibility == "partial":
            final_reason = (
                final_reason.rstrip(".")
                + ". Source compilation might be required on ARM64."
            )
        elif final_compatibility == "unknown":
            # Refine unknown reason if possible
            if (
                debug_info["pypi_check"]
                and debug_info["pypi_check"].get("compatible") == "unknown"
                and debug_info["wheel_tester_check"]
                and debug_info["wheel_tester_check"].get("status") != "found"
            ):
                final_reason = f"Could not determine compatibility from PyPI or Wheel Tester ({final_reason}). Manual check recommended."
            # else: keep the more specific reason from PyPI/Tester failure

        # Add PyPI warning (e.g., yanked) to the final reason
        if pypi_warning:
            final_reason = f"{final_reason.rstrip('.')} (Warning: {pypi_warning})"

        # Return the combined result, including original info and debug data
        return {
            **dependency_info,  # Include name, version_spec, file, original_line
            "compatible": final_compatibility,
            "reason": final_reason,
            "debug_info": debug_info,  # Include detailed check results for potential debugging
        }
