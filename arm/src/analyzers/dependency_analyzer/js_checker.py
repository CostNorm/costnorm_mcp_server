import json
import logging
import requests
import semver  # Added for version resolution
from typing import Dict, List, Any, Optional

from .base_checker import BaseDependencyChecker

logger = logging.getLogger(__name__)

# --- Module-level Cache ---
# Cache for NPM package information {cache_key: result}
_NPM_CACHE: Dict[str, Dict[str, Any]] = {}

# --- Known Package Lists (Module Level) ---
# Known problematic packages for ARM64 (often contain native code)
_PROBLEMATIC_PACKAGES = [
    "node-sass",
    "sharp",
    "canvas",
    "grpc",
    "electron",
    "node-gyp",
    "robotjs",
    "sqlite3",
    "bcrypt",
    "cpu-features",
    "node-expat",
    "dtrace-provider",
    "epoll",
    "fsevents",
    "libxmljs",
    "leveldown",
    # Add others as identified
]

# Packages generally known to be pure JavaScript and compatible
_KNOWN_COMPATIBLE_JS = [
    "react",
    "react-dom",
    "lodash",
    "axios",
    "express",
    "moment",
    "chalk",
    "commander",
    "dotenv",
    "uuid",
    "cors",
    "typescript",
    "jest",
    "mocha",
    "eslint",
    "prettier",
    "babel",
    "webpack",
    "rollup",
    "vite",
    "next",
    "vue",
    "angular",
    "jquery",
    "redux",
    "react-router-dom",
    "classnames",
    # Add others as needed
]


