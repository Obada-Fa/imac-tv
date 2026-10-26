"""Shared constellation palette for the TV shell and its content screens."""


def theme_css(scale=1.0, preferences=None):
    p = lambda n: max(1, round(n * scale))
    cfg = preferences or {}
    css = f"""
.page, .set-page {{ background-color: #0b121c; }}
.set-page {{ background-image: linear-gradient(135deg, #172a3b, #0b121c 65%); }}
.header-bar {{ background-image: linear-gradient(180deg, #0b121c, rgba(11,18,28,0)); }}
.brand, .hero-kicker, .set-section {{ color: #a8d7eb; }}
.nav-button {{ color: #9eafc1; }}
.nav-button:focus, .nav-current {{ color: #e5f6ff; border-bottom-color: #a8d7eb; }}
/* Settings are drawn by widgets/glass.py (Home's glass); CSS only sets their type. */
.set-title {{ font-size: {p(54)}px; font-weight: 200; color: #fff; }}
.set-output {{ font-size: {p(58)}px; font-weight: 200; color: #fff; }}
.set-note {{ font-size: {p(20)}px; color: rgba(255,255,255,0.6); }}
.src-row {{ background: rgba(28,44,60,0.88); border: {p(2)}px solid #34495c;
            border-radius: {p(18)}px; padding: {p(16)}px {p(24)}px; }}
.src-row:focus, .card:focus {{ background: #294459; border-color: #c0e8f8;
    box-shadow: 0 0 0 {p(3)}px rgba(168,215,235,0.22); }}
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
.rail-title {{ font-size: {p(28)}px; font-weight: 600; color: #d8e8f3; }}
.card {{ background: #152333; border-color: #25394b; border-radius: {p(18)}px; }}
.card:hover {{ background: #21374a; }}
.card-title {{ color: #d8e8f3; font-weight: 600; }}
.card-sub, .hero-info, .det-info {{ color: #a8c6db; }}
.hero-title, .det-title {{ font-weight: 600; }}
.hero-fade {{ background-image: linear-gradient(90deg, #0b121c, rgba(11,18,28,0.86) 32%, rgba(11,18,28,0.08)); }}
.hero-bottom-fade {{ background-image: linear-gradient(0deg, #0b121c, rgba(11,18,28,0)); }}
.hero-primary {{ background: #c0e8f8; border-color: #c0e8f8; color: #102536; }}
.hero-secondary, .pill {{ background: #203549; border-color: #57788d; color: #e5f6ff; }}
.search-entry {{ background: #172a3b; border-color: #425e74; }}
.buffer-overlay {{ background: rgba(7,14,23,0.96); }}
.buffer-card {{ background: transparent; border: none; padding: 0; box-shadow: none; }}
.buffer-mark {{ font-size: {p(18)}px; letter-spacing: 3px; font-weight: 500; color: #8faabc; }}
.buffer-title {{ font-size: {p(62)}px; font-weight: 500; color: #f0f6fa; }}
.buffer-line {{ font-size: {p(20)}px; color: #8fa5b7; }}
.buffer-status {{ font-size: {p(24)}px; font-weight: 400; color: #c3d9e8; }}
.buffer-overlay spinner {{ color: #a8d7eb; min-width: {p(26)}px; min-height: {p(26)}px; -gtk-icon-transform: none; }}

"""
    if not cfg.get('transparency', True):
        css += """
.header-bar, .buffer-overlay, .set-page, .ah-scrim { background: #0b121c; }
.src-row, .app-button,
.hero-secondary, .toast, .qr-dialog, .ah-sheet, .ah-dbtn, .ah-cardbtn { background: #1c2c3c; }
.hero-fade { background-image: linear-gradient(90deg, #0b121c 0%, #0b121c 44%, transparent 80%); }
"""
    if not cfg.get('visual_effects', True):
        css += """
* { transition: none; box-shadow: none; }
.set-page { background-image: none; }
.buffer-card, .card, .hero-primary, .hero-secondary, .src-row,
.card:focus, .set-btn:focus, .src-row:focus, .hero-primary:focus,
.hero-secondary:focus, .search-entry:focus, .conn-pill:focus, .conn-btn:focus,
.app-button:focus, .feed-card:focus { box-shadow: none; transition: none; }
.skel-b { background: #181a20; }
"""
    return css
