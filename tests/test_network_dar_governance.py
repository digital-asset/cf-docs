from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import network_dar_governance as governance

NOW = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
BEFORE = "2026-09-08T11:00:00Z"
AFTER = "2026-09-08T13:00:00Z"
ENDPOINT = "scan.example.com"


def config(version="0.1.1", **overrides):
    return {
        "packageConfig": {
            field: overrides.get(field, version)
            for field in governance.DAR_FIELDS.values()
        }
    }


def dso_response():
    # Minimal wire shapes from Scan's GetDsoInfoResponse and Daml GenMap encoding.
    return {
        "amulet_rules": {
            "contract": {
                "payload": {
                    "configSchedule": {
                        "initialValue": config(),
                        "futureValues": [],
                    }
                }
            }
        },
        "dso_rules": {
            "contract": {
                "payload": {
                    "svs": [[f"party-{i}", {"name": f"SV-{i}"}] for i in range(4)]
                }
            }
        },
        "voting_threshold": 3,
    }


def request(
    *, new=None, base=None, yes=1, effective=AFTER, tag="CRARC_SetConfig", args=None
):
    return {
        "contract_id": "contract-1",
        "payload": {
            "action": {
                "tag": "ARC_AmuletRules",
                "value": {
                    "amuletRulesAction": {
                        "tag": tag,
                        "value": args
                        or {
                            "newConfig": new or config("0.1.2"),
                            "baseConfig": base or config(),
                        },
                    }
                },
            },
            "trackingCid": "tracking-1",
            "targetEffectiveAt": effective,
            "voteBefore": BEFORE,
            "votes": [[f"SV-{i}", {"accept": True}] for i in range(yes)],
        },
    }


def votes(*requests):
    return {"dso_rules_vote_requests": list(requests)}


def test_current_uses_latest_effective_schedule_instead_of_available_versions():
    dso = dso_response()
    dso["amulet_rules"]["contract"]["payload"]["configSchedule"]["futureValues"] = [
        {"_1": AFTER, "_2": config("0.1.3")},
        {"_1": BEFORE, "_2": config("0.1.2")},
    ]
    current, proposals = governance.read_governance(dso, votes(), NOW)
    assert len(current) == 6
    assert {row["version"] for row in current} == {"0.1.2"}
    assert proposals[0]["status"] == "scheduled"
    assert {row["version"] for row in proposals[0]["versions"]} == {"0.1.3"}
    current, proposals = governance.read_governance(
        dso, votes(), governance.instant(AFTER)
    )
    assert {row["version"] for row in current} == {"0.1.3"}
    assert proposals == []


@pytest.mark.parametrize("yes,status", [(1, "open"), (3, "threshold-met")])
@pytest.mark.parametrize("effective", [AFTER, BEFORE, None])
def test_votes_never_promote_versions_even_after_effective_time(yes, status, effective):
    current, proposals = governance.read_governance(
        dso_response(), votes(request(yes=yes, effective=effective)), NOW
    )
    assert {row["version"] for row in current} == {"0.1.1"}
    assert len(proposals[0]["versions"]) == 6
    assert proposals[0]["status"] == status
    assert proposals[0]["acceptedVotes"] == yes
    assert proposals[0]["id"] == "tracking-1"


def test_set_config_only_changes_fields_that_differ_from_base():
    dso = dso_response()
    dso["amulet_rules"]["contract"]["payload"]["configSchedule"]["initialValue"] = (
        config("0.1.5")
    )
    vote = request(new=config("0.1.1", wallet="0.1.6"), base=config("0.1.1"))
    current, proposals = governance.read_governance(dso, votes(vote), NOW)
    assert {row["version"] for row in current} == {"0.1.5"}
    assert proposals[0]["versions"] == [{"name": "splice-wallet", "version": "0.1.6"}]


def test_unrelated_config_vote_is_omitted():
    vote = request(new={**config(), "unrelatedSetting": 100})
    assert governance.read_governance(dso_response(), votes(vote), NOW)[1] == []


def test_votes_from_offboarded_svs_do_not_count_toward_approval():
    vote = request(yes=2)
    vote["payload"]["votes"].append(["offboarded", {"accept": True}])
    vote["payload"]["votes"].append(["SV-3", {"accept": False}])
    proposal = governance.read_governance(dso_response(), votes(vote), NOW)[1][0]
    assert (
        proposal["acceptedVotes"],
        proposal["rejectedVotes"],
        proposal["status"],
    ) == (2, 1, "open")


@pytest.mark.parametrize(
    "tag,field",
    [
        ("CRARC_AddFutureAmuletConfigSchedule", "newScheduleItem"),
        ("CRARC_UpdateFutureAmuletConfigSchedule", "scheduleItem"),
    ],
)
def test_legacy_schedule_vote_is_proposed_until_applied(tag, field):
    vote = request(
        tag=tag, effective=None, args={field: {"_1": AFTER, "_2": config("0.1.2")}}
    )
    current, proposals = governance.read_governance(dso_response(), votes(vote), NOW)
    assert {row["version"] for row in current} == {"0.1.1"}
    assert proposals[0]["effectiveAt"] == AFTER
    assert proposals[0]["status"] == "open"


