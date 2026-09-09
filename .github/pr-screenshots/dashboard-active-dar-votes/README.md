# Dashboard governance review

- `scan-unavailable.png` shows the actual generated output from a live run: all three configured Scan endpoints returned HTTP 403. No release-lock values are relabeled as active.
- `governance-test-data.png` uses synthetic inputs matching `tests/test_network_dar_governance.py`. MainNet demonstrates an open vote; TestNet demonstrates a vote at the approval threshold; DevNet demonstrates an approved future schedule. The versions and dates in this screenshot are test data.
- `mobile-test-data.png` shows the same synthetic open-vote scenario at a 390px viewport width.

The fixture data was used only for local visual validation and was removed from the generated config and snippet before committing. Successful live Scan collection must be verified before merging.
