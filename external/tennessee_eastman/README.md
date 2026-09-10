# Tennessee Eastman external assets

This directory is reserved for local Tennessee Eastman Challenge Process assets.
The codebase treats these files as external data, not as core source code.

Recommended local download set:

- `temexd_mod.zip` from `https://depts.washington.edu/control/LARRY/TE/temexd_mod.zip`
- `tecode.zip` from `https://depts.washington.edu/control/LARRY/TE/tecode.zip`
- `tables.zip` from `https://depts.washington.edu/control/LARRY/TE/tables.zip`
- `IDVs/format.txt` from `https://depts.washington.edu/control/LARRY/TE/IDVs/format.txt`
- `IDVs/idv1.zip` from `https://depts.washington.edu/control/LARRY/TE/IDVs/idv1.zip`

Use `tools/download_tennessee_eastman.py` to recreate the local layout expected
by `TennesseeEastmanTraceBackend.from_asset_root()`.
