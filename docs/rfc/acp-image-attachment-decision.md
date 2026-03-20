# ACP image and attachment support decision

## Status

Draft

## Purpose

Make an explicit product and architecture decision about how image inputs and attachments should work in an ACP-first Artel.

This document exists because current Artel surfaces are inconsistent:

- local TUI supports image attachments
- remote TUI supports image attachments
- ACP currently does not provide equivalent first-class behavior

If Artel wants an ACP-native architecture, this gap must be resolved intentionally.

## Current state

## Local TUI

Local TUI supports image-oriented flows such as:

- `/image <path>`
- `/image-paste`
- queued pending attachments
- passing attachments into `session.run(...)`

## Remote TUI

Remote TUI also supports attachments in its custom WebSocket message payloads.

## ACP

Current ACP implementation:

- advertises `embedded_context=True`
- does **not** advertise image support
- converts incoming prompt content primarily through text/resource extraction
- does not treat ACP image blocks as first-class turn input

So ACP currently has no parity with the first-party TUI image workflow.

## Decision

### Decision summary

Artel should support **image prompt input over ACP as a first-class session capability**.

In practical terms:

- ACP should advertise image support when the runtime can accept image prompt input
- ACP-backed sessions should accept image content blocks as user turn input
- image-bearing turns should participate in the same session persistence and replay model as other turns, within practical limits
- clients should not have to emulate image input by hiding it behind slash commands or UI-only hacks

## Why this is the right decision

### 1. ACP-first without image parity is strategically weak

If local and remote first-party clients support image input but ACP does not, then ACP cannot honestly be called the canonical runtime contract for normal interactive sessions.

### 2. ACP already has the right conceptual shape

The ACP schema includes image content blocks. This is not an alien capability being forced into the protocol.

### 3. Artel already has the runtime concept of attachments

The product already knows what an image attachment is. The work is mostly in protocol mapping, validation, persistence behavior, and replay policy.

## Non-goals

This decision does not require:

- perfect parity for every clipboard/UI affordance across all clients
- immediate support for every non-image attachment type
- embedding full binary blobs into Artel persistence if a lighter reference model is better
- making every client render images identically

The goal is **runtime parity**, not identical UI affordances.

## Proposed model

## 1. ACP input model

Artel ACP should accept image-bearing prompts through ACP content blocks.

Preferred semantics:

- text blocks remain normal textual user input
- image blocks are treated as attachments associated with the same user turn
- resource blocks may continue to represent referenced context, but should not be the only path to non-text input

## 2. Runtime normalization model

At the runtime boundary, Artel should normalize a prompt into something like:

- textual prompt body
- zero or more image attachments
- zero or more embedded/resource context items

This is the model the rest of Artel should consume, regardless of transport.

## 3. Capability advertisement model

ACP capability advertisement should be accurate and dynamic enough for client trust.

Minimum expectation:

- if the ACP runtime can consume image prompt input, advertise `PromptCapabilities.image = true`
- if it cannot, advertise `false`

Longer term, capability signaling may also need to clarify whether:

- only local file/image URI references are supported
- inline image data is supported
- replay/load can surface image-bearing history meaningfully

## 4. Persistence model

The persistence goal is not necessarily "store every raw image blob forever".

The persistence goal is:

- keep session behavior coherent across resume/load/replay
- preserve enough attachment metadata to reconstruct user turns meaningfully

Recommended direction:

- persist image attachment references/metadata in session history
- avoid inflating the session store with raw binary payloads if not required
- support replay in a degraded but principled way when original image material is unavailable

## 5. Replay model

Replay/load/resume should be explicit about what is restored.

Preferred behavior:

- if attachment metadata is available, replay the user turn as text plus attachment summary/metadata
- if a client can render attachment references, expose them
- if original image data is unavailable, avoid pretending it still exists silently

## Open design choices

## Choice A — inline image bytes vs referenced images

Possible options:

### Option A1 — accept only referenced images

Examples:

- local file paths
- resource URIs resolvable by the client/runtime

Pros:

- simpler persistence story
- avoids large payload handling complexity
- aligns well with local coding workflows

Cons:

- weaker portability across machines/clients
- less ideal for editor integrations that provide inline image data

### Option A2 — accept inline image data and normalize to temporary/runtime-managed files

Pros:

- better interoperability with ACP clients
- closer to generic content-block semantics

Cons:

- more implementation complexity
- needs lifecycle rules for temporary artifacts
- potentially messy persistence semantics

### Recommendation

Start with a pragmatic split:

- support referenced/local-path image flows first if that is the easiest path to parity
- design the normalization layer so inline data can be added later without redesigning the whole model

## Choice B — how to replay image-bearing history

Possible options:

### Option B1 — replay only text and omit image markers

This is the weakest option and should be avoided.

### Option B2 — replay text plus attachment metadata summaries

Example:

- image name
- mime type
- path or URI if still meaningful

This is the preferred baseline.

### Option B3 — replay full image content where available

Nice to have, but not required for the first ACP-first convergence step.

## Choice C — model gating vs model capability

Not every model supports vision.

Artel must distinguish between:

- ACP transport/runtime support for image input
- current model support for actually consuming image input

Recommended behavior:

- ACP capability means the runtime/session surface can accept image-bearing turns
- if the active model cannot use images, Artel should reject the turn clearly or require model switching
- clients should not be forced to infer this only after failure

Longer term, model/session config state may need richer capability metadata.

## Recommended implementation policy

### Policy 1

Artel ACP should treat image input as part of the normal turn model, not as a side-channel command.

### Policy 2

Attachment metadata should be persistable and replayable even when raw image bytes are not.

### Policy 3

UI affordances may differ by client, but runtime semantics should not.

Examples:

- TUI may still support `/image-paste`
- an editor ACP client may expose an image picker
- both should normalize into the same runtime turn model

### Policy 4

If image support is partial during migration, docs must describe the exact limitation.

## Implementation backlog implications

This decision implies concrete work in at least these areas:

- ACP prompt parsing and normalization
- ACP capability advertisement
- session persistence model for attachment metadata
- replay/rendering behavior for image-bearing history
- tests for ACP image-bearing prompts
- docs updates for ACP and run-mode expectations

## Suggested first implementation slice

A realistic first slice is:

1. teach ACP prompt ingestion to detect image content blocks
2. normalize them into Artel image attachments for the turn runtime
3. advertise `PromptCapabilities.image = true` only when this path is supported
4. persist attachment metadata for the resulting user message
5. add targeted tests for:
   - image-bearing prompt acceptance
   - model rejection when vision is unavailable
   - replay/load behavior with attachment metadata

## What this decision does not settle

This document does not finalize:

- exact ACP block forms Artel will accept first
- whether inline image bytes are supported in phase 1
- exact database schema changes for attachment metadata replay
- whether non-image attachments should follow immediately after image support

Those are implementation decisions that should follow this policy choice.

## Bottom line

If Artel wants to be ACP-first in a meaningful sense, ACP must support the same category of user turns that first-party Artel clients support.

That means image-bearing turns should become a first-class ACP capability, even if the first implementation is conservative about storage and replay details.
