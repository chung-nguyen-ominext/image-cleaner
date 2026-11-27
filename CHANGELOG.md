# Changelog

All notable changes to ImageCleaner are documented here.

## 0.2.3 – 2026-11-27

- Fixed HiDPI/macOS chevron alignment in combo boxes and spin boxes.
- Aligned the Advanced and region-card padding fields with the other inputs.
- Added left inset for all spin-box values (Advanced and region-card padding), where QSS
  padding did not reach the internal line edit.

## 0.2.2 – 2026-11-27

- The GUI now saves only images to the output folder; `report.csv` / `report.json` are written
  by CLI batch runs only.
- Lossless saving improvements: unchanged images are copied byte-for-byte, indexed PNGs keep
  their palette when possible, PNG uses maximum compression, and `.webp` output is lossless.
  The log warns when a saved file is larger than its source.
- Added a **Clear all** button to the image list and made saving run without switching the
  rest of the window into the busy/cancel UI. Save buttons show a spinner and stay disabled
  while a save is running.

## 0.2.1 – 2026-11-27

- macOS keyboard shortcuts now use the native menu bar and respond to `⌘O`, `⌘Return`, `⌘S`,
  `⌘B`, `⌘0` and `⌘1`; the physical Control key still works.
- GitHub Actions workflows updated for Node 24.
- Linux onedir bundle verified with `SELFTEST OK`; 134 tests pass.

## 0.2.0 – 2026-11-27

- Redesigned UI with a responsive layout, collapsible side panels, Original/Result comparison,
  a quick zoom bar and clearer region controls.
- Advanced options moved into a dedicated dialog with clearer validation and accessible
  icon-only buttons.

## 0.1.0 – 2026-11-26

- First cross-platform release: Windows installer and portable build, macOS `.app`/`.dmg` for
  Apple Silicon and Intel, Linux `.deb` and portable tarball.
- Offline OCR and inpainting using RapidOCR with LaMa, Telea and NS engines.
- Batch CLI with JSON/CSV reports, safe output naming and uncertain-region skipping.
- Fixed the macOS app being packaged as a background-only application.
