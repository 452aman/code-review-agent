"""Per-repository review settings (.codereview.yml), validated before they can change a review's outcome."""
import fnmatch

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.core.types import Severity


class GateConfig(BaseModel):
    fail_on: list[Severity] = Field(default_factory=lambda: [Severity.critical], description="any (new) finding of these severities fails the gate")
    max_new_medium: int | None = Field(default=None, ge=0, description="fail if more than this many medium findings")
    min_score: int | None = Field(default=None, ge=0, le=100)
    only_new: bool = True


class ReviewConfig(BaseModel):
    ignore_rules: list[str] = Field(default_factory=list)
    ignore_paths: list[str] = Field(default_factory=list)
    severity_overrides: dict[str, Severity] = Field(default_factory=dict)
    gate: GateConfig = Field(default_factory=GateConfig)


class ConfigError(ValueError):
    pass


def parse_config(text: str) -> ReviewConfig:
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ConfigError("config must be a mapping")
        return ReviewConfig.model_validate(data)
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML: {e}") from e
    except ValidationError as e:
        raise ConfigError("; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e


def path_ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch("/" + path, p) for p in patterns)


def apply_config(findings, cfg: ReviewConfig):
    """Drop ignored rules/paths and re-grade overridden severities. Returns (kept, number_suppressed)."""
    kept, suppressed = [], 0
    for f in findings:
        if (f.rule_id and f.rule_id in cfg.ignore_rules) or path_ignored(f.file, cfg.ignore_paths):
            suppressed += 1
            continue
        if f.rule_id in cfg.severity_overrides:
            f = f.model_copy(update={"severity": cfg.severity_overrides[f.rule_id]})
        kept.append(f)
    return kept, suppressed
