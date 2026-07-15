# How Agent Rails Works

[简体中文](./how-agent-rails-works.zh-CN.md) | [English](./how-agent-rails-works.en.md)

Agent Rails is not another coding agent and does not take ownership of a business repository. It is a personal workflow guardrail between an agent and a target project: it makes the target, scope, and evidence explicit before work starts, then makes verification, commit scope, and release scope explicit before delivery.

This document explains the complete operating model. See the [English CLI Reference](./cli-reference.en.md) for commands and [Design and Safety Boundaries](./local-adapters-and-release-safety.md) for individual design decisions.

## Core Design

Agent Rails follows five principles:

1. **Separate the kit from the target project.** The kit supplies reusable capabilities; a business repository is only the Target Project being read or changed.
2. **Separate stable rules from task evidence.** SessionStart injects short, stable routing rules. An on-demand Task Pack carries branch, diff, documentation, and memory evidence.
3. **Resolve target identity before doing task work.** Every entrypoint first resolves the exact project root, Profile, worktree identity, and Task Pack path, preventing context from leaking across repositories or worktrees.
4. **Require ownership for generated artifacts.** An Adapter refreshes or removes only files and skills that Agent Rails can prove it manages. Tracked and user-authored content is preserved by default.
5. **Verify before publishing or switching versions.** Git scope, sensitive output, Release validation, and atomic switching all use fail-closed semantics.

## System Architecture

```mermaid
flowchart LR
    USER["User"] --> CLI["CLI Facades<br/>setup · run · verify"]
    AGENT["Coding Agent<br/>Claude · Codex · OpenCode"]

    subgraph KIT["Agent Rails Kit"]
        CTX["Target Project Context"]
        ADAPTER["Adapter Content and<br/>Managed Adapter Workspace"]
        PACK["Task Pack Generator"]
        CHECK["Agent Check and<br/>Publish Check"]
        RELEASE["Release Builder and<br/>Release Installer"]
    end

    subgraph TARGET["Target Project"]
        ROOT["Exact Git worktree root"]
        PROFILE["Resolved Profile"]
        LOCAL["Local Adapter<br/>guide · commands · skills"]
    end

    subgraph STATE["Personal State"]
        CONFIG["~/.agent-rails<br/>profiles · task packs · memory"]
        BIN["Stable CLI path<br/>~/.local/bin/agent-rails"]
    end

    CLI --> CTX
    CTX --> ROOT
    CTX --> PROFILE
    CONFIG --> CTX
    CLI --> ADAPTER
    ADAPTER --> LOCAL
    LOCAL --> AGENT
    AGENT --> PACK
    PACK --> ROOT
    PACK --> CONFIG
    AGENT --> CHECK
    CHECK --> ROOT
    RELEASE --> BIN
    BIN --> CLI
```

| Layer | Owns | Does not own |
| --- | --- | --- |
| CLI facades | Orchestrate `setup`, `run`, and `verify` over existing modules | Duplicated domain implementations |
| Target Project Context | Canonical target root, resolved Profile, project name, worktree slug, and Task Pack path | Automatic Profile propagation to another repository |
| Adapter modules | Tool-specific entrypoints, managed files, skill inventories, and local ignores | Content whose ownership cannot be proven |
| Task Pack | Budgeted target, Git, documentation, memory, and verification evidence | Unbounded repository or sensitive context in a prompt |
| Check / Publish Check | Verification selection, commit and publish scope, and likely-secret findings | Committing, pushing, or publishing on the user's behalf |
| Release modules | Complete kit bundles, validation, and installed-version switching | Target Project or Adapter ownership rules |

## Lifecycle of One Task

```mermaid
sequenceDiagram
    actor U as User
    participant A as Coding Agent
    participant H as SessionStart Hook
    participant C as Agent Rails CLI
    participant T as Target Project
    participant P as Task Pack

    U->>A: Start a session in the target project
    H-->>A: Inject stable marker, trigger matrix, and scope rules
    A->>C: pack --project exact-root task-goal
    C->>T: Resolve Context, Profile, and Git Scope
    C->>T: Read budgeted diff, entry docs, and verification config
    C-->>P: Write a 0600 staging file, then replace atomically
    P-->>A: Return current-branch evidence and delivery checklist
    A->>T: Read, change, and test
    A->>C: verify or check
    C->>T: Select and run the Verification Plan
    opt Preparing a commit or release
        A->>C: publish check --base deployed-revision
        C-->>A: Return scope, baseline, and redacted secret findings
    end
    A-->>U: Report changes, verification, gaps, and residual risks
```

A full context is not generated at startup because a session may perform only a fixed read operation. The agent chooses the smallest useful path for the task:

| Path | Use it for | Output |
| --- | --- | --- |
| Deep Pack | Cross-subproject, contract or schema changes, migrations, refactors, ambiguous product work | Full Task Pack with bounded evidence |
| Lite Pack | POCs, deployment preparation, focused continuation | More compact Task Pack |
| Check-only | Releasing, uploading, or final validation from an existing branch | Verification Plan and scope report |
| Skip | Fixed read-only work with no repository-scope risk | Explicit skip reason |

