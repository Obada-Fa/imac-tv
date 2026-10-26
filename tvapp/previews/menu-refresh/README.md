# Menu refresh previews

GTK3 renders on a private Xvfb display at 720p and 1080p.
Screens are shown without the shell navigation header.
The movies preview uses fictional catalog entries and existing preview artwork.
Loading shows sample playback status, not a live download.

Validation: 438 unit tests pass, including both appearance switches, persistence,
phone settings, scaled CSS, reduced animation scheduling, and remote navigation.
Physical-TV performance and live streaming have not been measured for this change.
