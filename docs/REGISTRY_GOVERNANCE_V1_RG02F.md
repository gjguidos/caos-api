# Registry Governance V1 — RG-02F safe client projections

RG-00, RG-01, RG-02, RG-02D, RG-02E and RG-02F are included in this milestone. **Registry Governance V1 is not fully complete: RG-03 remains unfinished and browser E2E verification is pending.** Earlier reports are historical snapshots.

## Projection boundary

- Client registry reads use typed, allowlisted projections after ownership, exposure and dependency validation. Trusted runtime manifests remain unchanged.
- Internal dependencies may satisfy composition without being disclosed. Missing, unclassified and foreign-tenant requirements still fail closed. Projection grants no attachment or invocation authority.
- Personality responses omit security/activation metadata and unreviewed extensions. Persisted workflow authoring responses are narrowed only after full read authorization; mutation responses are validated before writes.

## Crawler compatibility

The final projection retains visible controls, output profiles, feature panels, search inference and the approved `POST /v1/operations/{run_id}/cancel` contract. References to hidden controls and unreviewed cancellation endpoints are filtered. Regression tests cover real crawler metadata and adversarial inputs.

The crawler tool projection is supported, but the crawler role and crawl workflow still fail closed when the required `artifact_builder` provider is absent. This commit does not add that provider or claim full operational crawler availability.

## Verification

Previously completed verification was rerun successfully in the existing API container during commit review:

- 33 manifest projection tests passed.
- 390 service and registry tests passed.
- 9 RG-02F smoke checks passed. These isolated projection checks do not establish end-to-end API authorization or browser compatibility.

The smoke script has an environment-specific import-path default; pass `--module /app/src/domains/agentverse/security/manifest_projection.py` in the API container. Browser E2E verification remains pending. Administrative inventory, temporary exports, snapshots and the continuation patch are excluded from the commit.
