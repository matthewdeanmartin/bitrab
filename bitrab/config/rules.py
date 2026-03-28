from __future__ import annotations

import re

from bitrab.models.pipeline import JobConfig, RuleConfig


def evaluate_rules(job: JobConfig, env: dict[str, str]) -> None:
    """
    Evaluate rules for a job and update its configuration accordingly.

    If rules are present, they are evaluated in order. The first matching rule
    determines the job's 'when', 'allow_failure', 'variables', and 'needs'.
    If no rule matches, the job's 'when' is set to 'never'.

    If no rules are present, the job's existing 'when' is preserved.
    """
    if not job.rules:
        return

    matched_rule = None
    for rule in job.rules:
        if _rule_matches(rule, env):
            matched_rule = rule
            break

    if matched_rule:
        # If rule matches, it provides attributes.
        # If 'when' is not specified in the rule, it defaults to 'on_success' (GitLab behavior)
        job.when = matched_rule.when if matched_rule.when is not None else "on_success"

        if matched_rule.allow_failure is not None:
            job.allow_failure = matched_rule.allow_failure

        if matched_rule.variables:
            job.variables.update(matched_rule.variables)

        if matched_rule.needs is not None:
            job.needs = matched_rule.needs
    else:
        # If no rule matches, the job is excluded
        job.when = "never"


def _rule_matches(rule: RuleConfig, env: dict[str, str]) -> bool:
    """Check if a single rule matches the current environment."""
    if rule.if_expr is None:
        return True  # A rule with no 'if' always matches (e.g. 'when: always' or 'when: never')

    return _evaluate_if(rule.if_expr, env)


def _evaluate_if(expr: str, env: dict[str, str]) -> bool:
    """
    Evaluate a GitLab CI 'if' expression.

    Currently supports:
    - $VAR (true if non-empty)
    - $VAR == "value"
    - $VAR != "value"
    - $VAR =~ /regex/
    - $VAR !~ /regex/
    - Simple combinations with && and || (basic support)
    """
    # Simple implementation for now:
    # 1. Variable existence/non-empty check: "$CI_COMMIT_TAG"
    if expr.startswith("$") and " " not in expr and "==" not in expr and "!=" not in expr and "=~" not in expr and "!~" not in expr:
        var_name = expr[1:].strip('"')
        return bool(env.get(var_name))

    # 2. Equality: '$CI_COMMIT_BRANCH == "main"'
    eq_match = re.match(r'^\$(\w+)\s*==\s*"([^"]*)"$', expr)
    if eq_match:
        var_name, value = eq_match.groups()
        return env.get(var_name, "") == value

    # 3. Inequality: '$CI_COMMIT_BRANCH != "main"'
    neq_match = re.match(r'^\$(\w+)\s*!=\s*"([^"]*)"$', expr)
    if neq_match:
        var_name, value = neq_match.groups()
        return env.get(var_name, "") != value

    # 4. Regex match: '$CI_COMMIT_TAG =~ /^v/'
    re_match = re.match(r"^\$(\w+)\s*=~\s*/([^/]*)/$", expr)
    if re_match:
        var_name, pattern = re_match.groups()
        try:
            return bool(re.search(pattern, env.get(var_name, "")))
        except re.error:
            return False

    # 5. Regex non-match: '$CI_COMMIT_TAG !~ /^v/'
    nre_match = re.match(r"^\$(\w+)\s*!~\s*/([^/]*)/$", expr)
    if nre_match:
        var_name, pattern = nre_match.groups()
        try:
            return not bool(re.search(pattern, env.get(var_name, "")))
        except re.error:
            return True

    # Fallback or complex expressions: return False for now to be safe
    return False
