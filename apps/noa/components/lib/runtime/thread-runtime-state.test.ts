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
        isRunning: false,
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
        isRunning: false,
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
        isRunning: false,
      }).shouldReplaceRoute,
    ).toBe(true);
  });

  it("defers route replacement while the thread is still running", () => {
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
        isRunning: true,
      }).shouldReplaceRoute,
    ).toBe(false);
  });

  it("stops hydrating once the remote thread has been restored", () => {
    expect(
      getThreadRuntimeState({
        remoteId: "thread-2",
        messageCount: 0,
        hydratedRemoteId: "thread-2",
        hydrationInFlightRemoteId: null,
        attemptedRemoteId: "thread-2",
        attemptedRetryVersion: 0,
        retryVersion: 0,
        pathname: "/assistant/thread-2",
        lastRoutedRemoteId: null,
        hasRenderedMessage: false,
        isRunning: false,
      }),
    ).toMatchObject({
      shouldHydrate: false,
      isHydrating: false,
    });
  });

  it("hides the empty state while a persisted thread is hydrating", () => {
    expect(shouldShowThreadEmptyState({ isHydrating: true, hasMessages: false })).toBe(false);
    expect(shouldShowThreadEmptyState({ isHydrating: false, hasMessages: false })).toBe(true);
  });
});
