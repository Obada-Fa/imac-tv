"""Shared constellation palette for the TV shell and its content screens."""


def theme_css(scale=1.0, preferences=None):
    p = lambda n: max(1, round(n * scale))
    cfg = preferences or {}
    css = f"""
.page, .set-page {{ background-color: #0b121c; }}
.set-page {{ background-image: linear-gradient(135deg, #172a3b, #0b121c 65%); }}
.header-bar {{ background-image: linear-gradient(180deg, #0b121c, rgba(11,18,28,0)); }}
.set-section {{ color: #a8d7eb; }}
/* Glass pages (Movies, Series, Search, Details, Sources) paint Home's backdrop themselves (widgets/chrome.py). */
.glass-page {{ background: none; }}
/* Settings are drawn by widgets/glass.py (Home's glass); CSS only sets their type. */
.set-title {{ font-size: {p(54)}px; font-weight: 200; color: #fff; }}
.set-output {{ font-size: {p(58)}px; font-weight: 200; color: #fff; }}
.set-note {{ font-size: {p(20)}px; color: rgba(255,255,255,0.6); }}
.conn-pill, .conn-btn {{ background: transparent; border: {p(2)}px solid transparent;
                       box-shadow: none; border-radius: {p(12)}px; }}
.conn-pill:focus, .conn-btn:focus {{ background: #233e52; border-color: #c0e8f8; box-shadow: none; }}
.conn-ip {{ color: #d3e4ef; font-size: {p(18)}px; font-weight: 500; }}
.conn-code, .conn-wifi {{ color: #94acbf; font-size: {p(17)}px; font-weight: 400;
                          font-family: inherit; letter-spacing: 0; }}
.conn-wifi-off {{ color: #d7a7a0; }}
.conn-status-icon {{ color: #a8d7eb; }}
.pair-sheet {{ background: #142333; border: none; box-shadow: none; }}
.pair-sheet .ah-dtitle {{ font-size: {p(42)}px; font-weight: 500; }}
.pair-sheet .ah-dcode {{ color: #d9effa; font-size: {p(42)}px; font-weight: 500; }}
.pair-sheet .ah-daddr {{ color: #adc4d5; }}
.pair-sheet .ah-dbtn {{ border: {p(2)}px solid transparent; background: #294459; }}
.pair-sheet .ah-dbtn:focus {{ border-color: #c0e8f8; }}
.card-sub {{ color: rgba(255,255,255,0.62); }}
/* Home's type on the media screens: thin large titles, quiet '·' meta lines, soft shadows for bright art. */
.hero-title {{ font-size: {p(62)}px; font-weight: 300; color: #fff; text-shadow: 0 2px 4px rgba(0,0,0,0.45); }}
.det-title {{ font-size: {p(72)}px; font-weight: 200; color: #fff; text-shadow: 0 2px 4px rgba(0,0,0,0.45); }}
.hero-info, .det-info {{ font-size: {p(21)}px; font-weight: 400; color: rgba(255,255,255,0.72);
                         letter-spacing: {p(1)}px; text-shadow: 0 1px 3px rgba(0,0,0,0.6); }}
.hero-copy, .det-desc {{ font-size: {p(22)}px; font-weight: 400; color: rgba(255,255,255,0.80);
                         text-shadow: 0 1px 3px rgba(0,0,0,0.6); }}
entry.search-entry, entry.search-entry:focus {{ background-color: transparent; background-image: none;
    border: none; box-shadow: none; outline: none; color: #fff; font-size: {p(32)}px; font-weight: 300;
    padding: 0; caret-color: #fff; }}
entry.search-entry selection {{ background-color: rgba(255,255,255,0.22); color: #fff; }}
.section-note {{ color: rgba(255,255,255,0.6); }}
.empty-copy {{ font-size: {p(22)}px; font-weight: 300; color: rgba(255,255,255,0.78); }}
.buffer-overlay {{ background: none; }}

"""
    if not cfg.get('transparency', True):
        css += """
.set-page, .ah-scrim { background: #0b121c; }
.header-bar { background-image: linear-gradient(180deg, #0b121c, rgba(11,18,28,0.92) 55%, rgba(11,18,28,0)); }
.app-button,
.hero-secondary, .toast, .qr-dialog, .ah-sheet, .ah-dbtn, .ah-cardbtn { background: #1c2c3c; }
.hero-fade { background-image: linear-gradient(90deg, #0b121c 0%, #0b121c 44%, transparent 80%); }
"""
    if not cfg.get('visual_effects', True):
        css += """
* { transition: none; box-shadow: none; }
.set-page { background-image: none; }
.card, .card:focus, .set-btn:focus, .search-entry:focus,
.app-button:focus, .feed-card:focus { box-shadow: none; transition: none; }
"""
    return css
