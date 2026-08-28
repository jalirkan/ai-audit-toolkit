#!/usr/bin/env python3
"""Last night's assurance check, in one screen, for a reader with coffee.

``cli.py monitor`` already writes everything a reader needs: a status JSON
holding the drift report, next to the run that produced it. Nobody reads JSON
before breakfast. This renders that file as prose for the person who has to
decide, before the day starts, whether the model is still fit to rely on.

Three things shape what it prints:

**It reports; it does not judge.** Drift is a finding the monitor already made,
and saying it in plainer words is the whole job. So a brief that finds drift
still exits 0. ``cli.py monitor`` is the gate that fails a pipeline (its
EXIT_DRIFT), and a reader-facing summary that also exited non-zero on bad news
would make the two indistinguishable to whatever runs them. The only non-zero
exit here means the brief could not report at all.

**Silence is the failure mode to fear.** A timer that quietly stopped firing
looks exactly like a night with nothing to report. Age is therefore checked
before content: past ``--stale-hours`` the brief leads with how old the check
is, because a confident summary of a three-day-old run is worse than none.

**Numbers keep their intervals.** Measurements are rendered through
``Measurement.render`` (D-004, and D-030 on the rendering boundary) rather than
formatted here, so this cannot quietly become the one place in the toolkit that
prints a bare rate.

Everything it reads -- the status file, the stored runs -- is treated as data
written by another process, never as something to trust: unexpected shapes are
described, not assumed away, and a run id is pattern-checked before it is used
to build a path.

    ops/morning-brief.py
    ops/morning-brief.py --json | jq .has_drift
    ops/morning-brief.py --notify --status /tmp/monitor-status.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the nightly runner points ``--status-out``. Under ~/.local/state
#: because it is regenerated every night: reproducible, not precious.
DEFAULT_STATUS_PATH = Path.home() / ".local" / "state" / "ai-audit" / "monitor-status.json"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

#: A nightly job that has not written in a day and a half missed at least one
#: window. Wide enough to survive a late start or a laptop asleep for an
#: evening, narrow enough that two skipped nights cannot pass as fresh.
DEFAULT_STALE_HOURS = 36.0

#: Run ids are the first 16 hex characters of a content hash (battery.runner).
#: Matching against this does double duty: it picks stored runs out of a
#: directory that also holds rendered reports, and it keeps a run id that
#: arrived in a file from turning into a path of someone else's choosing.
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

WIDTH = 78

EXIT_OK = 0
#: Reserved for "there is nothing to brief on" -- see the module docstring.
EXIT_CANNOT_REPORT = 1

# The toolkit's own rendering is importable because this script lives in the
# repo. Kept optional anyway: ops scripts get copied onto other machines, and a
# failed import should cost formatting, not the brief.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
try:
    from core.evidence import Measurement
except ImportError:  # pragma: no cover - exercised by copying this file out
    Measurement = None  # type: ignore[assignment]


# --- terminal ----------------------------------------------------------------

_ANSI = {
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}
_RESET = "\033[0m"

#: How each outcome is coloured. Anything unknown stays uncoloured rather than
#: being coerced into one of these -- an outcome this build does not recognise
#: should look unfamiliar to the reader too.
_OUTCOME_COLOR = {
    "pass": "green",
    "fail": "red",
    "inconclusive": "yellow",
    "error": "magenta",
}


def color_enabled(stream: Any) -> bool:
    """Whether to emit ANSI escapes on ``stream``.

    Colour is decoration; a pipe, a log file, or a NO_COLOR environment must
    receive the same text without it.
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


