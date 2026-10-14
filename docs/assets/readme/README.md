# README Studio media

Captured on 14 October 2026 from the production frontend at commit
`d5e361b6cd2775b219e332189c43833d3f4570ce`, using Node 22 and Playwright Chromium.
The captures show the real `/studio` route and Nova components. Browser API
fixtures supply deterministic assistant responses, a demo identity, demo skills,
and simulated planning figures. During the recording, API fixtures store
UI state; the recording demonstrates the frontend interactions.

The welcome and chat PNGs use an 1180 × 1160 viewport. Capabilities and skill
upload use 1180 × 900. The silent recording plays at normal speed: GIF at 10 fps,
960 px wide; MP4 with H.264, `yuv420p`, and fast-start metadata at the original
resolution. Encoding removes the first 0.4 seconds of browser startup.

## Verification and Delivery Gate

The capture refresh leaves frontend source and styles unchanged. These checks
cover the updated documentation and captured media.

- R-02 PASS: the new copy uses direct sentences, with no em dashes or decorative quotation marks.
- R-15 PASS: the recording link names its format and action: “Watch the MP4 recording”.
- R-16 PASS: the copy names the calculator, chart filter, reload, and review behavior shown in the recording.
- R-17, R-18, R-36, R-38 PASS: the README and planner label the data as simulated; the captures contain no customer, performance, or compliance claims.
- R-23 PASS: the user requested replacement Studio media; screenshots and video come from the rendered application.
- R-24 PASS: README media references resolve to files in this directory.
- R-26, R-35 PASS: the recorded team size edit updates $28,000 to $42,000; the Events filter persists after reload; the review action sends the edited state exactly once. Capabilities loads and the upload dialog opens and closes. The production build and capture finish with no warnings or browser errors.
- R-37 PASS: the copy follows the existing README's concrete product descriptions and the captures retain the Nova design system.
- Copy checklist PASS: factual, specific wording; no buzzword padding, all-caps emphasis, invented testimonials, or generic calls to action. Sentence flow and captions were reviewed against the displayed content.
