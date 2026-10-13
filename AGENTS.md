# RTV contributor instructions

- This repository supports learning through reproduction, engineering implementation,
  and original exploration. Keep relevant mechanisms even when prior art exists.
  Attribute sources and distinguish reproduction, adaptation, and new proposals.
- Keep README, project documentation, generated reports, and the viewer in English.
  Use canonical `.md` paths without duplicate language versions or language buttons.
- Separate scripted fixtures, mocked tests, real-container tests, and real-model
  experiments in every report. Never promote a capability from proposed to verified
  without evidence. Preserve M0–M4 acceptance criteria when adding foundations.
- The owner has access to four lab H100 GPUs. Access, VRAM, scheduler, and usage rules
  are not yet configured; do not assume credentials or start lab jobs without them.
- Before any new paid API calls, cloud resources, or other incremental charges,
  explain the purpose and expected cost and obtain the owner's confirmation.
  Existing approval covers local development and model-free tests, not paid runs.
- Core RTV supports Python 3.11+. The pinned optional Harbor dependency group requires
  Python 3.12+. Use the lockfile; keep model and cloud integrations opt-in.
- Do not commit credentials, full trajectories, downloaded dependencies, images, or
  large experiment artifacts. Store local run outputs under ignored `work/`.