class Ink:
    """Paints text, or does not, depending on where it is going."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *names: str) -> str:
        if not self.enabled or not names:
            return text
        codes = "".join(_ANSI[n] for n in names if n in _ANSI)
        return f"{codes}{text}{_RESET}" if codes else text


def wrap(text: str, indent: str = "  ") -> List[str]:
    """Wrap for reading, but never mid-token.

    Paths and run ids are the parts a reader retypes or pastes; letting the
    wrapper split them at a hyphen to save a column would cost the one thing
    they are printed for.
    """
    lines = textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return lines or [indent + text]


# --- reading what other processes wrote --------------------------------------


def load_status(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """The monitor status file, or a plain-language reason it is unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"No nightly check has been recorded at {path}."
    except OSError as exc:
        return None, f"Could not read {path}: {exc}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, (
            f"{path} is not readable JSON ({exc}). A check that was interrupted "
            "part-way through writing leaves the file like this."
        )
    if not isinstance(data, dict) or not (
        "checked_at" in data or "has_drift" in data or "report" in data
    ):
        return None, f"{path} exists but is not a monitor status file."
    return data, ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def stored_runs(runs_dir: Path) -> List[Path]:
    """Stored run files, newest first. Reports and status files are not runs."""
    try:
        candidates = list(runs_dir.glob("*.json"))
    except OSError:
        return []
    runs = [p for p in candidates if RUN_ID_PATTERN.match(p.stem)]
    return sorted(runs, key=_mtime, reverse=True)


