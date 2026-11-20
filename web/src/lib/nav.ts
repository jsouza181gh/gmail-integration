// Full-page navigation, isolated so tests can observe it (jsdom cannot navigate).
export function navigateTo(url: string): void {
  window.location.assign(url)
}
