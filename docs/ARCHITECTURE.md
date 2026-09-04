# FlexDisplay architecture

Forgejo is the authoritative forge. For this repository only, GitHub is an
approved downstream compatibility surface because HACS only consumes public
GitHub repositories, existing Home Assistant consumers may already use the
GitHub app-source URL, and some public release assets may be replicated there
after Forgejo publication.

```mermaid
flowchart LR
    C[Codex worktree] --> P[Forgejo pull request]
    P --> M[Forgejo main]
    M --> R[Forgejo release]
    M --> G[One-way GitHub mirror]
    G --> H[HACS integration]
    R --> V[Verified post-publication handoff]
    V --> D[GitHub compatibility release]
    G --> D
    M --> A[Home Assistant app repository]
    A --> B[FlexDisplay Bridge and Studio]
    B --> X[X3 and X4]
    B --> N[Note 4]
    B -. architecture candidate .-> T[TOP52810 stock BLE tag]
    B --> S[Echo Spot receiver]
    B --> F[FlexHub API]
```

## Authority rules

1. Code, issues, pull requests, reviews, CI, tags, and release notes originate
   in Forgejo.
2. For this repository only, GitHub accepts the Forgejo push mirror, gated
   compatibility-release automation after the Forgejo release succeeds, and
   private Security Advisories. This exception supports HACS, existing Home
   Assistant consumers, and public assets; it is not precedent for another
   repository. A mirrored tag alone is not release authorization.
3. Home Assistant may install the Bridge app directly from Forgejo.
4. HACS continues to install the integration from the GitHub mirror.
5. Runtime configuration, credentials, device identities, backups, and local
   network secrets are never committed.

## Version boundaries

The Bridge, Studio, and integration share one platform version because Studio
is shipped inside the Bridge and the integration calls the Bridge API. The
Echo Spot receiver, FlexHub firmware, and X3/X4 firmware use independent
versions recorded in `docs/COMPATIBILITY.md`.

`release-manifest.json` is the coordinated release source of truth. Runtime
package markers, the compatibility matrix, Android source metadata, packaged
artifact defaults, and generated `docs/RELEASE_STATUS.md` must agree with it.
The distribution entries remain separate because a commit, protected tag,
Forgejo release, GitHub compatibility release, Home Assistant deployment,
Android installation, firmware rollout and physical result are different
states with different evidence.

## Bridge module boundaries

`flexdisplay_bridge.app.create_app()` is the composition root. It owns service
construction, lifecycle tasks, shared authorization and compatibility state;
HTTP concerns move behind router factories under `flexdisplay_bridge.api`.
Router dependencies are passed explicitly rather than imported from a global
application instance, while the existing `app.state` service aliases remain a
supported inspection and test seam.

Phase 1 extracts the complete FlexHub and Meshtastic HTTP surface into
`flexdisplay_bridge.api.flexhub`. Its public paths, authorization boundary,
response contracts and external FlexHub ownership remain unchanged. Later
phases can move devices, Android receivers, Studio/content, fleet, firmware and
diagnostics in bounded slices using the same composition pattern. A concern is
only considered extracted when its route inventory and behavior tests move
with it; creating pass-through wrapper modules does not count as
modularisation.

## Compact stock BLE tags

The TOP52810M-D01 stock-firmware candidate is a distinct compact e-paper tag
family, not an OpenDisplay receiver. Its proposed ownership, stable-identity
gate, black/white/red rendering limitations, periodic BLE delivery contract,
security boundary and recovery behavior are defined in
[`TOP52810_STOCK_BLE_ARCHITECTURE.md`](TOP52810_STOCK_BLE_ARCHITECTURE.md).
Until those admission gates are complete, the family remains external and
read-only and receives no upload, firmware, provisioning, policy or reset
surface.
