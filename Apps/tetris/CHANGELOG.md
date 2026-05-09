# Changelog

## [Unreleased] - 2025-12-30

### Added
- **Visual Effects**:
  - `ExplosionParticle` class for particle effects when clearing lines.
  - Screen shake animation (`.shake` CSS class) triggered on hard drops.
- **Gameplay Feel**:
  - "Juice" added to interactions (explosions, shakes) to improve user feedback.
  - Integrated effect rendering system into the main game loop.

### Modified
- `Apps/tetris/index.html`:
  - Added CSS keyframes for `@keyframes shake`.
  - Updated `Tetris` class to manage `this.effects` array.
  - Updated `hardDrop()` to trigger shake.
  - Updated `clearLines()` to spawn explosion particles.
