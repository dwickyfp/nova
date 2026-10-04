# Nova phoenix assets

Nova uses one brick-red and copper phoenix across Console, Studio, sign-in,
assistant, browser icons, and the root README. The application palette and
typography remain defined by [DESIGN.md](../../../DESIGN.md).

## Source and exports

The master was generated with the built-in imagegen tool and normalized to a
1024 × 1024 transparent PNG. All exports use that same composition and alpha;
none are independently generated or redrawn.

| File | Size | Use |
| --- | --- | --- |
| `nova-phoenix-master.png` | 1024 × 1024 | Master and large login emblem |
| `nova-mark-{64,128,256,512}.png` | Corresponding square size | Shared React logo and README |
| `favicon-{16,32,48}.png` | Corresponding square size | Browser tabs |
| `favicon.ico` | 16, 32, 48 | Browser fallback, PNG entries |
| `apple-touch-icon.png` | 180 × 180 | Touch icon |

Use the shared `Logo` component in application code. Set `sizes` to match its
display size, preserve the square aspect ratio, and keep the existing clear
margin. Use empty alternative text beside an existing brand label; use a
descriptive alternative when the emblem stands alone. Do not recolor, stretch,
crop, add glow, or create a separate phoenix for another screen. Both themes
use the same image. Touch platforms may supply their own background for alpha.

To reproduce exports on macOS, use `sips -z SIZE SIZE
nova-phoenix-master.png --out OUTPUT.png`. Regenerate ICO entries from the
16/32/48 PNGs. Retain the master as the source for future exports.

## Generation prompt

```text
Use case: logo-brand. Asset type: single master brand emblem for Nova analytics software, used unchanged from 16px favicon to large login emblem. Generate one luxurious phoenix mark, no text. Truly transparent background. Square canvas, centered emblem with 10% clear padding. Distinct phoenix bird head in slight profile, raised sweeping wings balanced on both sides, compact strong body, short elegant flame tail. Sculptural clean broad shapes, only three broad feather shapes per wing, generous negative spaces, bold readable silhouette at tiny sizes. Restrained premium flat graphic design with brick red #D04738 dominant and warm copper #D99765 secondary accent. Crisp edges, no thin lines. Subtle copper accent planes, not realistic metal. No surrounding frame, no badge, no circle, no shadows, no glow, no particles, no gradients, no mockup, no watermark, no typography. Original dignified enterprise brand, refined proportions.
```