def test_removal_vote_shows_cancellation_without_removing_approved_schedule():
    dso = dso_response()
    dso["amulet_rules"]["contract"]["payload"]["configSchedule"]["futureValues"] = [
        {"_1": AFTER, "_2": config("0.1.2")}
    ]
    vote = request(
        tag="CRARC_RemoveFutureAmuletConfigSchedule",
        args={"scheduleTime": AFTER},
        effective=None,
    )
    _, proposals = governance.read_governance(dso, votes(vote), NOW)
    assert {proposal["status"] for proposal in proposals} == {"scheduled", "open"}
    assert (
        next(p for p in proposals if p["status"] == "open")["action"]
        == "Cancel approved package change"
    )


@pytest.mark.parametrize("version,expected_votes", [("0.1.1", 1), ("0.1.2", 0)])
def test_schedule_update_is_compared_to_the_existing_scheduled_versions(
    version, expected_votes
):
    dso = dso_response()
    dso["amulet_rules"]["contract"]["payload"]["configSchedule"]["futureValues"] = [
        {"_1": AFTER, "_2": config("0.1.2")}
    ]
    vote = request(
        tag="CRARC_UpdateFutureAmuletConfigSchedule",
        effective=None,
        args={"scheduleItem": {"_1": AFTER, "_2": config(version)}},
    )
    _, proposals = governance.read_governance(dso, votes(vote), NOW)
    open_votes = [proposal for proposal in proposals if proposal["status"] == "open"]
    assert len(open_votes) == expected_votes
    if open_votes:
        assert {row["version"] for row in open_votes[0]["versions"]} == {version}


def test_empty_version_means_no_package_vetted():
    assert governance.package_versions(config(wallet=""))[-2] == {
        "name": "splice-wallet",
        "version": "",
    }


@pytest.mark.parametrize("missing", list(governance.DAR_FIELDS.values()))
def test_missing_package_field_is_not_silently_dropped(missing):
    value = config()
    del value["packageConfig"][missing]
    with pytest.raises(RuntimeError, match="packageConfig"):
        governance.package_versions(value)


def test_fetches_network_scan_sources_and_only_preserves_verified_snapshot():
    seen = []

    def fetch(url, timeout):
        seen.append(url)
        return votes(request()) if url.endswith("voterequests") else dso_response()

    versions, state = governance.collect_dar_governance(ENDPOINT, 1, NOW, fetch)
    assert seen == [state["sourceUrl"], state["votesUrl"]]
    previous = {"darVersions": versions, "darGovernance": state}
    saved = deepcopy(previous)
    retained, stale = governance.unavailable_dar_governance(ENDPOINT, previous)
    assert retained == versions
    assert stale["status"] == "stale"
    assert stale["proposals"] == state["proposals"]
    assert previous == saved
    # The old release-lock-derived versions must not be relabeled as active.
    assert (
        governance.unavailable_dar_governance(ENDPOINT, {"darVersions": versions})[0]
        == []
    )
    assert (
        governance.unavailable_dar_governance("other.example.com", previous)[1][
            "status"
        ]
        == "unavailable"
    )


def test_incomplete_vote_response_is_not_treated_as_no_pending_votes():
    with pytest.raises(RuntimeError, match="dso_rules_vote_requests"):
        governance.read_governance(dso_response(), {}, NOW)


def test_invalid_schedule_time_fails_instead_of_activating_future_version():
    dso = dso_response()
    dso["amulet_rules"]["contract"]["payload"]["configSchedule"]["futureValues"] = [
        {"_1": "no-date", "_2": config("0.1.2")}
    ]
    with pytest.raises(RuntimeError, match="timestamp"):
        governance.read_governance(dso, votes(), NOW)


def test_votes_outage_does_not_hide_active_versions_or_imply_no_proposals():
    def fetch(url, timeout):
        if url.endswith("voterequests"):
            raise RuntimeError("unavailable")
        return dso_response()

    versions, state = governance.collect_dar_governance(ENDPOINT, 1, NOW, fetch)
    assert len(versions) == 6
    assert state["status"] == "current"
    assert state["votesStatus"] == "unavailable"


def test_each_network_fetches_its_own_six_versions_and_can_refresh():
    for network, minor in [("mainnet", 1), ("testnet", 2), ("devnet", 3)]:
        endpoint = f"scan.{network}.example.com"
        for patch in [1, 2]:

            def fetch(url, timeout):
                assert url.startswith(f"https://{endpoint}/api/scan/")
                if url.endswith("voterequests"):
                    return votes()
                payload = dso_response()
                payload["amulet_rules"]["contract"]["payload"]["configSchedule"][
                    "initialValue"
                ] = config(f"0.{minor}.{patch}")
                return payload

            versions, state = governance.collect_dar_governance(endpoint, 1, NOW, fetch)
            assert len(versions) == 6
            assert {row["version"] for row in versions} == {f"0.{minor}.{patch}"}
            assert state["status"] == state["votesStatus"] == "current"
