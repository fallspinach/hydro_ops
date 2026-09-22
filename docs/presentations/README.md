# Forcing product user presentation

`forcing_products/hydro_ops_forcing_user_guide.pptx` is the editable, 20-slide
user-facing briefing, dated 2026-09-19. It covers purpose, source characteristics,
required GFS northern fallback, source selection, PRISM constraints, separate
NRT/Retro streams, schedule limitations, file/time conventions and access.

Companions in the same directory:

- `hydro_ops_forcing_user_guide_preview.pdf`: same-layout visual preview.
- `contact_sheet.png` and `slide_01.png` through `slide_20.png`: review images.
- `speaker_notes.md`: methods, caveats and provider URLs, also included in PPTX notes.

Rebuild with Python, `python-pptx` and `Pillow`:

```bash
python docs/presentations/build_forcing_deck.py
```

The builder currently uses DejaVu Sans fonts under the local Miniforge installation;
adjust `FONT` and `BOLD` for another machine. Output text and diagrams are editable
PowerPoint objects, not slide screenshots. The PDF/PNG previews are drawn from the
same layout commands; they are not a LibreOffice/PowerPoint rendering. Verification
checks explicit line wrapping, text-box capacity, slide bounds, note presence and
PPTX ZIP integrity. Check presentation-app font substitution before presenting.

Provider resolution/latency details were checked against official NOAA/NASA/PRISM
pages. Latencies are approximate and version-dependent. Project schedules reflect
the repository template, not an installed cron service. The snapshot makes no
claim that the backfill is complete or that current-hour delivery is operational.
