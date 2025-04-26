import re
import logging
from typing import Dict, Any, List

from .base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class TerraformAnalyzer(BaseAnalyzer):
    """
    Analyzes Terraform files (.tf) for ARM64 compatibility, focusing on EC2 instance types.
    """

    @property
    def analysis_key(self) -> str:
        return "instance_types"

    @property
    def relevant_file_patterns(self) -> List[str]:
        # Matches files ending with .tf
        return [r"\.tf$"]

    def analyze(self, file_content: str, file_path: str) -> Dict[str, Any]:
        """
        Analyzes Terraform file content for instance types and other ARM64
        compatibility indicators.

        Args:
            file_content: The content of the .tf file.
            file_path: The path to the .tf file.

        Returns:
            A dictionary containing lists of found 'instance_types' and 'other_indicators'.
            Example: {'instance_types': ['t2.micro', 'm5.large'], 'other_indicators': ['architecture']}
        """
        logger.debug(f"Analyzing Terraform file: {file_path}")
        results = {"instance_types": [], "other_indicators": []}

        # Look for AWS instance types in instance_type assignments
        # Handles variations in spacing and quotes
        instance_type_pattern = r'instance_type\s*=\s*["\']([^"\']+)["\']'
        try:
            matches = re.findall(instance_type_pattern, file_content)
            if matches:
                results["instance_types"] = list(
                    set(matches)
                )  # Store unique types found in this file
                logger.debug(
                    f"Found instance types in {file_path}: {results['instance_types']}"
                )

            # Look for architecture-specific resources or configurations (case-insensitive)
            arch_indicators = ["architecture", "amd64", "x86_64", "arm64", "graviton"]
            content_lower = file_content.lower()
            found_indicators = [ind for ind in arch_indicators if ind in content_lower]
            if found_indicators:
                results["other_indicators"] = found_indicators
                logger.debug(
                    f"Found architecture indicators in {file_path}: {found_indicators}"
                )

        except Exception as e:
            logger.error(
                f"Error parsing Terraform file {file_path}: {e}", exc_info=True
            )
            # Return empty results on error to avoid breaking the whole analysis
            return {"instance_types": [], "other_indicators": []}

        return results

    def aggregate_results(
        self, analysis_outputs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates instance type findings from multiple Terraform file analyses.

        Args:
            analysis_outputs: A list of dictionaries, where each dictionary is the
                              output of the `analyze` method for a single .tf file,
                              including the 'file' path.
                              Example: [{'file': 'main.tf', 'analysis': {'instance_types': ['t2.micro'], 'other_indicators': []}}, ...]

        Returns:
            A dictionary containing:
            - 'results': List of compatibility checks for each unique instance type found.
            - 'recommendations': List of suggested replacements for incompatible types.
            - 'reasoning': List of explanations for the compatibility status of each type.
        """
        aggregated_results = []
        recommendations = []
        reasoning = []
        processed_instance_types = set()  # Track processed types to avoid duplicates

        logger.info(
            f"Aggregating Terraform analysis results from {len(analysis_outputs)} files."
        )

        for output in analysis_outputs:
            file_path = output.get("file", "unknown_file")
            instance_types_in_file = output.get("analysis", {}).get(
                "instance_types", []
            )

            for instance_type in instance_types_in_file:
                # Process each unique instance type only once
                if instance_type not in processed_instance_types:
                    processed_instance_types.add(instance_type)
                    logger.debug(
                        f"Checking compatibility for instance type: {instance_type} (found in {file_path})"
                    )

                    compatibility = self._is_instance_type_arm_compatible(instance_type)
                    # Add the file path where it was first encountered (or all paths?)
                    # For simplicity, let's just add the current file path.
                    compatibility["file"] = file_path
                    # Add the instance type itself to the result for clarity
                    compatibility["instance_type"] = instance_type

                    aggregated_results.append(compatibility)

                    # Generate reasoning message
                    reason_msg = ""
                    if compatibility.get("already_arm"):
                        reason_msg = f"Instance type `{instance_type}` is already ARM-based and fully compatible."
                    elif compatibility.get("compatible") is True and compatibility.get(
                        "suggestion"
                    ):
                        reason_msg = f"Instance type `{instance_type}` (found in `{file_path}`) can be replaced with ARM equivalent `{compatibility['suggestion']}`."
                        recommendations.append(
                            f"Replace `{instance_type}` with `{compatibility['suggestion']}` in `{file_path}`"
                        )
                    elif compatibility.get("compatible") is False:
                        reason_msg = f"Instance type `{instance_type}` (found in `{file_path}`) has no direct ARM equivalent or is incompatible: {compatibility.get('reason', 'Unknown reason')}."
                        # Optionally add a recommendation to manually review if incompatible
                        recommendations.append(
                            f"Review or replace incompatible instance type `{instance_type}` in `{file_path}`."
                        )
                    else:  # compatible == 'unknown'
                        reason_msg = f"Instance type `{instance_type}` (found in `{file_path}`) requires manual verification for ARM compatibility."
                        recommendations.append(
                            f"Manually verify ARM compatibility for instance type `{instance_type}` in `{file_path}`."
                        )

                    if reason_msg:
                        reasoning.append(reason_msg)

        # De-duplicate recommendations (simple approach)
        unique_recommendations = sorted(list(set(recommendations)))

        logger.info(
            f"Finished aggregating Terraform results. Found {len(aggregated_results)} unique instance types."
        )
        return {
            "results": aggregated_results,  # Renamed from 'instance_types' for clarity
            "recommendations": unique_recommendations,
            "reasoning": reasoning,
        }

    def _is_instance_type_arm_compatible(self, instance_type: str) -> Dict[str, Any]:
        """
        Checks if an AWS instance type is ARM compatible or suggests an ARM equivalent.
        (Internal helper method)

        Args:
            instance_type: The AWS instance type string (e.g., "t3.large").

        Returns:
            A dictionary indicating compatibility status:
            - {'compatible': True, 'already_arm': True}
            - {'compatible': False, 'reason': str}
            - {'compatible': True, 'already_arm': False, 'suggestion': str, 'current': str}
            - {'compatible': 'unknown', 'reason': str, 'current': str}
        """
        # ARM-based instance families (case-insensitive check)
        arm_families = [
            "a1",
            "t4g",
            "m6g",
            "m7g",
            "c6g",
            "c7g",
            "r6g",
            "r7g",
            "x2gd",
            "im4gn",
            "gr6",  # Added gr6
        ]
        # X86-only instance families (or those without straightforward Graviton equivalents)
        x86_only_families = [
            "mac",
            "f1",
            "p2",
            "p3",
            "g3",
            "g4",
            "g5",
            "inf",
            "dl1",
            "vt1",
            "trn1",
        ]  # Added g5, dl1, vt1, trn1

        instance_type_lower = instance_type.lower()

        # Check if it's already an ARM instance
        if any(
            instance_type_lower.startswith(family + ".")
            or instance_type_lower == family
            for family in arm_families
        ):
            return {"compatible": True, "already_arm": True}

        # Check if it's in a family that has no direct ARM equivalent
        if any(
            instance_type_lower.startswith(family + ".")
            or instance_type_lower == family
            for family in x86_only_families
        ):
            return {
                "compatible": False,
                "reason": "Instance family has no direct ARM equivalent or is specialized (e.g., GPU, FPGA, Trainium).",
            }

        # For standard instance types, suggest ARM equivalents
        # Mapping from x86 prefix to Graviton prefix
        instance_mapping = {
            "t3.": "t4g.",
            "t3a.": "t4g.",  # t3a maps to t4g
            "t2.": "t4g.",
            "m6i.": "m7g.",
            "m6a.": "m7g.",  # Suggest m7g for m6i/m6a
            "m5.": "m6g.",
            "m5a.": "m6g.",
            "m5n.": "m6gn.",
            "m5zn.": "m6g.",  # m5zn has high freq, m6g is general purpose
            "m4.": "m6g.",
            "c6i.": "c7g.",
            "c6a.": "c7g.",  # Suggest c7g for c6i/c6a
            "c5.": "c6g.",
            "c5a.": "c6g.",
            "c5n.": "c6gn.",
            "c4.": "c6g.",
            "r6i.": "r7g.",
            "r6a.": "r7g.",  # Suggest r7g for r6i/r6a
            "r5.": "r6g.",
            "r5a.": "r6g.",
            "r5b.": "r6g.",
            "r5n.": "r6gn.",
            "r4.": "r6g.",
            "x1e.": "x2gd.",
            "x1.": "x2gd.",  # x2gd is Graviton2 based memory optimized
            "z1d.": "m6g.",  # z1d high freq, suggest general purpose m6g or manual review
            "i3.": "im4gn.",
            "i3en.": "i4g.",  # Storage optimized mapping
            "d2.": "i4g.",
            "d3.": "i4g.",
            "d3en.": "i4g.",  # Dense storage mapping
        }

        for x86_prefix, arm_prefix in instance_mapping.items():
            if instance_type_lower.startswith(x86_prefix):
                # Get the size part (e.g., "large" from "t3.large")
                size = instance_type[len(x86_prefix) :]
                # Construct suggested type, preserving original case for size if possible
                suggested_type = f"{arm_prefix}{size}"
                return {
                    "compatible": True,  # Mark as compatible because a suggestion exists
                    "already_arm": False,
                    "suggestion": suggested_type,
                    "current": instance_type,
                }

        # Default if no specific rule matched
        logger.warning(
            f"No specific ARM compatibility rule found for instance type: {instance_type}. Marking as unknown."
        )
        return {
            "compatible": "unknown",
            "current": instance_type,
            "reason": "Instance type family not explicitly mapped or recognized. Requires manual verification.",
        }
