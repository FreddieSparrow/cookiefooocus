progress_html = '''
<div class="loader-container">
  <div class="loader"></div>
  <div class="progress-container">
    <progress value="*number*" max="100"></progress>
  </div>
  <span>*text*</span>
</div>
'''


def make_progress_html(number, text):
    return progress_html.replace('*number*', str(number)).replace('*text*', text)


# ── Branding & legal ───────────────────────────────────────────────────────────

HEADER_HTML = '''
<div style="text-align:center; padding: 8px 0 4px 0;">
  <span style="font-size:1.4em; font-weight:700; letter-spacing:0.03em;">🍪 Cookie-Fooocus</span>
  <span style="font-size:0.85em; color:#888; margin-left:10px;">Provided by CookieHostUK · Coded with Claude AI</span>
</div>
'''

LEGAL_HTML = '''
<details style="margin-top:8px; font-size:0.78em; color:#666;">
  <summary style="cursor:pointer; font-weight:600;">⚖️ Legal Disclaimer (click to expand)</summary>
  <div style="padding:8px 0 0 4px; line-height:1.6;">
    <b>No Warranty.</b> This software is provided "as is", without warranty of any kind, express or
    implied. The authors and CookieHostUK accept no liability for any claim, damages, or other
    liability arising from the use of this software or generated content.<br><br>
    <b>User Responsibility.</b> You are solely responsible for all content generated using this
    tool. Ensure your use complies with all applicable local, national, and international laws,
    including copyright, privacy, and content regulations.<br><br>
    <b>Age Policy.</b> This tool must not be used to generate content depicting minors in any
    sexual or harmful context. The content safety system enforces this automatically, but you
    remain legally responsible for your prompts and outputs.<br><br>
    <b>No Liability for AI Output.</b> Generated images are produced by AI models. The developers
    make no representations about accuracy, appropriateness, or fitness for any particular purpose.<br><br>
    <b>Content Safety.</b> A multi-layer content filter is active at all times. Certain categories
    of content are blocked regardless of user input. This filter cannot be disabled.
  </div>
</details>
'''

TIPS_HTML = '''
<div style="font-size:0.82em; color:#777; margin: 4px 0 8px 0; padding: 6px 10px;
            background: #f8f8f8; border-radius:6px; border-left: 3px solid #ddd;">
  <b>Tips:</b>
  Describe your scene in plain English · Add style words like <em>cinematic, oil painting, 4k</em> ·
  Use <b>Styles</b> for quick looks · Upload an image to upscale, vary, or inpaint ·
  <b>Seed = -1</b> for random results
</div>
'''
