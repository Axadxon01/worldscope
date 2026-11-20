/**
 * A globe drawn only as its graticule (meridians and parallels every 20°), in
 * orthographic projection as seen from above Central Asia at 30°N. Only the near side
 * is drawn. Purely decorative; it sits behind the sign-in pages.
 */
const R = 300;
const VIEW_LAT = (30 * Math.PI) / 180;
const VIEW_LON = (65 * Math.PI) / 180;
const STEP = 3;

function project(latDeg: number, lonDeg: number): { x: number; y: number; visible: boolean } {
  const lat = (latDeg * Math.PI) / 180;
  const lon = (lonDeg * Math.PI) / 180 - VIEW_LON;
  const cosLat = Math.cos(lat);
  return {
    x: R + R * cosLat * Math.sin(lon),
    y:
      R -
      R * (Math.sin(lat) * Math.cos(VIEW_LAT) - cosLat * Math.cos(lon) * Math.sin(VIEW_LAT)),
    visible:
      Math.sin(lat) * Math.sin(VIEW_LAT) + cosLat * Math.cos(lon) * Math.cos(VIEW_LAT) >= 0,
  };
}

/** An SVG path through the visible points, lifting the pen across the far side. */
function trace(points: { x: number; y: number; visible: boolean }[]): string {
  let d = "";
  let drawing = false;
  for (const point of points) {
    if (!point.visible) {
      drawing = false;
      continue;
    }
    d += `${drawing ? "L" : "M"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
    drawing = true;
  }
  return d;
}

function range(from: number, to: number, step: number): number[] {
  const out = [];
  for (let value = from; value <= to; value += step) out.push(value);
  return out;
}

const MERIDIANS = range(-180, 160, 20).map((lon) =>
  trace(range(-90, 90, STEP).map((lat) => project(lat, lon))),
);
const PARALLELS = range(-80, 80, 20).map((lat) =>
  trace(range(-180, 180, STEP).map((lon) => project(lat, lon))),
);

export function GlobeLines({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox={`-2 -2 ${2 * R + 4} ${2 * R + 4}`}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1}
      strokeLinejoin="round"
    >
      <circle cx={R} cy={R} r={R} />
      {[...MERIDIANS, ...PARALLELS].map((d, index) => (
        <path key={index} d={d} />
      ))}
    </svg>
  );
}