def load_run(runs_dir: Path, run_id: Any) -> Optional[Dict[str, Any]]:
    """One stored run by id, or None if it is gone, unreadable, or not a run."""
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        return None
    try:
        data = json.loads((runs_dir / f"{run_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- formatting --------------------------------------------------------------


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an evidence timestamp. Naive values are read as UTC, as written."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_local(moment: datetime) -> str:
    """A UTC timestamp in the reader's own timezone -- they live in local time."""
    return moment.astimezone().strftime("%a %d %b, %H:%M")


def humanize_age(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds < -120:
        return "timestamped in the future"
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{round(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{round(hours)} hours ago"
    return f"{round(hours / 24)} days ago"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return ""
    if seconds < 1:
        return "under 1s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


def _render_measurement_fallback(data: Dict[str, Any]) -> str:
    """Formatting of last resort, still carrying the interval and the sample."""
    value, n = data.get("value"), data.get("n")
    if not isinstance(value, (int, float)) or not isinstance(n, int):
        return "measurement could not be read"
    if n == 0:
        return "not tested (n=0)"
    if data.get("kind") == "count":
        return f"{int(value)} of {n}" if int(value) != n else str(int(value))
    low, high = data.get("ci_low"), data.get("ci_high")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        confidence = data.get("confidence")
        pct = int(round((confidence if isinstance(confidence, float) else 0.95) * 100))
        successes = data.get("successes")
        tail = f"{successes}/{n}" if isinstance(successes, int) else f"n={n}"
        return f"{value:.3f} ({pct}% CI [{low:.3f}, {high:.3f}], {tail})"
    return f"{value:.3f} (n={n})"


def render_measurement(data: Any) -> str:
    """A measurement as the workpapers would phrase it."""
    if not isinstance(data, dict):
        return "measurement could not be read"
    if Measurement is not None:
        try:
            return Measurement.from_dict(data).render()
        except (KeyError, TypeError, ValueError):
            pass  # a record too malformed to construct still gets described
    return _render_measurement_fallback(data)


_DIRECTION_WORDS = {
    "lower_is_better": "lower is better",
    "higher_is_better": "higher is better",
    "neutral": "no direction declared",
}


def _interval_phrase(interval: Any) -> str:
    """The bootstrap interval for a change, signed so the direction is visible."""
    if not isinstance(interval, dict):
        return ""
    point, low, high = interval.get("point"), interval.get("low"), interval.get("high")
    if not all(isinstance(v, (int, float)) for v in (point, low, high)):
        return ""
    confidence = interval.get("confidence")
    pct = int(round((confidence if isinstance(confidence, float) else 0.95) * 100))
    return f"change {point:+.3f} ({pct}% CI [{low:+.3f}, {high:+.3f}])"


# --- the summary -------------------------------------------------------------


def _fingerprint_differences(before: Any, after: Any) -> List[Dict[str, Any]]:
    """Which parts of the model configuration moved, field by field.

    A changed model is usually either the reason for the check or the
    explanation for a "regression" that is really a different model, so it is
    worth naming the field rather than just flagging that something differs.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    differences: List[Dict[str, Any]] = []
    for key in ("adapter", "model", "system_prompt_hash"):
        if before.get(key) != after.get(key):
            differences.append(
                {"field": key, "baseline": before.get(key), "current": after.get(key)}
            )
    before_params = before.get("params") if isinstance(before.get("params"), dict) else {}
    after_params = after.get("params") if isinstance(after.get("params"), dict) else {}
    for key in sorted(set(before_params) | set(after_params)):
        if before_params.get(key) != after_params.get(key):
            differences.append(
                {
                    "field": f"params.{key}",
                    "baseline": before_params.get(key),
                    "current": after_params.get(key),
                }
            )
    return differences


def _units(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    units = report.get("units")
    return [u for u in units if isinstance(u, dict)] if isinstance(units, list) else []


def _metrics(unit: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics = unit.get("metrics")
    return [m for m in metrics if isinstance(m, dict)] if isinstance(metrics, list) else []


def _pair_list(report: Dict[str, Any], key: str) -> List[Tuple[str, str]]:
    raw = report.get(key)
    if not isinstance(raw, list):
        return []
    pairs = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


def _procedures(
    run: Optional[Dict[str, Any]], report: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Outcome and headline measurement per tested unit, with its drift verdict.

    What happened comes from the run, which is the record of the night; how it
    compares comes from the drift report. Taking the outcomes from the report
    instead would silently drop any unit added since the baseline -- exactly the
    unit a reader most needs to see, because nothing is watching it yet.
    """
    verdicts = {
        (m.get("probe_id"), m.get("unit"), m.get("metric")): m
        for unit in _units(report)
        for m in _metrics(unit)
    }
    added = set(_pair_list(report, "added_units"))

    entries: List[Dict[str, Any]] = []

    def append(probe_id: str, unit: str, outcome: str, measurement: Any) -> None:
        measurement = measurement if isinstance(measurement, dict) else {}
        metric_name = str(measurement.get("name", ""))
        comparison = verdicts.get((probe_id, unit, metric_name)) or {}
        entries.append(
            {
                "probe_id": probe_id,
                "unit": unit,
                "outcome": outcome,
                "metric": metric_name,
                "rendered": render_measurement(measurement) if measurement else "",
                "direction": str(measurement.get("direction", "")),
                "n": measurement.get("n"),
                "verdict": str(comparison.get("verdict", "")),
                "new_since_baseline": (probe_id, unit) in added,
            }
        )

    if run is not None and isinstance(run.get("evidence"), list):
        for evidence in run["evidence"]:
            if not isinstance(evidence, dict):
                continue
            config = evidence.get("config")
            measurements = evidence.get("measurements")
            append(
                str(evidence.get("probe_id", "")),
                str((config or {}).get("unit", "")) if isinstance(config, dict) else "",
                str(evidence.get("outcome", "unknown")),
                measurements[0] if isinstance(measurements, list) and measurements else None,
            )
        return entries

    for unit in _units(report):
        metrics = _metrics(unit)
        append(
            str(unit.get("probe_id", "")),
            str(unit.get("unit", "")),
            str(unit.get("current_outcome", "unknown")),
            metrics[0].get("current") if metrics else None,
        )
    return entries


def _movements(report: Dict[str, Any]) -> Dict[str, Any]:
    """Every metric sorted into moved-for-the-worse, moved-for-the-better, or not."""
    regressions: List[Dict[str, Any]] = []
    improvements: List[Dict[str, Any]] = []
    changed: List[Dict[str, Any]] = []
    unchanged = 0
    not_comparable = 0
    outcome_changes: List[Dict[str, Any]] = []

    for unit in _units(report):
        if unit.get("outcome_changed"):
            outcome_changes.append(
                {
                    "probe_id": str(unit.get("probe_id", "")),
                    "unit": str(unit.get("unit", "")),
                    "baseline_outcome": str(unit.get("baseline_outcome", "")),
                    "current_outcome": str(unit.get("current_outcome", "")),
                    "worsened": bool(unit.get("outcome_worsened")),
                }
            )
        for metric in _metrics(unit):
            verdict = str(metric.get("verdict", ""))
            entry = {
                "probe_id": str(metric.get("probe_id", "")),
                "unit": str(metric.get("unit", "")),
                "metric": str(metric.get("metric", "")),
                "verdict": verdict,
                "baseline": render_measurement(metric.get("baseline")),
                "current": render_measurement(metric.get("current")),
                "change": _interval_phrase(metric.get("interval")),
                "detail": str(metric.get("detail", "")),
            }
            if verdict == "regression":
                regressions.append(entry)
            elif verdict == "improvement":
                improvements.append(entry)
            elif verdict == "changed":
                changed.append(entry)
            elif verdict == "not-comparable":
                not_comparable += 1
            else:
                unchanged += 1

    return {
        "regressions": regressions,
        "improvements": improvements,
        "changed": changed,
        "unchanged": unchanged,
        "not_comparable": not_comparable,
        "outcome_changes": outcome_changes,
    }


def _next_command(summary: Dict[str, Any], runs_dir: Path) -> str:
    """The one thing worth doing next, given what the brief just said."""
    repo = REPO_ROOT
    runner = repo / "ops" / "nightly-audit.sh"
    # Named only when it is really there; a brief that tells a reader new to
    # Linux to run a file that does not exist has spent its credibility.
    start_a_run = (
        f"cd {repo} && ops/nightly-audit.sh"
        if runner.exists()
        else (
            f"cd {repo} && uv run python cli.py monitor suites/nightly.json "
            "--baseline qwen3-8b-nightly --adapter openai --model qwen3:8b"
        )
    )

    if not summary["ok"]:
        return start_a_run
    if summary["stale"]:
        # ops/install-nightly.sh installs the timer into the user session
        # (~/.config/systemd/user), so the user manager is the one that knows
        # why last night produced nothing.
        return "systemctl --user list-timers ai-audit-nightly.timer"

    run_id = summary.get("run_id") or ""
    have_run_file = bool(RUN_ID_PATTERN.match(run_id)) and (
        runs_dir / f"{run_id}.json"
    ).exists()
    needs_reading = summary["has_drift"] or summary["run_outcome"] in ("fail", "error")
    if needs_reading and have_run_file:
        return f"cd {repo} && uv run python cli.py report {run_id}"
    if needs_reading:
        return start_a_run
    return f"cd {repo} && uv run python cli.py journal verify"


def build_summary(
    status_path: Path,
    runs_dir: Path,
    *,
    stale_hours: float = DEFAULT_STALE_HOURS,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Everything the brief knows, as data. Prose and --json render only this."""
    now = now or datetime.now(timezone.utc)
    status, problem = load_status(status_path)
    runs = stored_runs(runs_dir)

    summary: Dict[str, Any] = {
        "generated_at": now.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status_path": str(status_path),
        "runs_dir": str(runs_dir),
        "ok": status is not None,
        "problem": problem,
        "stale": False,
        "stale_after_hours": stale_hours,
        # Every key a consumer might read is present in every outcome, so
        # --json has one shape whether or not the night went well.
        "checked_at": None,
        "checked_age_hours": None,
        "checked_age_phrase": "",
        "checked_local": "",
        "suite": "",
        "battery": "",
        "baseline": "",
        "run_id": "",
        "run_outcome": "",
        "run_duration_seconds": None,
        "procedures_run": 0,
        "trials_run": None,
        "endpoint": {},
        "has_drift": False,
        "headline": "",
        "comparable": True,
        "comparability_notes": [],
        "procedures": [],
        "drift": {},
        "runs_stored": len(runs),
        "latest_stored_run": None,
        "notes": [],
        "next_command": "",
    }

    if runs:
        latest = load_run(runs_dir, runs[0].stem)
        if latest is not None:
            finished = parse_timestamp(latest.get("finished_at"))
            summary["latest_stored_run"] = {
                "run_id": str(latest.get("run_id", runs[0].stem)),
                "battery": str(latest.get("battery", "")),
                "outcome": str(latest.get("outcome", "")),
                "finished_at": latest.get("finished_at"),
                "finished_local": format_local(finished) if finished else "",
            }

    if status is None:
        summary["headline"] = "no nightly check has been recorded"
        summary["next_command"] = _next_command(summary, runs_dir)
        return summary

    checked_at = parse_timestamp(status.get("checked_at"))
    if checked_at is None:
        # Undatable is treated as stale: the brief cannot vouch for a check it
        # cannot place in time, and the honest move is to say so loudly.
        summary["stale"] = True
        summary["notes"].append(
            "The status file carries no readable timestamp, so its age is unknown."
        )
    else:
        age = now - checked_at
        summary["checked_at"] = status.get("checked_at")
        summary["checked_age_hours"] = round(age.total_seconds() / 3600, 2)
        summary["checked_age_phrase"] = humanize_age(age)
        summary["checked_local"] = format_local(checked_at)
        summary["stale"] = age > timedelta(hours=stale_hours)

    summary["suite"] = str(status.get("suite", ""))
    summary["baseline"] = str(status.get("baseline", ""))
    summary["run_id"] = str(status.get("run_id", ""))
    summary["has_drift"] = bool(status.get("has_drift"))
    summary["headline"] = (
        "drift detected" if summary["has_drift"] else "no drift detected"
    )

    # An empty comparison is left empty rather than filled with zeroes: "no
    # metric moved" and "no metrics were compared" must not look alike.
    report = status.get("report")
    report = report if isinstance(report, dict) else {}

    run = load_run(runs_dir, summary["run_id"])
    if run is None and summary["run_id"]:
        summary["notes"].append(
            f"Run {summary['run_id']} is no longer stored in {runs_dir}, so the "
            "outcomes above come from the comparison rather than from the run "
            "itself, and anything added since the baseline is not shown."
        )
    if run is not None:
        summary["battery"] = str(run.get("battery", ""))
        summary["run_outcome"] = str(run.get("outcome", ""))
        started = parse_timestamp(run.get("started_at"))
        finished = parse_timestamp(run.get("finished_at"))
        if started and finished:
            summary["run_duration_seconds"] = round(
                (finished - started).total_seconds(), 1
            )
        evidence = run.get("evidence")
        if isinstance(evidence, list):
            summary["trials_run"] = sum(
                len(e.get("trials") or [])
                for e in evidence
                if isinstance(e, dict) and isinstance(e.get("trials"), list)
            )

    summary["endpoint"] = {
        "adapter": str(
            (report.get("current_fingerprint") or {}).get("adapter", "")
            if isinstance(report.get("current_fingerprint"), dict)
            else ""
        ),
        "model": str(
            (report.get("current_fingerprint") or {}).get("model", "")
            if isinstance(report.get("current_fingerprint"), dict)
            else ""
        ),
        "changed_since_baseline": bool(report.get("fingerprint_changed")),
        "differences": _fingerprint_differences(
            report.get("baseline_fingerprint"), report.get("current_fingerprint")
        ),
    }

    summary["procedures"] = _procedures(run, report)
    summary["procedures_run"] = len(summary["procedures"])
    summary["drift"] = _movements(report) if report else {}
    summary["comparable"] = bool(report.get("comparable", True))
    for probe_id, unit in _pair_list(report, "added_units"):
        summary["comparability_notes"].append(
            f"{probe_id}/{unit} is new since the baseline, so it has nothing to "
            "be compared against yet"
        )
    for probe_id, unit in _pair_list(report, "removed_units"):
        summary["comparability_notes"].append(
            f"{probe_id}/{unit} was in the baseline but did not run last night"
        )
    for unit in _units(report):
        if unit.get("config_changed"):
            summary["comparability_notes"].append(
                f"{unit.get('probe_id')}/{unit.get('unit')} was reconfigured since "
                "the baseline, so its comparison is not like-for-like"
            )

    if not summary["run_outcome"]:
        outcomes = [p["outcome"] for p in summary["procedures"]]
        for candidate in ("fail", "error", "inconclusive", "pass"):
            if candidate in outcomes:
                summary["run_outcome"] = candidate
                break

    summary["next_command"] = _next_command(summary, runs_dir)
    return summary


# --- prose -------------------------------------------------------------------


def _render_unavailable(summary: Dict[str, Any], ink: Ink) -> List[str]:
    lines = ["", ink("  Nothing to brief on.", "bold", "red")]
    lines.extend(wrap(summary["problem"], indent="  "))
    latest = summary.get("latest_stored_run")
    if summary["runs_stored"] and latest:
        lines.append("")
        lines.extend(
            wrap(
                f"{summary['runs_stored']} run(s) are stored in "
                f"{summary['runs_dir']}. The most recent is "
                f"{latest['run_id']} ({latest['battery'] or 'unnamed battery'}, "
                f"outcome {latest['outcome'] or 'unknown'}, "
                f"{latest['finished_local'] or 'time unknown'}).",
                indent="  ",
            )
        )
    else:
        lines.append("")
        lines.extend(
            wrap(
                f"No stored runs in {summary['runs_dir']} either, so nothing has "
                "been measured on this machine yet.",
                indent="  ",
            )
        )
    return lines


def _render_headline(summary: Dict[str, Any], ink: Ink) -> List[str]:
    lines: List[str] = []
    if summary["stale"]:
        age = summary.get("checked_age_phrase", "of unknown age")
        lines.append(ink("  STALE: this is not last night's check.", "bold", "yellow"))
        lines.extend(
            wrap(
                f"The last one ran {age} and nothing has been written since, so "
                "the nightly job may not be running at all.",
                indent="  ",
            )
        )
        lines.append("")

    drift = summary["has_drift"]
    headline = summary["headline"].upper() if drift else summary["headline"]
    painted = ink(headline, "bold", "red" if drift else "green")
    baseline = summary["baseline"] or "an unnamed baseline"
    prefix = "  As of that check: " if summary["stale"] else "  "
    lines.append(f"{prefix}{painted} against baseline {baseline}")

    if drift:
        lines.extend(
            wrap(
                "Drift means a control measured worse than the baseline by more "
                "than sampling variation explains.",
                indent="  ",
            )
        )

    descriptor = summary["battery"] or summary["suite"] or "suite unknown"
    facts = [f"run {summary['run_id'] or 'unknown'}"]
    if summary["procedures_run"]:
        facts.append(f"{summary['procedures_run']} procedure(s)")
    if summary["trials_run"]:
        facts.append(f"{summary['trials_run']} trials")
    duration = format_duration(summary["run_duration_seconds"])
    if duration:
        facts.append(duration)
    lines.append(ink(f"  {descriptor} -- {', '.join(facts)}", "dim"))

    endpoint = summary["endpoint"]
    if endpoint.get("adapter") or endpoint.get("model"):
        tail = (
            "model configuration changed since the baseline"
            if endpoint.get("changed_since_baseline")
            else "unchanged since the baseline"
        )
        lines.append(
            ink(
                f"  endpoint {endpoint.get('adapter', '?')} / "
                f"{endpoint.get('model', '?')} -- {tail}",
                "dim",
            )
        )
    return lines


def _render_procedures(summary: Dict[str, Any], ink: Ink) -> List[str]:
    lines = ["", ink("What ran", "bold")]
    if not summary["procedures"]:
        lines.extend(wrap("No procedures were recorded for this run."))
        return lines
    for entry in summary["procedures"]:
        outcome = entry["outcome"] or "unknown"
        # Padded to the longest outcome word ("inconclusive") so the probe ids
        # line up in a column the eye can run down.
        label = ink(f"{outcome:<12}", *(c for c in (_OUTCOME_COLOR.get(outcome),) if c))
        unit = f" / {entry['unit']}" if entry["unit"] else ""
        flag = ink("  (new since the baseline)", "cyan") if entry["new_since_baseline"] else ""
        lines.append(f"  {label}  {entry['probe_id']}{unit}{flag}")
        if entry["rendered"]:
            direction = _DIRECTION_WORDS.get(entry["direction"], "")
            detail = f"{entry['metric']} {entry['rendered']}"
            if direction:
                detail += f", {direction}"
            lines.extend(wrap(detail, indent=" " * 16))
    return lines


def _render_comparison(summary: Dict[str, Any], ink: Ink) -> List[str]:
    lines = ["", ink(f"Against baseline {summary['baseline'] or '(unnamed)'}", "bold")]
    drift = summary["drift"]
    if not drift:
        lines.extend(
            wrap(
                "The status file carried no comparison detail, so nothing above "
                "has been measured against the baseline."
            )
        )
        return lines

    for difference in summary["endpoint"].get("differences", []):
        lines.extend(
            wrap(
                f"the {difference['field']} changed: {difference['baseline']!r} -> "
                f"{difference['current']!r}",
                indent="  ",
            )
        )
    for note in summary["comparability_notes"]:
        lines.extend(wrap(f"not like-for-like: {note}", indent="  "))

    for entry in drift.get("regressions", []) + drift.get("changed", []):
        word = "worse" if entry["verdict"] == "regression" else "moved"
        lines.append(
            f"  {ink(word, 'red' if entry['verdict'] == 'regression' else 'yellow')}  "
            f"{entry['probe_id']}/{entry['unit']} {entry['metric']}"
        )
        change = f" -- {entry['change']}" if entry["change"] else ""
        lines.extend(
            wrap(f"{entry['baseline']} -> {entry['current']}{change}", indent="        ")
        )
    for entry in drift.get("improvements", []):
        lines.append(
            f"  {ink('better', 'green')} {entry['probe_id']}/{entry['unit']} "
            f"{entry['metric']}"
        )
        change = f" -- {entry['change']}" if entry["change"] else ""
        lines.extend(
            wrap(f"{entry['baseline']} -> {entry['current']}{change}", indent="        ")
        )
    for entry in drift.get("outcome_changes", []):
        direction = "worse" if entry["worsened"] else "better"
        lines.extend(
            wrap(
                f"{entry['probe_id']}/{entry['unit']} outcome "
                f"{entry['baseline_outcome']} -> {entry['current_outcome']} "
                f"({direction})",
                indent="  ",
            )
        )

    steady = drift.get("unchanged", 0)
    if steady:
        lines.extend(
            wrap(
                f"{steady} other metric(s) moved no more than sampling variation "
                "would explain.",
                indent="  ",
            )
        )
    skipped = drift.get("not_comparable", 0)
    if skipped:
        lines.extend(
            wrap(f"{skipped} metric(s) could not be compared statistically.", indent="  ")
        )
    return lines


def render_prose(summary: Dict[str, Any], ink: Ink) -> List[str]:
    title = ink("Nightly assurance brief", "bold")
    when = summary.get("checked_local", "")
    age = summary.get("checked_age_phrase", "")
    if not summary["ok"]:
        subtitle = "nothing recorded yet"
    elif when:
        subtitle = f"checked {when} ({age})"
    else:
        subtitle = "check time unknown"
    lines = [f"{title}  {ink(subtitle, 'dim')}"]

    if not summary["ok"]:
        lines.extend(_render_unavailable(summary, ink))
    else:
        lines.append("")
        lines.extend(_render_headline(summary, ink))
        lines.extend(_render_procedures(summary, ink))
        lines.extend(_render_comparison(summary, ink))
        for note in summary["notes"]:
            lines.extend(wrap(note, indent="  "))

    lines.append("")
    lines.append(f"{ink('Next:', 'bold')} {summary['next_command']}")
    lines.append("")
    return lines


# --- desktop notification ----------------------------------------------------


def notification_text(summary: Dict[str, Any]) -> Tuple[str, str, str]:
    """Title, body, and urgency for the desktop popup."""
    if not summary["ok"]:
        return (
            "Nightly assurance: no check recorded",
            summary["problem"],
            "critical",
        )
    if summary["stale"]:
        return (
            "Nightly assurance: stale",
            f"Last check {summary.get('checked_age_phrase', 'age unknown')} "
            f"({summary.get('checked_local', '')}). The nightly job may not be running.",
            "critical",
        )
    if summary["has_drift"]:
        regressions = summary["drift"].get("regressions", [])
        first = (
            f"{regressions[0]['probe_id']}/{regressions[0]['unit']} "
            f"{regressions[0]['metric']}: {regressions[0]['change'] or 'moved'}"
            if regressions
            else "an outcome got worse"
        )
        return (
            "Nightly assurance: DRIFT",
            f"vs baseline {summary['baseline']}. {first}",
            "critical",
        )
    return (
        "Nightly assurance: no drift",
        f"{summary['procedures_run']} procedure(s) vs baseline "
        f"{summary['baseline']}, run outcome {summary['run_outcome'] or 'unknown'}.",
        "normal",
    )


def send_notification(summary: Dict[str, Any]) -> bool:
    """Pop the summary onto the desktop. A missing notify-send is not an error.

    The brief's output is the deliverable; the notification is a convenience,
    so every way it can fail -- no binary, no session bus, a daemon that hangs
    -- is swallowed rather than allowed to take the brief down with it.
    """
    binary = shutil.which("notify-send")
    if binary is None:
        return False
    title, body, urgency = notification_text(summary)
    try:
        subprocess.run(
            [binary, "--app-name=ai-audit", f"--urgency={urgency}", title, body],
            check=False,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


# --- entry point -------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morning-brief",
        description=(
            "Plain-language summary of the last nightly assurance check: what "
            "ran, what it found, and how it compares to the baseline."
        ),
        epilog=(
            "Exits 0 whether or not drift was found -- reporting a finding is "
            "not the same as failing. A non-zero exit means there was no "
            "readable status file to report on."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--status",
        default=str(DEFAULT_STATUS_PATH),
        help="monitor status JSON written by 'cli.py monitor --status-out'",
    )
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help="directory of stored runs, for run detail the status file omits",
    )
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=DEFAULT_STALE_HOURS,
        help="age past which the check is reported as stale (default: 36)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the structured summary instead of prose",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="also send a desktop notification (skipped if notify-send is absent)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = build_summary(
            Path(args.status).expanduser(),
            Path(args.runs_dir).expanduser(),
            stale_hours=args.stale_hours,
        )
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            ink = Ink(color_enabled(sys.stdout))
            print("\n".join(render_prose(summary, ink)))
        if args.notify:
            send_notification(summary)
    except Exception as exc:  # noqa: BLE001 - a brief must not greet anyone with a traceback
        if os.environ.get("AI_AUDIT_DEBUG"):
            raise
        print(f"morning brief could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Set AI_AUDIT_DEBUG=1 to see the full error.", file=sys.stderr)
        return EXIT_CANNOT_REPORT
    return EXIT_OK if summary["ok"] else EXIT_CANNOT_REPORT


if __name__ == "__main__":
    sys.exit(main())