class JSDependencyChecker(BaseDependencyChecker):
    """Checks JavaScript dependencies (from package.json) for ARM64 compatibility."""

    def parse_dependencies(
        self, file_content: str, file_path: str
    ) -> List[Dict[str, Any]]:
        """
        Parses dependencies from package.json content.

        Args:
            file_content: Content of the package.json file.
            file_path: Path to the package.json file.

        Returns:
            List of dictionaries, each representing a dependency.
            Example: [{'name': 'react', 'version_spec': '^18.0.0', 'dev_dependency': False, 'file': 'package.json'}]
        """
        parsed_deps = []
        logger.debug(f"Parsing dependencies from: {file_path}")
        try:
            package_data = json.loads(file_content)
            dependencies = package_data.get("dependencies", {})
            dev_dependencies = package_data.get("devDependencies", {})

            for name, version_spec in dependencies.items():
                parsed_deps.append(
                    {
                        "name": name,
                        "version_spec": version_spec,  # Keep original specifier
                        "dev_dependency": False,
                        "file": file_path,
                    }
                )

            for name, version_spec in dev_dependencies.items():
                parsed_deps.append(
                    {
                        "name": name,
                        "version_spec": version_spec,
                        "dev_dependency": True,
                        "file": file_path,
                    }
                )

            logger.info(f"Parsed {len(parsed_deps)} dependencies from {file_path}.")

        except json.JSONDecodeError:
            logger.error(
                f"Invalid JSON format in {file_path}. Cannot parse dependencies."
            )
            # Return an empty list or a special marker? Empty list is safer.
            return []
        except Exception as e:
            logger.error(f"Error parsing {file_path}: {e}", exc_info=True)
            return []

        return parsed_deps

    def check_compatibility(self, dependency_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Checks the ARM compatibility of a single JS dependency using version resolution
        and NPM registry info.

        Args:
            dependency_info: Dictionary containing 'name', 'version_spec', 'dev_dependency', 'file'.

        Returns:
            Dictionary with compatibility status ('compatible', 'reason', etc.),
            including 'checked_version' and 'spec_satisfied'.
        """
        package_name = dependency_info.get("name")
        version_spec = dependency_info.get("version_spec", "")  # Keep original spec

        logger.debug(
            f"Checking compatibility for JS package: {package_name} (Spec: '{version_spec or 'None'}')"
        )

        # Call the internal check function with the original version_spec
        compatibility_result = self._check_npm_package_compatibility(
            package_name, version_spec  # Pass original version_spec
        )

        # Combine results
        return {
            **dependency_info,  # Include name, version_spec, file, dev_dependency
            "compatible": compatibility_result.get("compatible", "unknown"),
            "reason": compatibility_result.get("reason", "Unknown"),
            "dependency": f"{package_name}@{version_spec}",  # Use original spec for display
            "checked_version": compatibility_result.get(
                "checked_version"
            ),  # Add checked version
            "spec_satisfied": compatibility_result.get(
                "spec_satisfied"
            ),  # Add satisfaction flag
            "debug_info": compatibility_result.get("debug_info"),
        }

    def _check_npm_package_compatibility(
        self, package_name: str, version_spec: str  # Changed parameter
    ) -> Dict[str, Any]:
        """
        Internal helper to check if an NPM package is compatible with ARM64.
        Resolves version specifier, fetches specific version data from NPM registry,
        and performs checks. Caches results based on plan (Phase 1.2).

        Args:
            package_name: Name of the npm package.
            version_spec: The version specifier string (e.g., "^1.2.3", "latest", ">=2.0").

        Returns:
            dict: Compatibility information including 'compatible', 'reason',
                  'checked_version', 'spec_satisfied'.
        """
        global _NPM_CACHE
        # Initial cache check based on spec - might hit if previous check failed/fell back
        # Fallback cache key format: f"{package_name}@{version_spec}"
        fallback_cache_key = f"{package_name}@{version_spec}"
        if fallback_cache_key in _NPM_CACHE:
            # If the cached result for the spec indicates it was a fallback or error, return it
            cached_data = _NPM_CACHE[fallback_cache_key]
            # Check if spec_satisfied is explicitly False (fallback) or compatible is 'error'
            if (
                cached_data.get("spec_satisfied") is False
                or cached_data.get("compatible") == "error"
            ):
                logger.debug(
                    f"Using cached fallback/error result for spec {fallback_cache_key}"
                )
                return cached_data

        # Note: A successful cache hit for a *resolved* version will be checked later

        logger.info(
            f"Checking NPM compatibility for {package_name} (Spec: '{version_spec}')"
        )
        result: Dict[str, Any] = {
            "compatible": "unknown",
            "reason": "Initial state",
            "checked_version": None,
            "spec_satisfied": None,  # Will be True, False, or None if error before resolution
        }
        debug_info = {
            "source": "npm_registry",
            "details": {},
        }  # Default to registry check, init details dict
        target_version_str: Optional[str] = None
        version_metadata: Optional[Dict[str, Any]] = None
        is_fallback = False

        try:
            # 1. Fetch Full Package Data
            url_pkg_name = package_name.replace(
                "/", "%2F"
            )  # Basic encoding for scoped packages
            url = f"https://registry.npmjs.org/{url_pkg_name}"
            response = requests.get(url, timeout=15)  # Use timeout from plan
            debug_info["details"]["url"] = url
            debug_info["details"]["status_code"] = response.status_code

            if response.status_code == 404:
                result.update(
                    {
                        "compatible": "unknown",
                        "reason": f"Package '{package_name}' not found on NPM registry.",
                        "spec_satisfied": None,  # Cannot satisfy spec if package doesn't exist
                    }
                )
                debug_info["source"] = "npm_registry_error"
                _NPM_CACHE[fallback_cache_key] = {
                    **result,
                    "debug_info": debug_info,
                }  # Cache failure
                return _NPM_CACHE[fallback_cache_key]

            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            data = response.json()

            # 2. Resolve Version Specifier
            versions_dict = data.get("versions", {})
            if not versions_dict:
                result.update(
                    {
                        "compatible": "unknown",
                        "reason": f"No version information found for package '{package_name}' on NPM registry.",
                        "spec_satisfied": None,
                    }
                )
                debug_info["source"] = "npm_registry_no_versions"
                _NPM_CACHE[fallback_cache_key] = {
                    **result,
                    "debug_info": debug_info,
                }  # Cache failure
                return _NPM_CACHE[fallback_cache_key]

            available_versions = list(versions_dict.keys())
            latest_version_str = data.get("dist-tags", {}).get(
                "latest"
            )  # Needed for fallback

            try:
                # Handle empty or invalid specs gracefully before calling semver
                # Treat "" or "*" or "latest" as requesting the latest version
                if not version_spec or version_spec == "*" or version_spec == "latest":
                    if latest_version_str:
                        target_version_str = latest_version_str
                        logger.debug(
                            f"Version spec ('{version_spec}') implies latest: {target_version_str}"
                        )
                        # If spec was explicitly 'latest', it's satisfied. If empty or '*', it's a form of fallback/default.
                        is_fallback = not (version_spec == "latest")
                        result["spec_satisfied"] = not is_fallback
                    else:
                        raise ValueError(
                            "Cannot determine latest version for fallback/default."
                        )
                else:
                    # Attempt to resolve the spec using semver
                    target_version_str = max_satisfying(
                        available_versions,
                        version_spec,
                        loose=True,  # Use loose=True as per plan
                    )
                    if target_version_str is None:
                        logger.warning(
                            f"No version found for {package_name} satisfying spec '{version_spec}'. Falling back to latest."
                        )
                        if latest_version_str:
                            target_version_str = latest_version_str
                            is_fallback = True
                            result["spec_satisfied"] = False
                            # Add fallback reason
                            result["reason"] = (
                                f"No version satisfied spec '{version_spec}', fell back to latest ({target_version_str})."
                            )
                        else:
                            # No match and no latest? Very unlikely, but possible.
                            raise ValueError(
                                f"No version satisfies '{version_spec}' and no 'latest' tag found."
                            )
                    else:
                        logger.info(
                            f"Resolved spec '{version_spec}' to version: {target_version_str} for {package_name}"
                        )
                        is_fallback = False
                        result["spec_satisfied"] = True
                        result["reason"] = (
                            f"Resolved spec '{version_spec}' to version {target_version_str}."  # Initial reason
                        )

            except ValueError as e:
                # Handle invalid version spec or failure to find any version
                logger.error(
                    f"Error resolving version spec '{version_spec}' for {package_name}: {e}"
                )
                result.update(
                    {
                        "compatible": "unknown",
                        "reason": f"Invalid version spec '{version_spec}' or unable to resolve: {e}",
                        "spec_satisfied": None,  # Cannot satisfy if spec is invalid
                    }
                )
                debug_info["source"] = "version_resolution_error"
                _NPM_CACHE[fallback_cache_key] = {
                    **result,
                    "debug_info": debug_info,
                }  # Cache error under spec key
                return _NPM_CACHE[fallback_cache_key]

            result["checked_version"] = target_version_str
            debug_info["details"]["resolved_version"] = target_version_str
            debug_info["details"]["is_fallback"] = is_fallback

            # 3. Check Cache for Resolved Version (Success Cache Key)
            # Success cache key format: f"{package_name}@{resolved_version}"
            success_cache_key = f"{package_name}@{target_version_str}"
            if success_cache_key in _NPM_CACHE:
                logger.debug(
                    f"Using cached result for resolved version {success_cache_key}"
                )
                # Return cached data, ensuring spec_satisfied reflects the *current* check's outcome
                cached_result = _NPM_CACHE[success_cache_key].copy()
                cached_result["spec_satisfied"] = (
                    not is_fallback
                )  # Update based on current resolution
                # Update reason if current check was a fallback but cached wasn't (or vice versa)
                if is_fallback and cached_result.get("spec_satisfied") is not False:
                    cached_result["reason"] = (
                        f"Using cached result for {target_version_str}, but note: current spec '{version_spec}' required fallback. Original reason: {cached_result.get('reason')}"
                    )
                elif not is_fallback and cached_result.get("spec_satisfied") is False:
                    cached_result["reason"] = (
                        f"Using cached result for {target_version_str}, resolved from spec '{version_spec}'. Original reason: {cached_result.get('reason')}"
                    )

                return cached_result

            # 4. Get Specific Version Metadata
            version_metadata = versions_dict.get(target_version_str)
            if not version_metadata:
                # This should ideally not happen if target_version_str was derived correctly
                logger.error(
                    f"Could not find metadata for resolved version {target_version_str} in registry data for {package_name}."
                )
                result.update(
                    {
                        "compatible": "unknown",
                        "reason": f"Internal error: Metadata missing for resolved version {target_version_str}.",
                        # spec_satisfied already set based on resolution outcome
                    }
                )
                debug_info["source"] = "metadata_fetch_error"
                # Cache this specific error state under the *resolved* version key
                _NPM_CACHE[success_cache_key] = {**result, "debug_info": debug_info}
                return _NPM_CACHE[success_cache_key]

            # 5. Perform Compatibility Checks (using version_metadata)
            #    -> This part will be significantly updated in Phase 2, Task 2.
            #    -> For now, keep the *structure* of the old checks but point them to version_metadata.
            #    -> The logic here is TEMPORARY placeholder until the next step.

            debug_info["source"] = "npm_registry_metadata_check"
            debug_info["details"]["checked_version_metadata_keys"] = list(
                version_metadata.keys()
            )  # Log available keys

            # --- Enhanced Metadata Checks (Phase 2, Task 2) ---
            current_compatible_status = (
                True  # Start assuming compatible unless proven otherwise
            )
            reasons = []
            if result.get("reason") and result["reason"] != "Initial state":
                # Keep reason from resolution step (e.g., fallback)
                reasons.append(result["reason"])

            debug_info["details"]["indicators"] = []  # Store list of indicators found

            # 5a. Check CPU field
            cpu_field = version_metadata.get("cpu", [])
            if isinstance(
                cpu_field, str
            ):  # Handle case where it's a string instead of list
                cpu_field = [cpu_field]
            if cpu_field:
                debug_info["details"]["cpu_field"] = cpu_field
                # Check for explicit exclusion of ARM
                is_arm_allowed = any(
                    arch in cpu_field for arch in ["arm", "arm64", "any"]
                )
                is_only_non_arm = all(
                    arch in ["x64", "ia32"] for arch in cpu_field
                )  # Example non-ARM arches
                is_negated_arm_exclusion = any(
                    arch.startswith("!") and arch[1:] in ["arm", "arm64"]
                    for arch in cpu_field
                )
                is_negated_other_inclusion = any(
                    arch.startswith("!") and arch[1:] not in ["arm", "arm64"]
                    for arch in cpu_field
                )  # e.g. !x64

                if is_negated_arm_exclusion:
                    current_compatible_status = False
                    reason = f"CPU field explicitly excludes ARM ('{cpu_field}')"
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("cpu_exclusion")
                elif cpu_field and not is_arm_allowed and is_only_non_arm:
                    # Only contains specific non-ARM architectures
                    current_compatible_status = False
                    reason = (
                        f"CPU field only lists non-ARM architectures ('{cpu_field}')"
                    )
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("cpu_non_arm_only")
                elif (
                    cpu_field
                    and not is_arm_allowed
                    and not is_negated_other_inclusion
                    and "any" not in cpu_field
                ):
                    # Contains architectures, none are ARM, not 'any', and no negations like '!x64'
                    # Treat as potentially problematic / partial match
                    if (
                        current_compatible_status is not False
                    ):  # Don't downgrade from False
                        current_compatible_status = "partial"
                    reason = f"CPU field ('{cpu_field}') does not explicitly mention ARM support"
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("cpu_no_arm_mention")
                elif "arm" in cpu_field and "arm64" not in cpu_field:
                    # Explicitly mentions 32-bit arm but not 64-bit
                    if current_compatible_status is not False:
                        current_compatible_status = "partial"
                    reason = f"CPU field mentions 'arm' but not 'arm64' ('{cpu_field}')"
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("cpu_arm32_only")

            # 5b. Check OS field
            os_field = version_metadata.get("os", [])
            if isinstance(os_field, str):  # Handle string case
                os_field = [os_field]
            if os_field:
                debug_info["details"]["os_field"] = os_field
                # Check for explicit exclusion of Linux
                is_linux_excluded = "!linux" in os_field
                is_only_non_linux = all(
                    platform in ["win32", "darwin", "freebsd"] for platform in os_field
                )  # Example non-Linux

                if is_linux_excluded:
                    current_compatible_status = False
                    reason = f"OS field explicitly excludes Linux ('{os_field}')"
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("os_linux_exclusion")
                elif (
                    os_field
                    and not any(
                        p in os_field for p in ["linux", "any", "!win32", "!darwin"]
                    )
                    and is_only_non_linux
                ):
                    # Only contains specific non-Linux OS and doesn't allow 'any' or negate others
                    current_compatible_status = False
                    reason = f"OS field only lists non-Linux platforms ('{os_field}')"
                    reasons.append(reason)
                    debug_info["details"]["indicators"].append("os_non_linux_only")

            # 5c. Check 'binary' field
            binary_field = version_metadata.get("binary")
            if binary_field:
                debug_info["details"]["binary_field"] = binary_field
                if current_compatible_status is not False:  # Don't downgrade from False
                    current_compatible_status = "partial"
                reason = (
                    "Contains 'binary' field, may download pre-compiled native code"
                )
                reasons.append(reason)
                debug_info["details"]["indicators"].append("binary_field")

            # 5d. Check 'scripts' for build steps
            scripts = version_metadata.get("scripts", {})
            gypfile = version_metadata.get(
                "gypfile", False
            )  # Also check gypfile presence
            install_scripts_content = " ".join(
                [
                    scripts.get("install", ""),
                    scripts.get("preinstall", ""),
                    scripts.get("postinstall", ""),
                ]
            ).lower()  # Check combined install scripts

            if (
                gypfile
                or "node-gyp" in install_scripts_content
                or "node-pre-gyp" in install_scripts_content
            ):
                debug_info["details"][
                    "scripts_field"
                ] = scripts  # Log scripts if relevant
                if current_compatible_status is not False:  # Don't downgrade from False
                    current_compatible_status = "partial"
                reason = "Uses node-gyp/node-pre-gyp or has gypfile, likely involves native compilation"
                reasons.append(reason)
                debug_info["details"]["indicators"].append("build_script_native")

            # 5e. Consolidate Results
            final_reason = "; ".join(
                sorted(list(set(reasons)))
            )  # Combine unique reasons
            if not final_reason and current_compatible_status is True:
                final_reason = f"Package version {target_version_str} appears compatible based on metadata analysis."

            result["compatible"] = current_compatible_status
            result["reason"] = final_reason
            # --- End Enhanced Metadata Checks ---

            # 6. Cache Final Result
            result["debug_info"] = debug_info
            # Cache under the *resolved version* key (success key)
            _NPM_CACHE[success_cache_key] = result.copy()  # Store a copy

            # If the resolution was a fallback, also cache the result under the *original spec* key (fallback key)
            # This prevents re-fetching and re-resolving the same failing spec repeatedly during the same run
            if is_fallback:
                _NPM_CACHE[fallback_cache_key] = result.copy()  # Store a copy

            logger.debug(
                f"NPM check result for {package_name}@{target_version_str} (Spec: '{version_spec}', Fallback: {is_fallback}): {result['compatible']} - {result['reason']}"
            )
            return result

        except requests.exceptions.RequestException as req_err:
            logger.warning(
                f"Network error checking NPM for {package_name} spec '{version_spec}': {req_err}"
            )
            result.update(
                {
                    "compatible": "unknown",
                    "reason": f"Network error checking NPM: {req_err}",
                    "spec_satisfied": None,  # Cannot determine satisfaction due to network error
                }
            )
            debug_info["source"] = "network_error"
            debug_info["details"] = {"error": str(req_err)}
            # Cache the network error under the spec key to avoid retries within the same run
            _NPM_CACHE[fallback_cache_key] = {**result, "debug_info": debug_info}
            return _NPM_CACHE[fallback_cache_key]
        except json.JSONDecodeError as json_err:
            # This might happen if the registry returns invalid JSON
            logger.warning(
                f"Failed to parse NPM registry response for {package_name}: {json_err}"
            )
            result.update(
                {
                    "compatible": "unknown",
                    "reason": "Failed to parse NPM registry response.",
                    "spec_satisfied": None,
                }
            )
            debug_info["source"] = "json_decode_error"
            debug_info["details"] = {"error": str(json_err)}
            _NPM_CACHE[fallback_cache_key] = {**result, "debug_info": debug_info}
            return _NPM_CACHE[fallback_cache_key]
        except Exception as e:
            logger.error(
                f"Unexpected error checking JS compatibility for {package_name} spec '{version_spec}': {e}",
                exc_info=True,
            )
            result.update(
                {
                    "compatible": "unknown",
                    "reason": f"Unexpected error during JS compatibility check: {e}",
                    "spec_satisfied": None,
                }
            )
            debug_info["source"] = "unexpected_error"
            debug_info["details"] = {"error": str(e)}
            # Cache unexpected errors under the spec key as well
            _NPM_CACHE[fallback_cache_key] = {**result, "debug_info": debug_info}
            return _NPM_CACHE[fallback_cache_key]


def max_satisfying(available_versions, version_range, loose=False):
    """
    Return the highest version from `available_versions`
    that satisfies the Node-style `version_range`.

    NOTE: This function must parse the Node-style version ranges
          on its own. The simple example below only supports
          exact versions or no range at all.
    """
    valid_versions = []
    for version_str in available_versions:
        try:
            # parse() will raise ValueError if invalid
            parsed = semver.VersionInfo.parse(version_str)
        except ValueError:
            continue  # skip invalid or weird tags

        # For simplicity, let's say we only handle an exact match or empty spec:
        # (Extend this to handle ^, ~, >=, etc. as needed)
        if not version_range or version_range in ["*", "latest"]:
            valid_versions.append(parsed)
        elif version_str == version_range:
            valid_versions.append(parsed)

    if not valid_versions:
        return None

    # Sort and return max
    valid_versions.sort()
    return str(valid_versions[-1])
