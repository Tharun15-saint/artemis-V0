"""Artemis intelligence-layer exceptions."""


class CrudeDataStaleError(Exception):
    """Raised when the latest crude_oil row is older than the staleness threshold.

    Cost computations must not proceed with stale crude data — the dyeing premium
    flag and forward curve estimates could be materially wrong.
    """


class CrudeQualityBlockError(Exception):
    """Raised when get_blocking_failures() returns unresolved quality check failures.

    No cost output should be written until the underlying data quality issue is resolved
    and the blocking check is marked resolved in quality_check_log.
    """
