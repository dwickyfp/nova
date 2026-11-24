/** A polyline (Google encoding) as [longitude, latitude] positions; five decimals by default. */
export function decodePolyline(encoded: string, precision = 5): [number, number][] {
  const factor = 10 ** precision;
  const positions: [number, number][] = [];
  let index = 0;
  let lat = 0;
  let lon = 0;
  while (index < encoded.length) {
    const deltas: number[] = [];
    for (let axis = 0; axis < 2; axis += 1) {
      let shift = 0;
      let result = 0;
      let byte: number;
      do {
        if (index >= encoded.length) return positions;
        byte = encoded.charCodeAt(index) - 63;
        index += 1;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      deltas.push(result & 1 ? ~(result >> 1) : result >> 1);
    }
    lat += deltas[0];
    lon += deltas[1];
    positions.push([lon / factor, lat / factor]);
  }
  return positions;
}
