export class StaleRequestError extends Error {
  constructor() { super('Request belongs to an earlier session'); }
}

/** A session boundary aborts I/O and invalidates already-buffered responses too. */
export class RequestScope {
  private generation = 0;
  private controllers = new Set<AbortController>();
  get version(): number { return this.generation; }
  isCurrent(version: number): boolean { return version === this.generation; }
  invalidate(): void {
    this.generation += 1;
    this.controllers.forEach(controller => controller.abort());
    this.controllers.clear();
  }
  begin() {
    const version = this.generation;
    const controller = new AbortController();
    this.controllers.add(controller);
    return {
      signal: controller.signal,
      current: () => this.isCurrent(version),
      check: () => { if (!this.isCurrent(version)) throw new StaleRequestError(); },
      release: () => { this.controllers.delete(controller); },
    };
  }
}
