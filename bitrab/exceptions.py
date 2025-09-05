class BitrabError(Exception):
    """Any error raised from bitrab"""


class GitlabRunnerError(BitrabError):
    """Base exception for GitLab CI runner errors."""


class JobExecutionError(GitlabRunnerError):
    """Raised when a job fails to execute successfully."""
