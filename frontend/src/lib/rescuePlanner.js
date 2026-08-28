const DIRECTIONS = [
  [-1, 0, 1], [1, 0, 1], [0, -1, 1], [0, 1, 1],
  [-1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [1, 1, Math.SQRT2],
];

class MinHeap {
  constructor() { this.items = []; }
  push(item) {
    const items = this.items;
    items.push(item);
    let index = items.length - 1;
    while (index > 0) {
      const parent = (index - 1) >> 1;
      if (items[parent].cost <= item.cost) break;
      items[index] = items[parent];
      index = parent;
    }
    items[index] = item;
  }
  pop() {
    const items = this.items;
    if (!items.length) return null;
    const root = items[0];
    const tail = items.pop();
    if (items.length) {
      let index = 0;
      while (true) {
        const left = index * 2 + 1;
        const right = left + 1;
        if (left >= items.length) break;
        let child = left;
        if (right < items.length && items[right].cost < items[left].cost) child = right;
        if (items[child].cost >= tail.cost) break;
        items[index] = items[child];
        index = child;
      }
      items[index] = tail;
    }
    return root;
  }
}

export function planRescueRoutes({ start, targets, obstacles, centerZ = -85, radius = 132, cellSize = 4 }) {
  const minX = -radius;
  const minZ = centerZ - radius;
  const width = Math.floor((radius * 2) / cellSize) + 1;
  const count = width * width;
  const blocked = new Uint8Array(count);
  const risk = new Float32Array(count);
  const clearance = { rock1: 5.5, rock2: 6.5, tree: 4.5 };
  const indexOf = (gx, gz) => gz * width + gx;
  const worldOf = (gx, gz) => ({ x: minX + gx * cellSize, z: minZ + gz * cellSize });
  const gridOf = ({ x, z }) => ({
    gx: Math.max(0, Math.min(width - 1, Math.round((x - minX) / cellSize))),
    gz: Math.max(0, Math.min(width - 1, Math.round((z - minZ) / cellSize))),
  });

  for (let gz = 0; gz < width; gz += 1) {
    for (let gx = 0; gx < width; gx += 1) {
      const index = indexOf(gx, gz);
      const point = worldOf(gx, gz);
      if (Math.hypot(point.x, point.z - centerZ) > radius - cellSize) { blocked[index] = 1; continue; }
      for (const obstacle of obstacles) {
        const safeRadius = clearance[obstacle.type] ?? 5;
        const distance = Math.hypot(point.x - obstacle.x, point.z - obstacle.z);
        if (distance <= safeRadius) { blocked[index] = 1; break; }
        if (distance < safeRadius + 10) risk[index] += (safeRadius + 10 - distance) / 5;
      }
    }
  }

  const startGrid = gridOf(start);
  const startIndex = indexOf(startGrid.gx, startGrid.gz);
  blocked[startIndex] = 0;
  const distance = new Float64Array(count).fill(Infinity);
  const previous = new Int32Array(count).fill(-1);
  const heap = new MinHeap();
  distance[startIndex] = 0;
  heap.push({ index: startIndex, cost: 0 });

  while (heap.items.length) {
    const current = heap.pop();
    if (current.cost !== distance[current.index]) continue;
    const gx = current.index % width;
    const gz = Math.floor(current.index / width);
    for (const [dx, dz, length] of DIRECTIONS) {
      const nx = gx + dx, nz = gz + dz;
      if (nx < 0 || nz < 0 || nx >= width || nz >= width) continue;
      const next = indexOf(nx, nz);
      if (blocked[next]) continue;
      // Prevent diagonal corner cutting through two blocked orthogonal cells.
      if (dx && dz && (blocked[indexOf(gx + dx, gz)] || blocked[indexOf(gx, gz + dz)])) continue;
      const edgeCost = length * cellSize * (1 + (risk[current.index] + risk[next]) * 0.5);
      const candidate = current.cost + edgeCost;
      if (candidate >= distance[next]) continue;
      distance[next] = candidate;
      previous[next] = current.index;
      heap.push({ index: next, cost: candidate });
    }
  }

  const lineIsClear = (a, b) => {
    const length = Math.hypot(b.x - a.x, b.z - a.z);
    const steps = Math.max(1, Math.ceil(length / (cellSize * 0.45)));
    for (let i = 0; i <= steps; i += 1) {
      const point = { x: a.x + (b.x - a.x) * (i / steps), z: a.z + (b.z - a.z) * (i / steps) };
      const grid = gridOf(point);
      const index = indexOf(grid.gx, grid.gz);
      if (blocked[index] || risk[index] > 0.05) return false;
    }
    return true;
  };

  return targets.map((target) => {
    const targetGrid = gridOf(target);
    let cursor = indexOf(targetGrid.gx, targetGrid.gz);
    if (!Number.isFinite(distance[cursor])) return { ...target, reachable: false, path: [] };
    const raw = [];
    while (cursor !== -1) {
      raw.push(worldOf(cursor % width, Math.floor(cursor / width)));
      if (cursor === startIndex) break;
      cursor = previous[cursor];
    }
    raw.reverse();
    const path = raw.length ? [raw[0]] : [];
    let anchor = 0;
    while (anchor < raw.length - 1) {
      let next = raw.length - 1;
      while (next > anchor + 1 && !lineIsClear(raw[anchor], raw[next])) next -= 1;
      path.push(raw[next]);
      anchor = next;
    }
    return { ...target, reachable: true, cost: distance[indexOf(targetGrid.gx, targetGrid.gz)], path };
  });
}
