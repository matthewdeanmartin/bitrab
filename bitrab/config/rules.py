from __future__ import annotations

import glob
import logging
import re
from pathlib import Path

from bitrab.models.pipeline import JobConfig, RuleConfig

logger = logging.getLogger(__name__)

RE_VARIABLE = re.compile(r"^\$(\w+)\s*$")
RE_EQUALITY = re.compile(r'^\$(\w+)\s*==\s*"([^"]*)"$')
RE_INEQUALITY = re.compile(r'^\$(\w+)\s*!=\s*"([^"]*)"$')
RE_REGEX_MATCH = re.compile(r"^\$(\w+)\s*=~\s*/([^/]*)/$")
RE_REGEX_NOT_MATCH = re.compile(r"^\$(\w+)\s*!~\s*/([^/]*)/$")

# Tokenizer for && / || splitting (handles quoted strings so we don't split inside them)
RE_AND = re.compile(r"\s*&&\s*")
RE_OR = re.compile(r"\s*\|\|\s*")

# Cache for user-supplied regex patterns extracted from =~ / !~ rule expressions.
# Patterns come from static YAML so the set is small and bounded.
PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def evaluate_rules(job: JobConfig, env: dict[str, str], project_dir: Path | None = None) -> None:
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
        if rule_matches(rule, env, project_dir):
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


def rule_matches(rule: RuleConfig, env: dict[str, str], project_dir: Path | None = None) -> bool:
    """Check if a single rule matches the current environment.

    Both ``if_expr`` and ``exists`` must pass (AND semantics) when both are present.
    """
    if rule.if_expr is not None:
        if not evaluate_if(rule.if_expr, env):
            return False

    if rule.exists is not None:
        if not evaluate_exists(rule.exists, project_dir):
            return False

    return True


def evaluate_exists(patterns: list[str], project_dir: Path | None) -> bool:
    """Return True if at least one pattern matches an existing file under project_dir."""
    base = project_dir or Path(".")
    for pattern in patterns:
        # glob.glob handles wildcards; also do a plain exists check for literal paths
        matches = glob.glob(str(base / pattern), recursive=True)
        if matches:
            return True
    return False


def evaluate_if(expr: str, env: dict[str, str]) -> bool:
    """
    Evaluate a GitLab CI 'if' expression.

    Supports:
    - $VAR (true if non-empty)
    - $VAR == "value"
    - $VAR != "value"
    - $VAR =~ /regex/
    - $VAR !~ /regex/
    - Compound expressions with && and || (top-level, no parentheses)
      && binds tighter than ||, matching standard operator precedence.
    """
    # Handle || at the top level (lowest precedence): split on ' || ' outside quotes
    or_parts = split_top_level(expr, "||")
    if len(or_parts) > 1:
        return any(evaluate_if(part.strip(), env) for part in or_parts)

    # Handle && (higher precedence)
    and_parts = split_top_level(expr, "&&")
    if len(and_parts) > 1:
        return all(evaluate_if(part.strip(), env) for part in and_parts)

    # --- atomic expressions ---

    # 1. Variable existence/non-empty check: "$CI_COMMIT_TAG"
    var_match = RE_VARIABLE.match(expr)
    if var_match:
        var_name = var_match.group(1)
        return bool(env.get(var_name))

    # 2. Equality: '$CI_COMMIT_BRANCH == "main"'
    eq_match = RE_EQUALITY.match(expr)
    if eq_match:
        var_name, value = eq_match.groups()
        return env.get(var_name, "") == value

    # 3. Inequality: '$CI_COMMIT_BRANCH != "main"'
    neq_match = RE_INEQUALITY.match(expr)
    if neq_match:
        var_name, value = neq_match.groups()
        return env.get(var_name, "") != value

    # 4. Regex match: '$CI_COMMIT_TAG =~ /^v/'
    re_match = RE_REGEX_MATCH.match(expr)
    if re_match:
        var_name, pattern = re_match.groups()
        compiled = PATTERN_CACHE.get(pattern)
        if compiled is None:
            try:
                compiled = re.compile(pattern)
            except re.error:
                return False
            PATTERN_CACHE[pattern] = compiled
        return bool(compiled.search(env.get(var_name, "")))

    # 5. Regex non-match: '$CI_COMMIT_TAG !~ /^v/'
    nre_match = RE_REGEX_NOT_MATCH.match(expr)
    if nre_match:
        var_name, pattern = nre_match.groups()
        compiled = PATTERN_CACHE.get(pattern)
        if compiled is None:
            try:
                compiled = re.compile(pattern)
            except re.error:
                return True
            PATTERN_CACHE[pattern] = compiled
        return not bool(compiled.search(env.get(var_name, "")))

    # Fallback: unrecognized expression — warn and treat as non-match
    logger.warning("Could not evaluate rules expression: %r — treating as non-match", expr)
    return False


def split_top_level(expr: str, operator: str) -> list[str]:
    """Split *expr* on *operator* (``&&`` or ``||``) while respecting quoted strings.

    Returns a list with a single element (the original expression) if the
    operator is not found at the top level.
    """
    parts: list[str] = []
    current: list[str] = []
    i = 0
    op_len = len(operator)
    in_double_quote = False

    while i < len(expr):
        ch = expr[i]
        if ch == '"':
            in_double_quote = not in_double_quote
            current.append(ch)
            i += 1
        elif not in_double_quote and expr[i : i + op_len] == operator:
            parts.append("".join(current))
            current = []
            i += op_len
        else:
            current.append(ch)
            i += 1

    parts.append("".join(current))
    return parts if len(parts) > 1 else [expr]
