import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import '@testing-library/jest-dom/vitest'

// jsdom ships no matchMedia; the BIGSU AppShell reads it via useMediaQuery for
// its responsive chrome. Provide the standard no-op stub so shell-based
// components can render under test.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// jsdom ships no ResizeObserver; cmdk (behind the BIGSU CommandSearch palette)
// observes its list to size it. Provide a no-op stub so the palette can mount.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}

// jsdom stubs no scrollIntoView; cmdk calls it when it selects the active item.
if (typeof Element !== 'undefined' && typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = () => {}
}

afterEach(() => {
  cleanup()
})
