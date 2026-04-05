import { describe, expect, it } from "vitest";

import { getThreadRuntimeState, shouldShowThreadEmptyState } from "./thread-runtime-state";

describe("thread runtime state", () => {
  it("hydrates a remote thread when it has no messages and was not yet loaded", () => {
    expect(
      getThreadRuntimeState({
        remoteId: "thread-1",
        messageCount: 0,
        hydratedRemoteId: null,
        hydrationInFlightRemoteId: null,
        attemptedRemoteId: null,
        attemptedRetryVersion: -1,
        retryVersion: 0,
        pathname: "/assistant/thread-1",
        lastRoutedRemoteId: null,
        hasRenderedMessage: false,
      }),
    ).toMatchObject({
      shouldHydrate: true,
      isHydrating: true,
      shouldReplaceRoute: false,
      desiredPath: "/assistant/thread-1",
    });
  });

  it("does not replace /assistant until the new thread has rendered a message", () => {
    expect(
      getThreadRuntimeState({
        remoteId: "thread-2",
        messageCount: 0,
        hydratedRemoteId: null,
        hydrationInFlightRemoteId: null,
        attemptedRemoteId: null,
        attemptedRetryVersion: -1,
        retryVersion: 0,
        pathname: "/assistant",
        lastRoutedRemoteId: null,
        hasRenderedMessage: false,
      }).shouldReplaceRoute,
    ).toBe(false);
  });

  it("replaces /assistant once the new thread has rendered a message", () => {
    expect(
      getThreadRuntimeState({
        remoteId: "thread-2",
        messageCount: 1,
        hydratedRemoteId: null,
        hydrationInFlightRemoteId: null,
        attemptedRemoteId: null,
        attemptedRetryVersion: -1,
        retryVersion: 0,
        pathname: "/assistant",
        lastRoutedRemoteId: null,
        hasRenderedMessage: true,
      }).shouldReplaceRoute,
    ).toBe(true);
  });

  it("hides the empty state while a persisted thread is hydrating", () => {
    expect(shouldShowThreadEmptyState({ isHydrating: true, hasMessages: false })).toBe(false);
    expect(shouldShowThreadEmptyState({ isHydrating: false, hasMessages: false })).toBe(true);
  });
});
