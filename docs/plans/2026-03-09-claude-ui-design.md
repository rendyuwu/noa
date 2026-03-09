# Claude-Style UI Redesign (Assistant UI Claude Clone)

**Date:** 2026-03-09

## Context

The current web UI (especially `/assistant`) is functionally correct but visually minimal and does not resemble assistant-ui's production examples. The goal is to ship a Claude-like experience that matches the assistant-ui Claude example as closely as possible.

Reference:
- https://www.assistant-ui.com/examples/claude

## Goals

- Make the UI feel like Claude (layout, typography, spacing, color, shadows, motion) with best-effort parity to the reference.
- Keep existing backend/runtime behavior (threads, streaming, tool calls) while upgrading presentation.
- Ensure responsive behavior (desktop and mobile) and production-grade UI polish.
- Add a frontend `/api/*` proxy so the browser only talks to same-origin endpoints.

## Non-Goals (for this milestone)

- Implement true message Edit or Reload (regenerate) behavior.
- Implement real model switching.
- Implement attachment upload + server-side processing (beyond basic client-side image attachments).

These affordances may appear in the UI to match Claude, but they must be disabled and documented clearly.

## Scope

- **/assistant**: Full Claude-style assistant workspace (sidebar thread list + chat + composer).
- **/login** and **/admin**: Restyle to match the same visual language (warm palette, typography, buttons).
- **/api proxy**: Route all frontend requests through Next.js route handlers.

## Design Decisions

### 1) Option 1: Claude example as source of truth

We will port the assistant-ui Claude clone structure and styling patterns and adapt them to our app's primitives + thread list integration.

Rationale:
- Most reliable path to visual parity.
- Avoids fighting the default look of the prebuilt `Thread`/`ThreadListSidebar` components.

### 2) Styling foundation: Tailwind + Claude tokens

- Add Tailwind CSS to `apps/web`.
- Define Claude-like tokens for background/surfaces/borders/shadows/typography.
- Default to warm light background (like the reference) and support a dark variant.

### 3) Claude-like assistant workspace composition

- Use assistant-ui primitives (`ThreadPrimitive`, `MessagePrimitive`, `ComposerPrimitive`, `ThreadListPrimitive`) but with the reference component structure and class names.
- Keep runtime transport + thread list persistence unchanged.
- Tool UIs:
  - Keep the existing `request_approval` tool UI but restyle it to match Claude.
  - Provide a general fallback UI for any other tools.

### 4) Disabled controls (ship look first)

To match Claude, we will render UI affordances for features we do not yet support, but they must be disabled with clear UX:

- **Edit** (disabled): Our runtime/backend does not support editing and resubmitting messages yet.
- **Reload / Regenerate** (disabled): Our backend does not support regenerating from a message source id yet.

Documentation requirements:
- Explain what is disabled, why, and what would be required to enable it (backend semantics + runtime integration).

### 5) /api proxy to avoid direct backend calls

Add a catch-all Next.js route handler at `/api/[...path]` that forwards requests to the FastAPI backend.

Benefits:
- Browser never calls `http://backend-host/...` directly.
- Simplifies CORS and local dev.
- Central place to add auth header forwarding, logging, and later rate limiting.

Config:
- `NOA_API_URL` (server-only) points to the backend origin, e.g. `http://localhost:8000`.
- Frontend code uses `"/api"` as the base URL.

Streaming:
- The proxy must return `Response(upstream.body, ...)` to preserve streaming from `/assistant`.

## Acceptance Criteria

- `/assistant` matches the assistant-ui Claude example styling closely (best effort), including spacing/typography/surfaces.
- `/login` and `/admin` match the same visual system.
- Network requests from the browser go to same-origin `/api/*` only.
- Streaming responses still work through the proxy.
- Edit/Reload affordances are disabled and documented.

## Follow-ups (post-milestone)

- Enable real Edit and Reload by defining backend behavior and wiring runtime capabilities.
- Add real attachment processing (upload to backend, pass to model, persist).
- Add ModelSelector support and backend model routing.
- Add feedback endpoint and wire `FeedbackAdapter` to persist telemetry.
