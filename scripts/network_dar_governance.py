"""Read active DAR versions and proposed changes from Scan governance contracts."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import sys
from typing import Literal, NotRequired, TypedDict


DAR_FIELDS = {
    "splice-amulet": "amulet",
    "splice-amulet-name-service": "amuletNameService",
    "splice-dso-governance": "dsoGovernance",
    "splice-validator-lifecycle": "validatorLifecycle",
    "splice-wallet": "wallet",
    "splice-wallet-payments": "walletPayments",
}


class DarVersion(TypedDict):
    name: str
    version: str


class DarProposal(TypedDict):
    id: str
    status: Literal["scheduled", "open", "threshold-met"]
    action: str
    effectiveAt: str | None
    versions: list[DarVersion]
    voteBefore: NotRequired[str]
    acceptedVotes: NotRequired[int]
    rejectedVotes: NotRequired[int]
    requiredVotes: NotRequired[int]


class DarGovernance(TypedDict):
    status: Literal["current", "stale", "unavailable"]
    votesStatus: Literal["current", "unavailable"]
    sourceUrl: str
    votesUrl: str
    proposals: list[DarProposal]


def record(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(f"Missing or invalid {label}")
    return value


def sequence(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise RuntimeError(f"Missing or invalid {label}")
    return value


def text_value(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"Missing or invalid {label}")
    return value


def instant(value: object) -> datetime:
    text = text_value(value, "governance timestamp")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Invalid governance timestamp: {text!r}") from exc
    if result.tzinfo is None:
        raise RuntimeError(f"Governance timestamp has no timezone: {text!r}")
    return result


def package_versions(config: object) -> list[DarVersion]:
    packages = record(
        record(config, "AmuletConfig").get("packageConfig"), "packageConfig"
    )
    # Empty strings explicitly mean that no version should be vetted. Preserve them.
    return [
        {
            "name": name,
            "version": text_value(packages.get(field), f"packageConfig.{field}"),
        }
        for name, field in DAR_FIELDS.items()
    ]


def contract_payload(value: object) -> dict:
    return record(record(value, "contract").get("payload"), "contract payload")


def map_entries(value: object, label: str) -> list[tuple[str, dict]]:
    # Daml GenMap JSON is an array of key/value pairs.
    result = []
    for entry in sequence(value, label):
        if not isinstance(entry, list) or len(entry) != 2:
            raise RuntimeError(f"Invalid {label} entry")
        result.append((text_value(entry[0], label), record(entry[1], label)))
    if len({key for key, _ in result}) != len(result):
        raise RuntimeError(f"Duplicate {label} entry")
    return result


def differences(new: list[DarVersion], old: list[DarVersion]) -> list[DarVersion]:
    previous = {row["name"]: row["version"] for row in old}
    return [row for row in new if row["version"] != previous.get(row["name"])]


def schedule_item(value: object) -> tuple[datetime, str, list[DarVersion]]:
    item = record(value, "schedule item")
    timestamp = text_value(item.get("_1"), "schedule effective time")
    return instant(timestamp), timestamp, package_versions(item.get("_2"))


def read_governance(
    dso_response: dict, votes_response: dict, now: datetime
) -> tuple[list[DarVersion], list[DarProposal]]:
    amulet = contract_payload(
        record(dso_response.get("amulet_rules"), "amulet_rules").get("contract")
    )
    schedule = record(amulet.get("configSchedule"), "configSchedule")
    current = package_versions(schedule.get("initialValue"))
    future = sorted(
        (
            schedule_item(item)
            for item in sequence(schedule.get("futureValues"), "futureValues")
        ),
        key=lambda item: item[0],
    )
    if len({time for time, _, _ in future}) != len(future):
        raise RuntimeError("Duplicate AmuletRules schedule effective time")
    for time, _, versions in future:
        if time <= now:
            current = versions

    proposals: list[DarProposal] = []
    previous = current
    for time, timestamp, versions in future:
        if time > now:
            changes = differences(versions, previous)
            if changes:
                proposals.append(
                    {
                        "id": f"schedule:{timestamp}",
                        "status": "scheduled",
                        "action": "Approved package change",
                        "effectiveAt": timestamp,
                        "versions": changes,
                    }
                )
            previous = versions

    dso = contract_payload(
        record(dso_response.get("dso_rules"), "dso_rules").get("contract")
    )
    active_svs = {
        text_value(info.get("name"), "SV name")
        for _, info in map_entries(dso.get("svs"), "svs")
    }
    threshold = dso_response.get("voting_threshold")
    if type(threshold) is not int or not 1 <= threshold <= len(active_svs):
        raise RuntimeError("Invalid Scan voting_threshold")

    for contract in sequence(
        votes_response.get("dso_rules_vote_requests"), "dso_rules_vote_requests"
    ):
        request = contract_payload(contract)
        action = record(request.get("action"), "vote action")
        if action.get("tag") != "ARC_AmuletRules":
            continue
        action = record(
            record(action.get("value"), "AmuletRules action").get("amuletRulesAction"),
            "amuletRulesAction",
        )
        tag = action.get("tag")
        if tag not in {
            "CRARC_SetConfig",
            "CRARC_AddFutureAmuletConfigSchedule",
            "CRARC_UpdateFutureAmuletConfigSchedule",
            "CRARC_RemoveFutureAmuletConfigSchedule",
        }:
            continue
        args = record(action.get("value"), "AmuletRules choice arguments")
        effective_at = request.get("targetEffectiveAt")
        if effective_at is not None:
            instant(effective_at)
        reference = current
        if effective_at is not None:
            for time, _, versions in future:
                if time <= instant(effective_at):
                    reference = versions
        label = "Proposed package change"
        if tag == "CRARC_SetConfig":
            # SetConfig patches only fields changed relative to baseConfig. A stale
            # unchanged field must never be presented as a proposed downgrade.
            intended = differences(
                package_versions(args.get("newConfig")),
                package_versions(args.get("baseConfig")),
            )
            changes = differences(intended, reference)
        elif tag == "CRARC_RemoveFutureAmuletConfigSchedule":
            schedule_time = instant(args.get("scheduleTime"))
            previous = current
            changes = []
            for time, timestamp, versions in future:
                if time == schedule_time and time > now:
                    changes = differences(previous, versions)
                    effective_at = effective_at or timestamp
                    break
                if now < time < schedule_time:
                    previous = versions
            label = "Cancel approved package change"
        else:
            field = (
                "newScheduleItem"
                if tag == "CRARC_AddFutureAmuletConfigSchedule"
                else "scheduleItem"
            )
            _, timestamp, versions = schedule_item(args.get(field))
            effective_at = effective_at or timestamp
            reference = current
            for time, _, previous_versions in future:
                if time < instant(timestamp) or (
                    tag == "CRARC_UpdateFutureAmuletConfigSchedule"
                    and time == instant(timestamp)
                ):
                    reference = previous_versions
            changes = differences(versions, reference)
        if not changes:
            continue

        votes = [
            vote
            for name, vote in map_entries(request.get("votes"), "votes")
            if name in active_svs
        ]
        if any(type(vote.get("accept")) is not bool for vote in votes):
            raise RuntimeError("Invalid vote acceptance flag")
        accepted = sum(vote["accept"] for vote in votes)
        vote_before = text_value(request.get("voteBefore"), "voteBefore")
        instant(vote_before)
        proposals.append(
            {
                "id": text_value(
                    request.get("trackingCid")
                    or record(contract, "vote contract").get("contract_id"),
                    "vote ID",
                ),
                # Even with enough yes votes, votes may change until execution. Only
                # the AmuletRules contract determines which versions are active.
                "status": "threshold-met" if accepted >= threshold else "open",
                "action": label,
                "effectiveAt": effective_at,
                "voteBefore": vote_before,
                "versions": changes,
                "acceptedVotes": accepted,
                "rejectedVotes": len(votes) - accepted,
                "requiredVotes": threshold,
            }
        )
    proposals.sort(
        key=lambda proposal: (
            instant(proposal["effectiveAt"])
            if proposal["effectiveAt"]
            else datetime.min.replace(tzinfo=timezone.utc),
            proposal["id"],
        )
    )
    return current, proposals


def collect_dar_governance(
    endpoint: str,
    timeout: float,
    now: datetime,
    fetch_json: Callable[[str, float], dict],
) -> tuple[list[DarVersion], DarGovernance]:
    source_url = f"https://{endpoint}/api/scan/v0/dso"
    votes_url = f"https://{endpoint}/api/scan/v0/admin/sv/voterequests"
    state: DarGovernance = {
        "status": "current",
        "votesStatus": "current",
        "sourceUrl": source_url,
        "votesUrl": votes_url,
        "proposals": [],
    }
    dso = fetch_json(source_url, timeout)
    # Validate and retain active versions and approved schedules independently of
    # the open-vote endpoint, which may be temporarily unavailable.
    versions, proposals = read_governance(dso, {"dso_rules_vote_requests": []}, now)
    try:
        versions, proposals = read_governance(dso, fetch_json(votes_url, timeout), now)
    except Exception as exc:
        print(
            f"WARNING: {endpoint}: open DAR votes unavailable ({exc})", file=sys.stderr
        )
        state["votesStatus"] = "unavailable"
    state["proposals"] = proposals
    return versions, state


def unavailable_dar_governance(
    endpoint: str, previous: dict
) -> tuple[list[DarVersion], DarGovernance]:
    source_url = f"https://{endpoint}/api/scan/v0/dso"
    votes_url = f"https://{endpoint}/api/scan/v0/admin/sv/voterequests"
    old_state = previous.get("darGovernance", {})
    old_versions = previous.get("darVersions", [])
    if (
        old_state.get("status") in {"current", "stale"}
        and old_state.get("sourceUrl") == source_url
        and {row.get("name") for row in old_versions} == set(DAR_FIELDS)
        and len(old_versions) == len(DAR_FIELDS)
    ):
        state = deepcopy(old_state)
        state["status"] = "stale"
        return deepcopy(old_versions), state
    return [], {
        "status": "unavailable",
        "votesStatus": "unavailable",
        "sourceUrl": source_url,
        "votesUrl": votes_url,
        "proposals": [],
    }
