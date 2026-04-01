# Playbook: X (Twitter) Publication

## Protocol §35.3

1. **Authentication**: Use the `x` browser profile.
2. **Navigation**: Navigate to `https://x.com/compose/post`.
3. **Draft Injection**:
   - Inject text from `drafts/x.md`.
   - Ensure the character count is within X limits (automated check in `drafting.py`).
4. **Media Attachment**:
   - Use the media gallery icon.
   - Upload `media/social_card.png`.
5. **Quality Check**: Ensure no broken links or truncated text.
6. **Submission**: Click "Post".
7. **Verification**: Confirm the tweet ID is captured in the response logs.

## Fallback Mode
Manual tweeting via the verified browser session if automation is rate-limited.
