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
.set-rail {{ background: rgba(17,31,46,0.72); border: 1px solid #304457;
             border-radius: {p(24)}px; padding: {p(20)}px; }}
.set-rail .set-btn {{ background: transparent; border-color: transparent; min-width: {p(180)}px; }}
.set-rail .set-current {{ background: #263f53; border-color: #57788d; }}
.set-rail .set-btn:focus {{ background: #36546b; border-color: #c0e8f8; }}
.set-title {{ font-size: {p(40)}px; font-weight: 600; }}
.set-output {{ font-size: {p(38)}px; font-weight: 600; }}
.set-note {{ color: #acbdcd; }}
.set-btn, .src-row {{ background: rgba(28,44,60,0.88); border: {p(2)}px solid #34495c;
                     border-radius: {p(18)}px; padding: {p(16)}px {p(24)}px; }}
.set-btn-main {{ font-size: {p(24)}px; font-weight: 600; }}
.set-btn-sub {{ font-size: {p(20)}px; color: #aac9dc; }}
.set-btn:focus, .src-row:focus, .card:focus {{
    background: #294459; border-color: #c0e8f8;
    box-shadow: 0 0 0 {p(3)}px rgba(168,215,235,0.22);
}}
.set-btn:disabled {{ background: #141e2a; border-color: #253442; }}
.set-btn:disabled label {{ color: #718496; }}
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
.buffer-overlay {{ background: rgba(7,14,23,0.94); }}
.buffer-card {{ background: #142536; border: {p(2)}px solid #3a566c;
                border-radius: {p(32)}px; padding: {p(48)}px {p(64)}px;
                box-shadow: 0 {p(18)}px {p(50)}px rgba(0,0,0,0.3); }}
.buffer-mark {{ font-size: {p(20)}px; letter-spacing: 3px; font-weight: 700; color: #a8d7eb; }}
.buffer-title {{ font-size: {p(42)}px; font-weight: 600; }}
.buffer-line {{ font-size: {p(20)}px; color: #aabfcf; }}
.buffer-status {{ font-size: {p(24)}px; color: #d1e8f6; }}
.buffer-overlay spinner {{ color: #a8d7eb; min-width: {p(48)}px; min-height: {p(48)}px; -gtk-icon-transform: none; }}
"""
    if not cfg.get('transparency', True):
        css += """
.header-bar, .buffer-overlay, .set-page, .ah-scrim { background: #0b121c; }
.set-rail, .set-btn, .src-row, .conn-pill, .conn-btn, .app-button,
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
