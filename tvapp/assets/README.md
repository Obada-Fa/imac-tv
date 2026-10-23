# TV Home assets

`preview-*.png` are generated placeholder artwork for the launcher preview only.
Live recommendation cards should use image paths supplied by the local feed adapter.

The app marks are the original upstream app icons:
- Stremio: https://github.com/Stremio/stremio-shell/blob/master/images/stremio.svg
- VacuumTube: https://github.com/shy1132/VacuumTube/blob/main/assets/icon.svg
- VLC: loaded from the installed desktop icon theme (`vlc` / `org.videolan.VLC`) so the installed app's own icon is used.

`shapes.json` is a retained collection of legacy doodle drawings; ambient Home no longer uses it.
They are drawings from Google's Quick, Draw! dataset, https://github.com/googlecreativelab/quickdraw-dataset
(Creative Commons Attribution 4.0, https://creativecommons.org/licenses/by/4.0/), reduced to a few dozen points
each by `scripts/build-shapes.py`. Please keep this attribution if you redistribute the file.
