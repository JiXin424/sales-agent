"""Failure clustering: group failed evaluation cases by shared symptoms.

Clustering enables the optimizer to propose one fix for a group of related
failures rather than one fix per individual case.
"""

from __future__ import annotations


def cluster_by_primary_cause(
    diagnoses: list[dict],
) -> dict[str, list[str]]:
    """Group diagnosis dicts by their primary_cause.

    Each diagnosis dict must have 'primary_cause' and 'case_id' keys.

    Returns:
        {cause: [case_id, ...]}
    """
    clusters: dict[str, list[str]] = {}
    for d in diagnoses:
        cause = d.get("primary_cause", "unknown")
        clusters.setdefault(cause, []).append(d.get("case_id", ""))
    return clusters


def cluster_by_shared_symptoms(
    diagnoses: list[dict],
    min_cluster_size: int = 2,
) -> list[dict]:
    """Cluster diagnoses by shared evidence patterns.

    Two diagnoses share symptoms when they have overlapping evidence strings
    or identical blocked check lists.

    Returns a list of cluster dicts with keys:
        - cluster_key: str
        - cases: list[str]
        - shared_symptoms: list[str]
        - primary_cause: str (majority vote)
    """
    clusters: list[dict] = []
    assigned: set[int] = set()

    for i, di in enumerate(diagnoses):
        if i in assigned:
            continue
        cluster = {
            "cluster_key": f"cluster_{len(clusters)}",
            "cases": [di.get("case_id", "")],
            "shared_symptoms": list(di.get("evidence", [])),
            "primary_cause": di.get("primary_cause", "unknown"),
        }

        for j, dj in enumerate(diagnoses):
            if j <= i or j in assigned:
                continue
            if di.get("primary_cause") == dj.get("primary_cause"):
                cluster["cases"].append(dj.get("case_id", ""))
                assigned.add(j)

        clusters.append(cluster)

    # Filter clusters below min size
    return [c for c in clusters if len(c["cases"]) >= min_cluster_size]
