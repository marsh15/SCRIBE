/**
 * PDF points -> viewport pixels (T23).
 *
 * Stored boxes are pdfplumber's: points, origin TOP-LEFT, `[x0, top, x1, bottom]`, in the page's upright frame of
 * `pageWidth` x `pageHeight` points. The viewer draws a page at `scale` pixels per point, optionally turned by
 * `rotation` degrees clockwise (the viewport rotation relative to the frame the box was measured in). The result is a
 * CSS rect (`left`, `top`, `width`, `height`) inside the rendered page.
 */
export type Bbox = readonly [number, number, number, number];
export type ViewportRect = { left: number; top: number; width: number; height: number };
export type PageGeometry = { pageWidth: number; pageHeight: number; scale: number; rotation?: number };

export function normalizeRotation(rotation: number | undefined): 0 | 90 | 180 | 270 {
  const r = (((Math.round((rotation ?? 0) / 90) * 90) % 360) + 360) % 360;
  return r as 0 | 90 | 180 | 270;
}

export function isValidBbox(bbox: readonly number[] | null | undefined): bbox is Bbox {
  return (
    !!bbox &&
    bbox.length === 4 &&
    bbox.every((n) => Number.isFinite(n)) &&
    bbox[2] > bbox[0] &&
    bbox[3] > bbox[1]
  );
}

export function bboxToViewportRect(bbox: Bbox, geometry: PageGeometry): ViewportRect | null {
  const { pageWidth: W, pageHeight: H, scale } = geometry;
  if (!isValidBbox(bbox) || !(W > 0) || !(H > 0) || !(scale > 0)) return null;
  const [x0, y0, x1, y1] = bbox;
  let l: number, t: number, r: number, b: number;
  switch (normalizeRotation(geometry.rotation)) {
    case 90: // clockwise: (x, y) -> (H - y, x)
      [l, t, r, b] = [H - y1, x0, H - y0, x1];
      break;
    case 180:
      [l, t, r, b] = [W - x1, H - y1, W - x0, H - y0];
      break;
    case 270: // (x, y) -> (y, W - x)
      [l, t, r, b] = [y0, W - x1, y1, W - x0];
      break;
    default:
      [l, t, r, b] = [x0, y0, x1, y1];
  }
  return { left: l * scale, top: t * scale, width: (r - l) * scale, height: (b - t) * scale };
}

/** Union of boxes (same frame); null when none is valid. */
export function unionBbox(boxes: ReadonlyArray<readonly number[] | null | undefined>): Bbox | null {
  const valid = boxes.filter(isValidBbox);
  if (!valid.length) return null;
  return [
    Math.min(...valid.map((b) => b[0])),
    Math.min(...valid.map((b) => b[1])),
    Math.max(...valid.map((b) => b[2])),
    Math.max(...valid.map((b) => b[3])),
  ];
}