This split keeps SessionStart stable and lets a Task Pack be regenerated whenever the target, branch, or goal changes, without trusting stale session memory.

## Target Project and Profile Isolation

Target Project Context is the common starting point for the main entrypoints. It canonicalizes any nested directory to the exact Git worktree root, then resolves a Profile in a fixed order.

```mermaid
flowchart TD
    INPUT["--project path or current directory"] --> ROOT["Canonicalize the exact Target Project root"]
    ROOT --> EXPLICIT{"Was --profile passed explicitly?"}
    EXPLICIT -->|Yes| USE_EXPLICIT["Use explicit Profile<br/>with legacy deleted-kit-Profile fallback"]
    EXPLICIT -->|No| PROJECT{"Does the project contain a Profile?"}
    PROJECT -->|Yes| USE_PROJECT[".agent-rails/profile<br/>or profile.sh"]
    PROJECT -->|No| USER{"Does a user project Profile exist?"}
    USER -->|Yes| USE_USER["~/.agent-rails/profiles/projects/{name}.profile<br/>or legacy profiles/{name}.profile"]
    USER -->|No| DEFAULT["Use kit profiles/default.profile"]

    USE_EXPLICIT --> FINAL["Load Profile, then derive project name, worktree slug, and Task Pack path"]
    USE_PROJECT --> FINAL
    USE_USER --> FINAL
    DEFAULT --> FINAL
```

Target changes follow a separate isolation rule:

```mermaid
flowchart TD
    CHANGE["Continue the task in another directory"] --> SAME_ROOT{"Is it still the same exact root?"}
    SAME_ROOT -->|Yes| KEEP["Keep the resolved Context"]
    SAME_ROOT -->|No| SAME_REPO{"Is it another worktree of the same repository?"}
    SAME_REPO -->|Yes| WORKTREE["Pass the new exact worktree root<br/>The repository Profile may be reused"]
    SAME_REPO -->|No| SIBLING["Resolve a Profile for the new repository<br/>Never inherit the source repository Profile"]
    WORKTREE --> REPACK["Regenerate the Task Pack<br/>Verify Current Git State"]
    SIBLING --> REPACK
```

A directory basename cannot prove repository identity. Isolation therefore depends on an explicit root and the resolution flow, not a simple folder-name comparison.

## Adapter Ownership Model

Adapters connect Agent Rails to different coding-agent tools. The default `local` mode is personal: it uses the target repository's `.git/info/exclude` and does not modify the team's `.gitignore`. After validation, explicit `project` mode promotes the same managed artifacts into portable, committable team files.

```mermaid
flowchart TD
    ACTION["Install, refresh, or uninstall an Adapter"] --> CLASSIFY{"Which class owns the target path?"}
    CLASSIFY -->|Agent Rails marker or compatible legacy signature| MANAGED["Managed generated file<br/>May be refreshed or removed"]
    CLASSIFY -->|Skill name is in the exact inventory| SKILL["Managed skill<br/>May be refreshed or removed"]
    CLASSIFY -->|Git tracked| TRACKED["Preserve"]
    CLASSIFY -->|User-authored same-path content| USER_FILE["Preserve"]
    CLASSIFY -->|New unoccupied path| NEW["Write generated content with an ownership marker"]
    MANAGED --> MODE{"Adapter mode?"}
    SKILL --> MODE
    NEW --> MODE
    MODE -->|local default| LOCAL_IGNORE["Maintain an idempotent local-ignore block<br/>Invisible to collaborators"]
    MODE -->|explicit project| PROJECT_FILES["Remove the managed ignore block<br/>Write committable files without personal paths"]
```

`local → project` is the formal promotion path; switching back restores local ignores. `--force` is an explicit repair choice, not the automatic update default. `update` follows the ownership-aware refresh path; `doctor --fix` can repair damaged managed content.

## Git Scope, Verification, and Publish Evidence

`pack`, `check`, and `publish check` share Git Scope resolution so the three entrypoints do not develop different meanings for “what changed.”

```mermaid
flowchart LR
    TARGET["Target ref"] --> RESOLVE["Validate commit refs"]
    BASE["Explicit or policy-selected Base ref"] --> RESOLVE
    RESOLVE --> MERGE["Compute merge base"]
    MERGE --> COMMITTED["Committed paths<br/>merge-base...target"]
    WORKTREE["Optional working-tree status"] --> SNAPSHOT["Worktree paths"]
    COMMITTED --> UNION["Unified Changed Paths Snapshot"]
    SNAPSHOT --> UNION
    UNION --> PACK["Task Pack<br/>Evidence selection and ranking"]
    UNION --> CHECK["Agent Check<br/>Verification Plan"]
    UNION --> PUBLISH["Publish Check<br/>Commit scope and secret scan"]
```

