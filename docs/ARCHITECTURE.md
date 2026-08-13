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
    G --> D[Public release assets]
    M --> A[Home Assistant app repository]
    A --> B[FlexDisplay Bridge and Studio]
    B --> X[X3 and X4]
    B --> N[Note 4]
    B --> S[Echo Spot receiver]
    B --> F[FlexHub API]
```

## Authority rules

1. Code, issues, pull requests, reviews, CI, tags, and release notes originate
   in Forgejo.
2. GitHub accepts only the Forgejo push mirror and release automation.
3. Home Assistant may install the Bridge app directly from Forgejo.
4. HACS continues to install the integration from the GitHub mirror.
5. Runtime configuration, credentials, device identities, backups, and local
   network secrets are never committed.

## Version boundaries

The Bridge, Studio, and integration share one platform version because Studio
is shipped inside the Bridge and the integration calls the Bridge API. The
Echo Spot receiver, FlexHub firmware, and X3/X4 firmware use independent
versions recorded in `docs/COMPATIBILITY.md`.
