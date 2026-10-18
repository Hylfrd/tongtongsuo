import './ambient.css';

/** Decorative motion is independent of form state and never moves page content. */
export function installAmbient(root: HTMLElement): () => void {
  const backdrop = document.createElement('div');
  backdrop.className = 'ambient-background';
  backdrop.setAttribute('aria-hidden', 'true');
  for (const shape of ['cyan', 'blue', 'circle', 'zheng']) {
    const layer = document.createElement('div');
    layer.className = `ambient-shape ambient-${shape}`;
    backdrop.append(layer);
  }
  root.prepend(backdrop);

  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');
  let animations: Animation[] = [];
  let frame = 0;
  let lastFrame = 0;
  let speed = 1;
  let targetSpeed = 1;
  let pointer: { x: number; y: number; time: number } | undefined;

  function cancelFrame(): void {
    cancelAnimationFrame(frame);
    frame = 0;
    lastFrame = 0;
  }

  function tick(now: number): void {
    const elapsed = Math.min((now - (lastFrame || now)) / 1000, 0.1);
    lastFrame = now;
    targetSpeed = 1 + (targetSpeed - 1) * Math.exp(-elapsed / 0.7);
    speed += (targetSpeed - speed) * (1 - Math.exp(-elapsed / 0.3));
    for (const animation of animations) animation.updatePlaybackRate(speed);
    if (Math.abs(speed - 1) < 0.003 && targetSpeed < 1.003) {
      speed = targetSpeed = 1;
      for (const animation of animations) animation.updatePlaybackRate(1);
      frame = 0;
      lastFrame = 0;
      return;
    }
    frame = requestAnimationFrame(tick);
  }

  function move(event: PointerEvent): void {
    if (reduced.matches || !finePointer.matches || document.hidden || event.pointerType !== 'mouse') return;
    const now = performance.now();
    if (pointer) {
      const elapsed = now - pointer.time;
      if (elapsed > 0 && elapsed < 250) {
        const distance = Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y);
        // Accelerate the existing timeline, rather than restarting keyframes.
        targetSpeed = Math.max(targetSpeed, 1 + Math.min(1.5, distance / elapsed * 0.7));
        if (!frame) frame = requestAnimationFrame(tick);
      }
    }
    pointer = { x: event.clientX, y: event.clientY, time: now };
  }

  function leave(): void {
    pointer = undefined;
    targetSpeed = 1;
  }

  function synchronize(): void {
    cancelFrame();
    leave();
    speed = 1;
    // Re-read after preference changes: CSS removes animations in reduced mode.
    animations = backdrop.getAnimations({ subtree: true });
    for (const animation of animations) {
      animation.updatePlaybackRate(1);
      if (document.hidden || reduced.matches) animation.pause();
      else animation.play();
    }
  }

  root.addEventListener('pointermove', move, { passive: true });
  root.addEventListener('pointerleave', leave);
  window.addEventListener('blur', leave);
  document.addEventListener('visibilitychange', synchronize);
  reduced.addEventListener('change', synchronize);
  finePointer.addEventListener('change', synchronize);
  synchronize();

  return () => {
    cancelFrame();
    root.removeEventListener('pointermove', move);
    root.removeEventListener('pointerleave', leave);
    window.removeEventListener('blur', leave);
    document.removeEventListener('visibilitychange', synchronize);
    reduced.removeEventListener('change', synchronize);
    finePointer.removeEventListener('change', synchronize);
    for (const animation of animations) animation.cancel();
    backdrop.remove();
  };
}