Project checks try `origin/main`, `origin/master`, `main`, and `master` in order. Publish checks additionally prefer the current upstream, but an upstream is only a source-control baseline; it does not prove what is deployed. When a deployment delta cannot be established, the report marks `Deployment delta: UNRESOLVED` and requires the currently deployed revision explicitly.

Sensitive-output behavior is also purpose-specific. Task Packs favor conservative redaction. Publish Check scans only added committed, staged, and unstaged lines plus complete untracked text, then reports only the location and evidence needed for a decision. Base64 and URL encoding are never treated as redaction.

## How GitHub Release Updates Work

Agent Rails is a multi-file shell kit, so a Release distributes a complete archive instead of a wrapper that still depends on a source directory. The CLI distinguishes a Git checkout from a Release Install using its own resolved location.

```mermaid
flowchart TD
    UPDATE["agent-rails upgrade self or update"] --> SOURCE{"Is the current kit a Git checkout?"}
    SOURCE -->|Yes| CLEAN{"Is the worktree clean?"}
    CLEAN -->|No| STOP_GIT["Stop: commit, stash, or explicitly skip pull"]
    CLEAN -->|Yes| PULL["git pull --ff-only"]

    SOURCE -->|No| DOWNLOAD["Download fixed-name archive and SHA-256"]
    DOWNLOAD --> VERIFY["Verify digest, single top directory, paths, and VERSION"]
    VERIFY --> STAGE["Write releases/{version}"]
    STAGE --> SWITCH["Atomically replace current and stable CLI symlinks"]
    SWITCH --> REEXEC["Re-execute update from the new version when needed"]

    PULL --> TESTS["Run source-checkout tests"]
    REEXEC --> SKIP_TESTS["Skip source-checkout-only tests"]
    TESTS --> SELF{"Kit-only upgrade?"}
    SKIP_TESTS --> SELF
    SELF -->|Yes| DONE["Done; keep old version directories for rollback"]
    SELF -->|No| TOOL{"Explicit --tool and --mode"}
    TOOL -->|claude| CLAUDE["Claude Doctor → Install → Final Doctor"]
    TOOL -->|codex| CODEX["Codex Doctor → Install → Final Doctor"]
    TOOL -->|opencode| OPENCODE["OpenCode Doctor → Install → Final Doctor"]
    CLAUDE --> DONE
    CODEX --> DONE
    OPENCODE --> DONE
```

The Release Installer finishes downloading and validating before it switches anything. Any checksum, archive-layout, version, or user-owned non-symlink path error stops the operation. Project maintenance requires one explicit tool, so a historical default cannot refresh the wrong Adapter. See [GitHub Release Distribution](./github-release-distribution.md) for the full asset and rollback contract.

## Consistency and Failure Semantics

| Situation | Guarantee |
| --- | --- |
| Target path is missing or its Profile cannot load | Fail before reading project evidence |
| Target/base ref is invalid or has no merge base | Do not produce a misleading empty-scope report |
| Task Pack rendering fails | Preserve the old file and print no success; staging permissions are `0600` |
| Adapter cannot prove file ownership | Preserve the file by default |
| Publish baseline cannot represent deployed state | Mark it `UNRESOLVED`; do not claim release readiness |
| Release checksum, structure, or version does not match | Do not switch `current` |
| Stable CLI or `current` is a regular file | Treat it as user-owned and refuse replacement |

Together, these rules enforce one outcome: Agent Rails may do less, but it must not present incomplete evidence as safe completion.

## Explicit Non-Goals

- It does not commit, push, merge, or create a Release on the user's behalf.
- It does not commit Agent Rails files to a business repository by default.
- It does not store access keys, cookies, or tokens in the kit.
- It does not require online memory; online memory is only an optional read provider for Task Packs.
- It does not guess sibling-repository configuration from the current session's Profile.
- It does not describe SHA-256 as signing or supply-chain attestation.

## Code Map

| Concern | Implementation entrypoint |
| --- | --- |
| CLI routing | [`bin/agent-rails`](../bin/agent-rails) |
| Target Project Context | [`scripts/agent-target-project.sh`](../scripts/agent-target-project.sh) |
| Profiles and personal paths | [`scripts/agent-paths.sh`](../scripts/agent-paths.sh) |
| SessionStart | [`hooks/agent-rails-session-start.sh`](../hooks/agent-rails-session-start.sh) |
| Task Pack | [`scripts/agent-context-pack.sh`](../scripts/agent-context-pack.sh) |
| Git Scope | [`scripts/agent-git-scope.sh`](../scripts/agent-git-scope.sh) |
| Verification and publish check | [`scripts/agent-check.sh`](../scripts/agent-check.sh), [`scripts/agent-publish-check.sh`](../scripts/agent-publish-check.sh) |
| Adapter ownership | [`scripts/agent-adapter-workspace.sh`](../scripts/agent-adapter-workspace.sh) |
| Update and Release install | [`scripts/agent-update.sh`](../scripts/agent-update.sh), [`scripts/agent-release-install.sh`](../scripts/agent-release-install.sh) |
